import asyncio
from uuid import UUID

import httpx

from app.errors import AppError


def authentication_failed() -> AppError:
    return AppError(401, "AUTHENTICATION_FAILED", "인증 실패")


class AuthClient:
    def __init__(self, http: httpx.AsyncClient, timeout_seconds: float):
        self.http = http
        self.timeout_seconds = timeout_seconds

    async def verify(self, token: str) -> UUID:
        # 회원 토큰 검증
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.http.get(
                    "/auth/v1/user", headers={"Authorization": f"Bearer {token}"},
                )
        except (httpx.HTTPError, TimeoutError):
            raise AppError(503, "AUTH_UNAVAILABLE", "인증 서버 연결 실패", True) from None

        if response.status_code in {400, 401, 403}:
            raise authentication_failed()
        if response.status_code == 429 or response.status_code >= 500:
            raise AppError(503, "AUTH_UNAVAILABLE", "인증 서버 연결 실패", True)
        if response.status_code != 200:
            raise AppError(502, "AUTH_INVALID_RESPONSE", "인증 서버 응답 오류")
        try:
            user = response.json()
            if not isinstance(user, dict) or not isinstance(user.get("id"), str):
                raise ValueError
            user_id = UUID(user["id"])
            if user_id.int == 0:
                raise ValueError
        except (ValueError, TypeError):
            raise AppError(502, "AUTH_INVALID_RESPONSE", "인증 서버 응답 오류") from None
        if (user.get("is_anonymous") is not False
                or user.get("role") != "authenticated"
                or user.get("aud") != "authenticated"):
            raise AppError(403, "ACCESS_DENIED", "접근 권한 없음")
        return user_id
