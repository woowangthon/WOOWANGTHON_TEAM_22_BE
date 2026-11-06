import asyncio

import httpx
from pydantic import ValidationError

from app.errors import AppError
from app.schemas import AIMessage, AIReply


def outcome_unknown(status: int = 502) -> AppError:
    return AppError(status, "AI_OUTCOME_UNKNOWN",
                    "응답을 확인하지 못했어요. 자동으로 다시 보내지 않습니다.")


class AIClient:
    def __init__(self, http: httpx.AsyncClient, request_timeout: float):
        self.http = http
        self.request_timeout = request_timeout

    async def chat(self, messages: list[AIMessage]) -> AIReply:
        try:
            # read timeout과 별개로 전체 HTTP 대기에 상한을 둡니다.
            async with asyncio.timeout(self.request_timeout):
                response = await self.http.post(
                    "/api/chat", json={"messages": [item.model_dump() for item in messages]},
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            raise AppError(503, "AI_CONNECTION_FAILED",
                           "AI 서버에 연결하지 못했어요.", retryable=True) from error
        except (httpx.TimeoutException, TimeoutError) as error:
            raise outcome_unknown(504) from error
        except httpx.RequestError as error:
            raise outcome_unknown() from error

        # 리다이렉트·AI 오류 본문은 전달하거나 따라가지 않습니다.
        if response.status_code in {429, 503}:
            raise AppError(503, "AI_UNAVAILABLE", "AI 서버가 요청을 처리하지 못했어요.")
        if 400 <= response.status_code < 500:
            raise AppError(502, "AI_REQUEST_REJECTED", "AI 서버와 요청 계약을 확인해야 해요.")
        if response.status_code != 200:
            raise AppError(502, "AI_BAD_RESPONSE", "AI 서버 응답을 확인하지 못했어요.")
        try:
            return AIReply.model_validate(response.json())
        except (ValidationError, ValueError, UnicodeError) as error:
            raise AppError(502, "AI_BAD_RESPONSE", "AI 서버 응답 형식이 올바르지 않아요.") from error
