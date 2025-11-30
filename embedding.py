import asyncio
from asyncio.log import logger
import os
import re
import sys
import json
import pandas as pd
from dotenv import load_dotenv
import chromadb
from chromadb.utils import embedding_functions
from contextlib import asynccontextmanager
from sqlalchemy import create_engine
import markdownify
from datetime import datetime
from fastmcp.utilities.logging import get_logger
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

# ---------------------------------------------------------
# 1. 설정 (Configuration)
# ---------------------------------------------------------

load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
MODEL_NAME = os.getenv("MODEL_NAME", "intfloat/multilingual-e5-base")
TEXT_PREFIX =  os.getenv("TEXT_PREFIX", "passage: ")

# MySQL 연결 문자열 (mysql+pymysql://사용자:비번@호스트:포트/DB명)
DB_CONNECTION_STR = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:3306/db1_notebook"

VECTOR_DB_PATH = "./chroma_db_store"
COLLECTION_NAME = "note_collection"
STATE_COLLECTION_NAME = "system_state"

logger = get_logger(__name__)

logger.info("MySQL 데이터베이스 연결 중...")
try:
    engine = create_engine(DB_CONNECTION_STR)
except Exception as e:
    logger.warning(f"MySQL 연결 오류: {e}")
    raise e

client = chromadb.PersistentClient(path=VECTOR_DB_PATH)
state_collection = client.get_or_create_collection(name=STATE_COLLECTION_NAME)

# ---------------------------------------------------------
# 1-1. 상태 관리 (State Management)
# ---------------------------------------------------------
def get_last_run_time():
    """
    system_state 컬렉션에서 마지막 실행 시간을 조회합니다.
    """
    # ID가 'etl_status'인 데이터 조회
    result = state_collection.get(ids=["etl_status"])
    
    # 데이터가 있으면 메타데이터에서 timestamp 반환
    if result['ids']:
        last_run = result['metadatas'][0].get('last_run_at')
        return last_run
    
    # 없으면 초기값 반환
    logger.info("[State] 실행 기록 없음. 초기화 모드로 동작.")
    return '1970-01-01 00:00:00'

def update_last_run_time(last_timestamp):
    """
    system_state 컬렉션에 마지막 실행 시간을 저장(덮어쓰기)합니다.
    """
    # datetime 객체를 문자열로 변환
    if isinstance(last_timestamp, (pd.Timestamp, datetime)):
        last_timestamp = str(last_timestamp)

    # 더미 임베딩을 피하기 위해 간단한 텍스트 저장
    # (Chroma는 텍스트나 임베딩이 필수이므로 의미 없는 값 넣음)
    state_collection.upsert(
        ids=["etl_status"],
        documents=["This is a system state record."], 
        metadatas={"last_run_at": last_timestamp}
    )
    logger.info(f"[State] 실행 시간 업데이트 완료: {last_timestamp}")


def get_pat_jti(user_id):
    result = state_collection.get(where={"sub": str(user_id)})
    return result['metadatas']


def add_pat_jti(user_id, payload):
    metadata = dict(payload, **{"iat": payload["iat"].isoformat(), "exp": payload["exp"].isoformat()})
    state_collection.upsert(
        ids=[f"jti_{user_id}_{payload['jti']}"],
        documents=["_"], 
        metadatas=metadata,
    )


def delete_pat_jti(user_id, jti):
    state_collection.delete(where={"sub": str(user_id), "jti": jti})


# ---------------------------------------------------------
# 2. Extract: MySQL에서 데이터 가져오기
# ---------------------------------------------------------
def fetch_user_from_mysql(username):
    try:
        # 실제 테이블 구조에 맞게 쿼리 수정
        # content 컬럼에는 HTML이 들어있다고 가정
        query = "SELECT us_id FROM db1_account.user where us_username=%(username)s"
        
        df = pd.read_sql(query, engine, params={'username': username})
        return df.iloc[0].to_dict()
    except Exception as e:
        logger.warning(f"MySQL 연결 또는 쿼리 오류: {e}")
        return None


