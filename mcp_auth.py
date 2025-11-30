import os
import jwt
import datetime
import uuid
from dotenv import load_dotenv
from starlette.requests import Request
from starlette.responses import JSONResponse
from fastmcp.server.middleware import Middleware, MiddlewareContext

from embedding import fetch_user_from_mysql, add_pat_jti, get_pat_jti

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
PAT_SECRET_KEY = os.getenv("PAT_SECRET_KEY")
PAT_EXPIRATION_DAYS = os.getenv("PAT_EXPIRATION_DAYS", 7)
ALGORITHM = "HS256"

def authenticate(request:Request):
    auth_header = request.headers.get("Authorization")
    # 1. 헤더 존재 여부 및 스키마 확인
    if not auth_header:
        return JSONResponse(
            status_code=401, 
            content={"detail": "Missing Authorization header"}
        )
    # 2. 토큰 추출
    prefix, token = auth_header.split(" ")
    if prefix not in ["JWT", "Bearer", "PAT"]:
        return JSONResponse(
            status_code=401, 
            content={"detail": "Invalid Authorization header"}
        )
    try:
        if prefix == "PAT":
            # PAT 토큰 검증
            payload = jwt.decode(token, PAT_SECRET_KEY, algorithms=[ALGORITHM])
            jti_list = [i['jti'] for i in get_pat_jti(int(payload["sub"]))]
            if payload["jti"] not in jti_list:
                return JSONResponse(
                    status_code=401, 
                    content={"detail": "Invalid PAT token"}
                )
            # 검증 성공 시: request.state에 사용자 정보 저장
            request.state.user = {"us_id": int(payload["sub"])}
        else:
            # 3. 토큰 디코딩 및 서명 검증
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

            # 4. 검증 성공 시: request.state에 사용자 정보 저장
            # (이후 라우터나 툴에서 request.state.user로 접근 가능)
            request.state.user = fetch_user_from_mysql(payload["sub"])
        if request.state.user is None:
            return JSONResponse(
                status_code=401, 
                content={"detail": "User not found"}
            )
    except jwt.ExpiredSignatureError:
        return JSONResponse(
            status_code=401, 
            content={"detail": "Token has expired"}
        )
    except jwt.InvalidTokenError as e:
        return JSONResponse(
            status_code=401, 
            content={"detail": "Invalid token"}
        )
    return None


class AuthenticationMiddleware(Middleware):
    async def on_request(self, context: MiddlewareContext, call_next):
        response = authenticate(context.request)
        if response:
            return response
        return await call_next(context)
    

def create_pat_token(user_id: int) -> dict:
    """
    주어진 사용자 ID로 Personal Access Token (PAT) 생성
    Args:
        user_id: 토큰을 발급할 사용자 ID
    Returns:
        생성된 JWT 토큰 문자열
    """
    payload = {
        "sub": str(user_id),
        "iat": datetime.datetime.now(datetime.timezone.utc),
        "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=PAT_EXPIRATION_DAYS),
        "jti": str(uuid.uuid4())
    }
    token = jwt.encode(payload, PAT_SECRET_KEY, algorithm=ALGORITHM)
    add_pat_jti(user_id, payload)
    return {"access_token": token}