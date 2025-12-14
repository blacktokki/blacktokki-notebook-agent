import hashlib
import jwt
import uuid
from datetime import datetime
from starlette.requests import Request
from starlette.responses import JSONResponse
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.dependencies import get_http_request

from env import SECRET_KEY
from notebook_db import fetch_token_from_db, fetch_user_from_db

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
    if prefix == "PAT":
        # PAT 토큰 검증
        hashed_object = hashlib.sha256(token.encode('utf-8'))
        calculated_hash = hashed_object.hexdigest()
        stored_token = fetch_token_from_db(calculated_hash)            
        if stored_token is None:
            return JSONResponse(
                status_code=401, 
                content={"detail": "Invalid PAT token"}
            )
        if datetime.fromisoformat(str(stored_token['pa_expired'])) < datetime.now():
            return JSONResponse(
                status_code=401, 
                content={"detail": "Expired PAT token"}
            )
        request.state.user = {"us_id": int(stored_token["us_id"])}
    else:
        try:
            # 3. 토큰 디코딩 및 서명 검증
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

            # 4. 검증 성공 시: request.state에 사용자 정보 저장
            # (이후 라우터나 툴에서 request.state.user로 접근 가능)
            request.state.user = fetch_user_from_db(payload["sub"])
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
    if request.state.user is None:
        return JSONResponse(
            status_code=401, 
            content={"detail": "User not found"}
        )
    return None


class AuthenticationMiddleware(Middleware):
    async def on_request(self, context: MiddlewareContext, call_next):
        request = get_http_request()
        if request:
            response = authenticate(request)
            if response:
                return response
        return await call_next(context)
