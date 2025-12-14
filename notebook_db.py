import pandas as pd
from sqlalchemy import create_engine
from fastmcp.utilities.logging import get_logger

from env import DB_USER, DB_PASSWORD

# MySQL 연결 문자열 (mysql+pymysql://사용자:비번@호스트:포트/DB명)
DB_CONNECTION_STR = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:3306/db1_notebook"
logger = get_logger(__name__)

logger.info("MySQL 데이터베이스 연결 중...")
try:
    engine = create_engine(DB_CONNECTION_STR)
except Exception as e:
    logger.warning(f"MySQL 연결 오류: {e}")
    raise e

def fetch_token_from_db(token):
    try:
        query = "SELECT us_id, pa_token, pa_expired FROM personal_access_token where pa_token=%(token)s"
        
        df = pd.read_sql(query, engine, params={'token': token})
        return df.iloc[0].to_dict()
    except Exception as e:
        logger.warning(f"MySQL 연결 또는 쿼리 오류: {e}")
        return None


def fetch_user_from_db(username):
    try:
        # 실제 테이블 구조에 맞게 쿼리 수정
        # content 컬럼에는 HTML이 들어있다고 가정
        query = "SELECT us_id FROM db1_account.user where us_username=%(username)s"
        
        df = pd.read_sql(query, engine, params={'username': username})
        return df.iloc[0].to_dict()
    except Exception as e:
        logger.warning(f"MySQL 연결 또는 쿼리 오류: {e}")
        return None


def fetch_notes_from_db(last_run_time):
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