def fetch_notes_from_mysql(last_run_time):
    try:
        # 실제 테이블 구조에 맞게 쿼리 수정
        # content 컬럼에는 HTML이 들어있다고 가정
        query = "SELECT co_id, us_id, co_title, co_description, co_updated FROM content where co_updated > %(last_run)s and co_type='NOTE'"
        
        df = pd.read_sql(query, engine, params={'last_run': last_run_time})
        logger.info(f"DB에서 {last_run_time} 이후에 수정된 {len(df)}개의 노트를 가져왔습니다.")
        return df
    except Exception as e:
        logger.warning(f"MySQL 연결 또는 쿼리 오류: {e}")
        return pd.DataFrame()

# ---------------------------------------------------------
# 3. Transform logic: HTML -> Markdown -> Header Split
# ---------------------------------------------------------
chunk_size = 500
chunk_overlap = 100
unlink_regex_pattern = r'\[(.*?)\]\((.*?)\)'

def process_content(original_id, user_id, title, html_content, created_at):
    """
    HTML 내용을 마크다운으로 변환하고 헤더 기반으로 청킹하여 Document 리스트를 반환합니다.
    """
    
    # [Step A] HTML -> Markdown 변환
    # heading_style="atx"는 # 기호를 사용하도록 강제합니다 (필수)
    # 링크 제거 정규식도 적용
    md_content = markdownify.markdownify(html_content or "", heading_style="atx")
    # md_content = re.sub(unlink_regex_pattern, r'[\1]()', md_content_pre)

    # [Step B] 1차 청킹: 헤더(Header) 기준 분리
    # 문서를 논리적 섹션(챕터)으로 나눕니다.
    max_level = 1
    for i in range(1, 7):
        headers_to_split_on = [
            ("#" * int(i2), "h" + str(i2))
        for i2 in range(1, max_level + 1)]
        markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
        md_header_splits = markdown_splitter.split_text(md_content)
        if any([sys.getsizeof(d.page_content) > chunk_size for d in md_header_splits]):
            max_level += 1
        else:
            break

    # [Step C] 2차 청킹: 문자 수 기준 분리
    # 헤더로 나눴어도 특정 챕터의 내용이 클 수 있으므로 안전장치로 다시 자릅니다.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n"]
    )
    
    # Document 객체 리스트를 받아 다시 쪼갭니다.
    final_splits = text_splitter.split_documents(md_header_splits)

    processed_data = []
    
    for idx, split in enumerate(final_splits):
        # 헤더 정보가 메타데이터에 포함되어 있음 (예: {'#': '소개', '##': '배경'})
        header_metadata = split.metadata 
        
        # 검색 품질을 위해 텍스트 앞에 상위 헤더 문맥을 붙여줄 수도 있음 (선택사항)
        # 여기서는 원본 텍스트 그대로 사용
        raw_chunk_text = split.page_content
        if (len(raw_chunk_text) < 2):
            continue  # 너무 짧은 청크는 무시
        # 현재 청크 내의 모든 링크 추출 (텍스트, URL)
        found_links = re.findall(unlink_regex_pattern, raw_chunk_text)
        
        # 메타데이터용 리스트 생성 (JSON 저장을 위해 dict 리스트로)
        # 예: [{"text": "구글", "url": "https://google.com"}, ...]
        links_meta_list = [{"text": link_text, "url": link_url} for link_text, link_url in found_links]
        
        # 3. 임베딩용 텍스트 정제 (기존 요구사항: URL 제거하고 텍스트만 남기거나 []()형태로)
        # 여기서는 [텍스트]() 형태로 변경합니다.
        chunk_text = re.sub(unlink_regex_pattern, r'[\1]()', raw_chunk_text)


        # 헤더 경로 문자열 생성
        header_path = "\n".join([f"{'#' * int(k[1])} {v}" for k, v in header_metadata.items()]) if header_metadata else ""
        text = f"# {title}\n{header_path}\n{chunk_text}"  # 임베딩될 텍스트
        logger.debug("================================")
        logger.debug(text)

        processed_data.append({
            "id": f"{original_id}_{idx}", # 유니크 ID 생성
            "text": f"{TEXT_PREFIX}{text}",
            "metadata": {
                "original_id": original_id,
                "user_id": user_id,
                "title": title,
                "created_at": str(created_at),
                "links": json.dumps(links_meta_list, ensure_ascii=False),
                **header_metadata # 헤더 정보도 메타데이터로 저장
            }
        })
        
    return processed_data

