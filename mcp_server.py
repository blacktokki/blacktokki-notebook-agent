import asyncio
import json
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastmcp import FastMCP, settings
from fastmcp.utilities.logging import get_logger
from fastmcp.server.dependencies import get_http_request
from starlette.requests import Request
from starlette.responses import JSONResponse
from py_eureka_client import eureka_client
from diff_match_patch import diff_match_patch

from embedding import embedding, search, to_html, to_markdown
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

def _search_notes(query: str, exact: bool, size: int, page: int, with_hidden: bool, with_external: bool):
    return search(get_http_request().state.user["us_id"], query, exact, size, page, with_hidden, with_external)

@mcp.tool()
def search_notes_tool(query: str, page: int = 0, withHidden: bool = False) -> str:
    """
    1. 사용자의 질문과 관련된 노트를 데이터베이스에서 검색합니다.
    Args:
        query: 검색할 질문이나 키워드 (예: "파이썬 프로젝트 아이디어")
        page(Optional): 0부터 시작하는 검색 결과 페이지 번호 (default: 0)
        withHidden(Optional, Boolean): 숨김 노트 포함 여부 (default: false)
    """
    results = _search_notes(query, False, 20, page, withHidden, True)
    
    if results.get("error"):
        return json.dumps({"error": results["error"]}, ensure_ascii=False)
    
    if not results.get('documents') or not results['documents'][0]:
        return json.dumps({"query": query, "results": [], "message": "관련된 내용을 찾을 수 없습니다."}, ensure_ascii=False)

    search_results = []
    for i, doc in enumerate(results['documents'][0]):
        meta = results['metadatas'][0][i]
        search_results.append({
            "index": i + 1,
            "title": meta['title'],
            "original_id": meta['original_id'],
            "content": doc
        })
        
    return json.dumps({
        "query": query,
        "results": search_results
    }, ensure_ascii=False)

@mcp.custom_route("/search", methods=["GET"])
def search_notes(request: Request):
    response = authenticate(request)
    if response:
        return response
    query = request.query_params["query"]
    page = int(request.query_params["page"])
    size = int(request.query_params["size"])
    exact = request.query_params.get("exact") == "true"
    with_hidden = request.query_params.get("withHidden") == "true"
    with_external = request.query_params.get("withExternal") == "true"
    results = _search_notes(query, exact, size, page, with_hidden, with_external)
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
def search_notes(title: str = None, withHidden: bool = False) -> str:
    """
    2. 다건 노트 조회
    노트 제목을 포함하는 검색을 지원하며 본문 내용을 markdown으로 제공합니다.
    """
    client = NotebookClient()
    notes = client.fetch_contents(["NOTE"], withHidden)
    results = []
    title_param = note.get("title", "").lower()
    
    for note in notes:
        if title and title.lower() not in title_param:
            continue
            
        desc = note.get("description", "")
        preview = to_markdown(desc or "")
        results.append({
            "id": note['id'],
            "title": str(title),
            "preview": preview
        })
    return json.dumps({"count": len(results), "notes": results}, ensure_ascii=False)

@mcp.tool()
def get_note_snapshots(note_id: int, page: int = 0) -> str:
    """
    특정 노트의 수정 이력(SNAPSHOT 및 DELTA)을 조회합니다.
    DELTA 타입은 기준 SNAPSHOT과 결합하여 전체 내용을 복원한 후 마크다운으로 반환합니다.

    Args:
        note_id: 이력을 조회할 원본 노트의 ID (parentId)
        page: 조회할 페이지 번호 (기본값: 0)
    """
    client = NotebookClient()
    dmp = diff_match_patch()
    
    # 1. SNAPSHOT 및 DELTA 타입만 조회
    contents = client.fetch_contents(["SNAPSHOT", "DELTA"], True, note_id, page)
    snapshots = client.fetch_contents(["SNAPSHOT"], True, note_id, 0)
    
    if not contents:
        return json.dumps({"message": "수정 이력이 없습니다.", "snapshots": []}, ensure_ascii=False)

    # 2. 현재 페이지 내 스냅샷 매핑 구축
    snapshot_map = {item['id']: item for item in snapshots}
    
    results = []
    for item in contents:
        content_type = item.get('type')
        description_html = item.get('description', '')
        
        # 3. DELTA 타입의 내용 복원 처리
        if content_type == 'DELTA':
            snapshot_id = item.get('option', {}).get('SNAPSHOT_ID')
            if snapshot_id:
                base_snapshot = snapshot_map.get(snapshot_id)
                if base_snapshot:
                    try:
                        # diff-match-patch를 사용하여 원래 내용 확인
                        diffs = dmp.diff_fromDelta(base_snapshot['description'], description_html)
                        description_html = dmp.diff_text2(diffs)
                    except Exception as e:
                        logger.error(f"Delta restoration failed: {str(e)}")
                        continue
                else:
                    logger.error(f"Snapshot {snapshot_id} not found for DELTA {item.get('id')}")
                    continue
        
        # 4. SNAPSHOT 및 복원된 DELTA 모두 to_markdown 처리
        markdown_content = to_markdown(description_html or "")
        
        results.append({
            "id": item.get('id'),
            "type": content_type,
            "updated": item.get('updated'),
            "content": markdown_content,
        })
        
    return json.dumps({
        "note_id": note_id,
        "page": page,
        "snapshots": results
    }, ensure_ascii=False)

@mcp.tool()
def write_note(title: str, content_markdown: str) -> str:
    """
    4. 노트 쓰기 (제목 수정 금지)
    해당 제목의 노트가 이미 존재하면: 내용을 수정(덮어쓰기)합니다. 제목은 변경되지 않습니다.
    해당 제목의 노트가 없으면: 새 노트를 생성합니다.
    노트의 내용(content_markdown)은 markdown 포맷으로 작성해야 합니다.
    """
    client = NotebookClient()
    try:
        existing_note = client.get_note_by_title(title)
        content_html = to_html(content_markdown)
        if existing_note:
            client.update_note_content(existing_note["id"], existing_note, content_html)
            return json.dumps({
                "status": "success",
                "action": "update",
                "title": title,
                "id": existing_note['id']
            }, ensure_ascii=False)
        else:
            new_id = client.create_note(title, content_html)
            return json.dumps({
                "status": "success",
                "action": "create",
                "title": title,
                "id": new_id
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

@mcp.tool()
def move_note(old_title: str, new_title: str) -> str:
    """
    5. 노트 이동 (이름 변경)
    노트의 제목을 변경합니다. (예: 'Folder/OldName' -> 'Folder/NewName')
    노트가 이미 존재할 경우 '노트 쓰기'를 사용하여 내용을 덮어쓰기 해야 합니다.
    """
    client = NotebookClient()
    try:
        if client.get_note_by_title(new_title):
            return json.dumps({
                "status": "error", 
                "message": f"A note with the title '{new_title}' already exists."
            }, ensure_ascii=False)

        note = client.get_note_by_title(old_title)
        if not note:
            return json.dumps({
                "status": "error", 
                "message": f"Note '{old_title}' not found."
            }, ensure_ascii=False)

        client.rename_note(note["id"], note, new_title)
        return json.dumps({
            "status": "success",
            "old_title": old_title,
            "new_title": new_title,
            "note_id": note["id"]
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)