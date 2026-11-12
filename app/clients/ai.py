import asyncio

import httpx
from pydantic import ValidationError

from app.errors import AppError
from app.schemas import AIDiaryReply, AIMessage, AIReply


def outcome_unknown(status: int = 502) -> AppError:
    return AppError(status, "AI_OUTCOME_UNKNOWN",
                    "응답을 확인하지 못했어요. 자동으로 다시 보내지 않습니다.")


class AIClient:
    def __init__(self, http: httpx.AsyncClient, request_timeout: float):
        self.http = http
        self.request_timeout = request_timeout

    async def chat(self, messages: list[AIMessage]) -> AIReply:
        response = await self._post("/api/chat", messages)
        try:
            return AIReply.model_validate(response.json())
        except (ValidationError, ValueError, UnicodeError) as error:
            raise AppError(502, "AI_BAD_RESPONSE", "AI 서버 응답 형식이 올바르지 않아요.") from error

    async def diary(self, messages: list[AIMessage]) -> AIDiaryReply:
        response = await self._post("/api/diary", messages)
        try:
            result = AIDiaryReply.model_validate(response.json())
            expected = [{"id": index, "text": item.text}
                        for index, item in enumerate(messages) if item.role == "child"]
            if [source.model_dump() for source in result.sources] != expected:
                raise ValueError("아이 발언의 인덱스/원문/순서와 출처가 다릅니다.")
            allowed = {source["id"] for source in expected}
            if not result.requires_confirmation or any(
                not set(page.source_ids) <= allowed
                or len(set(page.source_ids)) != len(page.source_ids)
                for page in result.pages
            ):
                raise ValueError("확인 전 초안의 출처 또는 상태가 올바르지 않습니다.")
            # AI는 정상 생성 text를 공백으로, 페이지 문장은 줄바꿈으로 연결합니다.
            if " ".join(result.text.split()) != " ".join(
                " ".join(page.text for page in result.pages).split()
            ):
                raise ValueError("일기 본문과 페이지 내용이 다릅니다.")
            return result
        except (ValidationError, ValueError, UnicodeError) as error:
            raise AppError(502, "AI_BAD_RESPONSE", "AI 서버 응답 형식이 올바르지 않아요.") from error

    async def _post(self, path: str, messages: list[AIMessage]) -> httpx.Response:
        try:
            # read timeout과 별개로 전체 HTTP 대기에 상한을 둡니다.
            async with asyncio.timeout(self.request_timeout):
                response = await self.http.post(
                    path, json={"messages": [item.model_dump() for item in messages]},
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
        return response
