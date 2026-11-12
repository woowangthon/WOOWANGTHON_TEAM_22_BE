import re
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPBearer

from app.clients.auth import authentication_failed
from app.errors import AppError


bearer = HTTPBearer(auto_error=False)


async def require_member(request: Request, credentials=Depends(bearer)) -> UUID:
    headers = request.headers.getlist("authorization")
    # 인증 실패 처리
    if len(headers) != 1 or credentials is None:
        raise authentication_failed()
    match = re.fullmatch(r"Bearer ([A-Za-z0-9._~+/-]+=*)", headers[0], re.IGNORECASE)
    if match is None or len(match[1]) > 8192:
        raise authentication_failed()
    auth = request.app.state.auth_client
    if auth is None:
        raise AppError(503, "AUTH_NOT_CONFIGURED", "인증 설정 누락")
    return await auth.verify(match[1])


MemberId = Annotated[UUID, Depends(require_member)]
