import asyncio
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastmcp import FastMCP, settings
from fastmcp.utilities.logging import get_logger
from fastmcp.server.dependencies import get_http_request
from starlette.requests import Request
from starlette.responses import JSONResponse
from py_eureka_client import eureka_client

from embedding import embedding, search
from mcp_auth import AuthenticationMiddleware, authenticate
from notebook_client import NotebookClient

load_dotenv()
logger = get_logger(__name__)

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
def search_notes_tool(query: str, page:int = 0) -> str:
    """
    사용자의 질문과 관련된 노트를 데이터베이스에서 검색합니다.
    Args:
        query: 검색할 질문이나 키워드 (예: "파이썬 프로젝트 아이디어")
        page(Optional): 0부터 시작하는 검색 결과 페이지 번호 (default: 0)
    Returns:
        검색된 노트 내용들을 문자열로 반환
    """
    results = search(get_http_request().state.user["us_id"], query, 20, page)
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
    page = int(request.query_params["page"])
    size = int(request.query_params["size"])
    us_id = request.state.user["us_id"]
    results = search(us_id, query, size, page)
    if results.get("error"):
        raise Exception(results["error"])
    formatted_results = [
    {
        "id": id,
        "distance": dist,
        "metadata": meta,
        "document": doc
    }
    for id, dist, meta, doc in zip(
        results['ids'][0], 
        results['distances'][0], 
        results['metadatas'][0], 
        results['documents'][0]
    )
]

    logger.info(f"--- 질문: {query} ---")
    for i, item in enumerate(formatted_results):
        logger.info(f"순위 {i+1}:")
        logger.info(f"메타데이터: {item['metadata']}")
        logger.info(f"거리(유사도 역수): {item['distance']}")
        logger.info(f"내용: \n{item['document']}\n")
        logger.info("-" * 20)
    return JSONResponse(formatted_results)

@mcp.tool()
def write_note(title: str, content_html: str) -> str:
    """
    1. 노트 쓰기 (제목 수정 금지)
    - 해당 제목의 노트가 이미 존재하면: 내용을 수정(덮어쓰기)합니다. 제목은 변경되지 않습니다.
    - 해당 제목의 노트가 없으면: 새 노트를 생성합니다.
    """
    client = NotebookClient()
    try:
        existing_note = client.get_note_by_title(title)
        
        if existing_note:
            # Update Content Only
            client.update_note_content(existing_note["id"], existing_note, content_html)
            return f"Note '{title}' (ID: {existing_note['id']}) content updated."
        else:
            # Create New
            new_id = client.create_note(title, content_html)
            return f"Note '{title}' (ID: {new_id}) created."
    except Exception as e:
        return f"Error writing note: {str(e)}"

@mcp.tool()
def search_notes(keyword: str = None) -> str:
    """
    2. 다건 노트 조회
    키워드 검색을 지원하며 본문 미리보기를 제공합니다.
    """
    client = NotebookClient()
    notes = client.fetch_contents(["NOTE"])
    results = []
    
    for note in notes:
        title = note.get("title", "")
        if keyword and keyword.lower() not in title.lower():
            continue
            
        desc = note.get("description", "")
        preview = client._clean_html_tags(desc)[:100].replace("\n", " ")
        results.append(f"ID: {note['id']} | Title: {title} | Preview: {preview}...")
        
    return "\n".join(results) if results else "No notes found."

@mcp.tool()
def get_archives(note_title: str) -> str:
    """
    3. 아카이브 조회 (SNAPSHOT, DELTA)
    """
    client = NotebookClient()
    parent_note = client.get_note_by_title(note_title)
    if not parent_note:
        return f"Error: Note '{note_title}' not found."
    
    archives = client.fetch_contents(["SNAPSHOT", "DELTA"], parent_id=parent_note["id"])
    results = []
    for arc in archives:
        updated = arc.get("updated", "Unknown")
        type_ = arc.get("type")
        results.append(f"[{updated}] {type_} (ID: {arc['id']})")
        
    return "\n".join(results) if results else "No archives found."

@mcp.tool()
def get_kanban_boards() -> str:
    """
    4. 칸반 보드 조회
    """
    client = NotebookClient()
    boards = client.fetch_contents(["BOARD"])
    results = []
    for board in boards:
        option = board.get("option", {})
        note_ids = option.get("BOARD_NOTE_IDS", [])
        header_level = option.get("BOARD_HEADER_LEVEL", "Unknown")
        results.append(f"ID: {board['id']} | Title: {board['title']} | Columns: {note_ids} (H{header_level})")
    return "\n".join(results) if results else "No boards found."

@mcp.tool()
def move_kanban_card(source_note_title: str, target_note_title: str, card_header_text: str) -> str:
    """
    5. 칸반 카드 이동
    """
    client = NotebookClient()
    try:
        return client.move_kanban_card_logic(source_note_title, target_note_title, card_header_text)
    except Exception as e:
        return f"Error moving card: {str(e)}"

@mcp.tool()
def move_note(old_title: str, new_title: str) -> str:
    """
    6. 노트 이동 (이름 변경)
    노트의 제목을 변경합니다. (예: 'Folder/OldName' -> 'Folder/NewName')
    경로(폴더)를 변경하는 것과 동일한 효과를 가집니다.
    """
    client = NotebookClient()
    try:
        # 1. 대상 노트 확인 (덮어쓰기 방지)
        if client.get_note_by_title(new_title):
            return f"Error: A note with the title '{new_title}' already exists."

        # 2. 원본 노트 확인
        note = client.get_note_by_title(old_title)
        if not note:
            return f"Error: Note '{old_title}' not found."

        # 3. 이름 변경 요청
        client.rename_note(note["id"], note, new_title)
        return f"Successfully moved/renamed note from '{old_title}' to '{new_title}'."
    except Exception as e:
        return f"Error moving note: {str(e)}"