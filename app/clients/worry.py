import asyncio
import json

import httpx
from pydantic import ValidationError

from app.errors import AppError
from app.worry_schemas import WorrySession, WorrySummary

ERRORS = {
    "WORRY_ACCESS_REQUIRED": (403, "서버에서 확인한 이용 권한과 동의가 필요해요."),
    "WORRY_NOTICE_REQUIRED": (409, "보관 기간과 열람 안내를 확인해 주세요."),
    "WORRY_NOT_FOUND": (404, "대화를 찾을 수 없어요."),
    "WORRY_FINISHED": (409, "종료된 대화예요. 새 대화를 시작해 주세요."),
    "WORRY_HISTORY_LIMIT": (409, "대화 한도에 도달했어요. 종료하거나 새 대화를 시작해 주세요."),
    "WORRY_RECOVERY_REQUIRED": (409, "저장된 처리 상태를 확인하고 종료하거나 새 대화를 시작해 주세요."),
    "IDEMPOTENCY_CONFLICT": (409, "같은 요청 ID에 다른 내용을 보낼 수 없어요."),
}


class WorryRepository:
    """회원 소유권·만료·동의와 원장 전이는 서버 전용 원자적 RPC에서 재검사한다."""
    def __init__(self, writer, timeout):
        self.http, self.timeout = writer, timeout

    async def command(self, owner, action, session_id=None, **args):
        if self.http is None:
            raise AppError(503, "WORRY_STORE_UNAVAILABLE", "고민 대화 저장소 설정이 필요해요.")
        try:
            async with asyncio.timeout(self.timeout):
                r = await self.http.post("/rest/v1/rpc/worry_command", json={
                    "p_owner": str(owner), "p_action": action,
                    "p_session": str(session_id) if session_id else None, "p_args": args,
                })
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, TimeoutError, ValueError):
            # DB 응답 유실도 자동 재전송하지 않는다. 같은 키 또는 GET으로 복구한다.
            raise AppError(503, "WORRY_STORE_UNAVAILABLE", "저장 상태를 확인해 주세요.") from None
        if not isinstance(data, dict):
            raise AppError(502, "WORRY_STORE_INVALID", "대화 기록 형식 확인이 필요해요.")
        if "error" in data:
            code = data["error"]
            status, message = ERRORS.get(code, (502, "대화 기록 형식 확인이 필요해요."))
            raise AppError(status, code if code in ERRORS else "WORRY_STORE_INVALID", message)
        return data

    @staticmethod
    def snapshot(data):
        try:
            return WorrySession.model_validate_json(json.dumps(data["session"]))
        except (KeyError, TypeError, ValueError, ValidationError):
            raise AppError(502, "WORRY_STORE_INVALID", "대화 기록 형식 확인이 필요해요.") from None

    async def list(self, owner, limit, offset):
        data = await self.command(owner, "list", limit=limit, offset=offset)
        try:
            return [WorrySummary.model_validate_json(json.dumps(s)) for s in data["sessions"]]
        except (KeyError, TypeError, ValueError, ValidationError):
            raise AppError(502, "WORRY_STORE_INVALID", "대화 기록 형식 확인이 필요해요.") from None
