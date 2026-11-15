import asyncio
import json

import httpx
from pydantic import ValidationError

from app.errors import AppError
from app.worry_schemas import WorryAIReply


class WorryAIClient:
    def __init__(self, http):
        self.http = http

    async def chat(self, messages):
        # 오직 서버가 보관한 대화만 전송한다. ID, 동의 근거, 회원 정보는 보내지 않는다.
        payload = [{"role": m.role, "text": m.text} for m in messages]
        if (len(payload) > 24 or sum(len(m["text"]) for m in payload) > 8000
                or any(not m["text"].strip() or len(m["text"]) > 2000 for m in payload)
                or (payload and payload[-1]["role"] != "child")):
            raise AppError(409, "WORRY_HISTORY_LIMIT", "대화를 종료하거나 새 대화를 시작해 주세요.")
        try:
            async with asyncio.timeout(30):
                async with self.http.stream("POST", "/api/worry/chat", json={"messages": payload}) as r:
                    if r.status_code == 422:
                        raise AppError(502, "WORRY_AI_REJECTED", "AI 요청 계약 확인이 필요해요.")
                    if r.status_code != 200:
                        raise AppError(502, "WORRY_AI_OUTCOME_UNKNOWN", "AI 응답을 확인하지 못했어요.")
                    data = bytearray()
                    async for chunk in r.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 32768:
                            raise ValueError
            reply = WorryAIReply.model_validate(json.loads(data))
            if (not payload and reply.status == "reply") or (payload and reply.status == "opening"):
                raise ValueError
            return reply
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise AppError(503, "WORRY_AI_NOT_SENT", "AI에 연결하지 못했어요.") from None
        except (httpx.RequestError, TimeoutError, ValidationError, ValueError, UnicodeError):
            raise AppError(502, "WORRY_AI_OUTCOME_UNKNOWN", "AI 응답을 확인하지 못했어요.") from None
