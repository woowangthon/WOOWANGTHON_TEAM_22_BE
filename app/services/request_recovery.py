import json

from pydantic import ValidationError

from app.clients.edits import edit_unavailable
from app.clients.room import room_storage_unavailable
from app.errors import AppError
from app.request_recovery_schemas import (
    EditLookupResponse, EditReceipt, WaterLookupResponse, WaterReceipt,
)


class RequestRecoveryService:
    def __init__(self, repository, diaries):
        self.repository = repository
        self.diaries = diaries

    async def edit(self, owner_id, token, diary_id, key, query):
        # 회원 JWT/RLS를 통한 기존 live 일기 소유권 검사를 먼저 수행한다.
        await self.diaries.owned_diary(owner_id, token, diary_id)
        raw = await self.repository.receipt(
            "diary_edit_requests",
            "request_id,user_id,diary_id,client_request_id,fingerprint,expected_version,"
            "confirmed_text,state,result,drawing",
            "user_id", owner_id, key, edit_unavailable,
        )
        response = EditLookupResponse(
            diary_id=diary_id, client_request_id=key, **query.model_dump(),
            status="unknown", edit_version=None,
        )
        # 미커밋 요청은 SELECT에 보이지 않을 수 있다. 부재는 실패/재전송 승인이 아니다.
        if raw is None:
            return response
        try:
            receipt = EditReceipt.model_validate_json(json.dumps(raw))
            if receipt.user_id != owner_id or receipt.client_request_id != key:
                raise ValueError
            if (receipt.diary_id != diary_id or receipt.fingerprint != query.fingerprint
                    or receipt.expected_version != query.expected_version):
                raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 ID의 내용이 달라요.")
            if receipt.state == "pending":
                if receipt.result is not None:
                    raise ValueError
                return response
            result = receipt.result
            if (result is None or result.diary_id != diary_id
                    or result.edit_version != receipt.expected_version + 1
                    or result.confirmed_text != receipt.confirmed_text):
                raise ValueError
            drawing = result.original_drawing
            if drawing is not None:
                self.diaries.drawings.validate_row(drawing, owner_id, diary_id)
                if drawing["upload_status"] != "ready":
                    raise ValueError
            if receipt.drawing is not None:
                if (receipt.drawing != drawing
                        or drawing["id"] != str(receipt.request_id)):
                    raise ValueError
            return response.model_copy(update={"status": "completed", "edit_version": result.edit_version})
        except AppError as error:
            if error.code == "IDEMPOTENCY_CONFLICT":
                raise
            raise edit_unavailable() from None
        except (ValidationError, ValueError, TypeError, KeyError):
            raise edit_unavailable() from None

    async def water(self, owner_id, key):
        raw = await self.repository.receipt(
            "member_tree_watering_events", "member_id,client_request_id,watered_on",
            "member_id", owner_id, key, room_storage_unavailable,
        )
        if raw is None:
            return WaterLookupResponse(client_request_id=key, status="unknown", watered_on=None)
        try:
            receipt = WaterReceipt.model_validate_json(json.dumps(raw))
            if receipt.member_id != owner_id or receipt.client_request_id != key:
                raise ValueError
        except (ValidationError, ValueError, TypeError):
            raise room_storage_unavailable() from None
        return WaterLookupResponse(
            client_request_id=key, status="completed", watered_on=receipt.watered_on,
        )
