import os
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
from starlette.requests import Request
from starlette.responses import JSONResponse
import chromadb
from chromadb.utils import embedding_functions
from embedding import server_lifespan

load_dotenv()

# --- 설정 (ETL 스크립트와 동일한 경로) ---
MODEL_NAME = os.getenv("MODEL_NAME", "intfloat/multilingual-e5-base")
VECTOR_DB_PATH = "./chroma_db_store"
COLLECTION_NAME = "note_collection"
QUERY_PREFIX =  os.getenv("QUERY_PREFIX", "query: ")

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
            n_results=3  # 상위 3개 추출
        )

        return results

    except Exception as e:
        return {"error": f"검색 중 오류 발생: {str(e)}"}

# MCP 서버 생성
mcp = FastMCP("MyNoteSearcher", lifespan=server_lifespan)

@mcp.tool()
def search_notes_tool(query: str) -> str:
    """
    사용자의 질문과 관련된 노트를 데이터베이스에서 검색합니다.
    Args:
        query: 검색할 질문이나 키워드 (예: "파이썬 프로젝트 아이디어")
    Returns:
        검색된 노트 내용들을 문자열로 반환
    """
    results = _search(9, query)
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
    query = request.query_params["query"]
    results = _search(9, query)

    logger.info(f"--- 질문: {query} ---")
    for i in range(len(results['documents'][0])):
        logger.info(f"순위 {i+1}:")
        logger.info(f"메타데이터: {results['metadatas'][0][i]}")
        logger.info(f"거리(유사도 역수): {results['distances'][0][i]}")
        logger.info(f"내용: \n{results['documents'][0][i]}\n")
        logger.info("-" * 20)
    return JSONResponse(results)

