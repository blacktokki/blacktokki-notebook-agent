import os
import asyncio
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastmcp import FastMCP, Context, settings
from fastmcp.utilities.logging import get_logger
from fastmcp.server.dependencies import get_http_request
from starlette.requests import Request
from starlette.responses import JSONResponse
import chromadb
from chromadb.utils import embedding_functions
from py_eureka_client import eureka_client

from env import MODEL_NAME, QUERY_PREFIX
from embedding import COLLECTION_NAME, VECTOR_DB_PATH, delete_pat_jti, embedding, get_pat_jti
from mcp_auth import AuthenticationMiddleware, authenticate, create_pat_token

load_dotenv()
logger = get_logger(__name__)

def _search(user_id: int, query: str) -> str:
    try:
        client = chromadb.PersistentClient(path=VECTOR_DB_PATH)
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=MODEL_NAME
        )
        collection = client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
        
        # 벡터 검색 수행
        results = collection.query(
            query_texts=[f"{QUERY_PREFIX}{query}"],
            where={"user_id": user_id},
            n_results=5  # 상위 5개 추출
        )

        return results

    except Exception as e:
        return {"error": f"검색 중 오류 발생: {str(e)}"}

# MCP 서버 생성
@asynccontextmanager
async def server_lifespan(server:FastMCP):
    # [시작 시 실행]
    logger.info("Background embedding task started.")
    # 백그라운드 작업 시작 (반드시 task 변수에 할당해두어야 GC되지 않음)
    await eureka_client.init_async(
        eureka_server="http://127.0.0.1:8761",
        app_name="agent",
        instance_host=settings.get_setting("host"),
        instance_port=settings.get_setting("port")
    )
    
    task = asyncio.create_task(embedding())
    
    yield  # 여기서 서버가 실행됨 (대기)
    
    # [종료 시 실행]
    logger.info("Stopping background task...")
    await eureka_client.stop_async()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Stop background task completed.")


mcp = FastMCP("MyNoteSearcher", middleware=[AuthenticationMiddleware()], lifespan=server_lifespan)

@mcp.tool()
def search_notes_tool(query: str) -> str:
    """
    사용자의 질문과 관련된 노트를 데이터베이스에서 검색합니다.
    Args:
        query: 검색할 질문이나 키워드 (예: "파이썬 프로젝트 아이디어")
    Returns:
        검색된 노트 내용들을 문자열로 반환
    """
    results = _search(get_http_request().state.user["us_id"], query)
    if results.get("error"):
        return results["error"]
    if not results['documents'][0]:
        return "관련된 내용을 찾을 수 없습니다."
    response = f"검색 결과 ('{query}'):\n\n"
    for i, doc in enumerate(results['documents'][0]):
        meta = results['metadatas'][0][i]
        response += f"[{i+1}] {meta['title']} (원본ID: {meta['original_id']})\n"
        response += f"내용: {doc}\n"
        response += "-" * 30 + "\n"
        
    return response
    

@mcp.custom_route("/search", methods=["GET"])
def search_notes(request: Request):
    response = authenticate(request)
    if response:
        return response
    query = request.query_params["query"]
    us_id = request.state.user["us_id"]
    results = _search(us_id, query)

    logger.info(f"--- 질문: {query} ---")
    for i in range(len(results['documents'][0])):
        logger.info(f"순위 {i+1}:")
        logger.info(f"메타데이터: {results['metadatas'][0][i]}")
        logger.info(f"거리(유사도 역수): {results['distances'][0][i]}")
        logger.info(f"내용: \n{results['documents'][0][i]}\n")
        logger.info("-" * 20)
    return JSONResponse(results)


@mcp.custom_route("/access-token", methods=["GET", "POST"])
def access_token(request: Request):
    response = authenticate(request)
    if response:
        return response
    us_id = request.state.user["us_id"]

    if request.method == "GET":
        result = get_pat_jti(us_id)
    else:
        result = create_pat_token(us_id)
    return JSONResponse(result)


@mcp.custom_route("/access-token/{jti}", methods=["DELETE"])
def access_token(request: Request):
    response = authenticate(request)
    if response:
        return response
    us_id = request.state.user["us_id"]
    delete_pat_jti(us_id, request.path_params["jti"])
    return JSONResponse({})