# ---------------------------------------------------------
# 4. Main Pipeline
# ---------------------------------------------------------
def run_pipeline():
    # A. 마지막 실행 시간 로드
    last_run = get_last_run_time()
    df = fetch_notes_from_mysql(last_run)
    if df.empty:
        return

    # ChromaDB 설정
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=MODEL_NAME
    )
    note_collection = client.get_or_create_collection(name=COLLECTION_NAME, embedding_function=ef)

    documents = []
    metadatas = []
    ids = []

    updated_original_ids = df['co_id'].tolist()

    # [Step 4] 기존 청크 삭제 (Clean up)
    if updated_original_ids:
        logger.info(f"업데이트 대상 문서 {len(updated_original_ids)}개의 기존 벡터 삭제 중...")
        # where 절을 사용하여 삭제
        for original_id in updated_original_ids:
             note_collection.delete(where={"original_id": original_id})

    # [Step 5] 데이터 변환
    logger.info("새 데이터 변환 중...")
    for _, row in df.iterrows():
        chunks = process_content(row['co_id'], row['us_id'], row['co_title'], row['co_description'], row['co_updated'])
        for chunk in chunks:
            documents.append(chunk['text'])
            metadatas.append(chunk['metadata'])
            ids.append(chunk['id'])

    # [Step 6] 데이터 저장
    if documents:
        batch_size = 100
        logger.info(f"총 {len(documents)}개의 청크를 {batch_size}개씩 나누어 저장합니다.")
        for i in range(0, len(documents), batch_size):
            logger.info(f"  - 청크 {i} ~ {min(i+batch_size, len(documents))} 저장 중...")
            end = i + batch_size
            note_collection.upsert(
                documents=documents[i:end],
                metadatas=metadatas[i:end],
                ids=ids[i:end]
            )
        
        # [Step 7] 상태값 업데이트 (DB 내부 컬렉션 이용)
        max_updated_at = df['co_updated'].max()
        update_last_run_time(max_updated_at)
        
        logger.info(f"작업 완료. 총 {len(documents)}개 청크 저장됨.")


async def embedding():
    loop = asyncio.get_running_loop()
    try:
        while True:
            # [핵심] 동기 함수(run_pipeline)를 별도 스레드 풀에서 실행
            # 이렇게 해야 메인 루프가 멈추지 않아 cancel() 신호를 받을 수 있음
            await loop.run_in_executor(None, run_pipeline)
            await asyncio.sleep(5)
            
    except asyncio.CancelledError:
        # task.cancel()이 호출되면 이 블록이 실행됨
        logger.warning("Embedding task was cancelled via signal!")
        raise  # 에러를 다시 던져줘야 완전히 종료됨


@asynccontextmanager
async def server_lifespan(server):
    # [시작 시 실행]
    logger.info("Background embedding task started.")
    # 백그라운드 작업 시작 (반드시 task 변수에 할당해두어야 GC되지 않음)
    task = asyncio.create_task(embedding())
    
    yield  # 여기서 서버가 실행됨 (대기)
    
    # [종료 시 실행]
    logger.info("Stopping background task...")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Stop background task completed.")