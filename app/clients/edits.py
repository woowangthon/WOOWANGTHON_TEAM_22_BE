import asyncio

import httpx

from app.errors import AppError


EDIT_ERRORS = {
    "DIARY_NOT_FOUND": 404,
    "DIARY_EDIT_VERSION_CONFLICT": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "DIARY_EDIT_INVALID": 422,
}


def edit_unavailable():
    return AppError(503, "DIARY_EDIT_STORAGE_UNAVAILABLE", "수정·삭제 결과를 확인할 수 없어요. 같은 요청 ID로 다시 확인해 주세요.", True)


class EditRepository:
    def __init__(self, diaries):
        self.diaries = diaries

    async def command(self, owner_id, diary_id, action, payload):
        writer = self.diaries.writer
        if writer is None:
            raise edit_unavailable()
        try:
            async with asyncio.timeout(self.diaries.timeout_seconds):
                response = await writer.post("/rest/v1/rpc/member_diary_edit", json={
                    "p_user_id": str(owner_id), "p_diary_id": str(diary_id),
                    "p_action": action, "p_payload": payload,
                })
        except (httpx.HTTPError, TimeoutError):
            raise edit_unavailable() from None
        if not response.is_success:
            try:
                error = response.json()
                code = error.get("details")
                status = EDIT_ERRORS.get(code)
                if status == response.status_code and error.get("code") == f"PT{status}":
                    raise AppError(status, code, "일기 상태 또는 요청 내용을 확인해 주세요.")
            except (ValueError, AttributeError, TypeError):
                pass
            raise edit_unavailable()
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except ValueError:
            raise edit_unavailable() from None
