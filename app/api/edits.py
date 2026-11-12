import re
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.exceptions import HTTPException

from app.auth import MemberId
from app.edit_schemas import EditRequest, EditResponse
from app.errors import AppError
from app.services.drawing_images import MAX_BYTES

router = APIRouter(prefix="/api/diaries", tags=["diary edits v0.4"])


@router.put("/{diary_id}/edits", response_model=EditResponse, openapi_extra={
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "additionalProperties": False,
        "required": ["client_request_id", "expected_version", "confirmed_text"],
        "properties": {"client_request_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"},
                       "expected_version": {"type": "integer", "minimum": 1, "maximum": 2147483646},
                       "confirmed_text": {"type": "string", "maxLength": 20000},
                       "file": {"type": "string", "format": "binary"}},
    }}}},
})
async def edit_diary(diary_id: UUID, request: Request, member_id: MemberId):
    token = request.headers["authorization"]
    await request.app.state.diary_service.repository.owned_diary(member_id, token, diary_id)
    received = 0

    async def limited_receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_BYTES + 131072:
            raise MultiPartException("요청 크기 초과")
        return message

    try:
        if request.headers.get("content-type", "").split(";", 1)[0] != "multipart/form-data":
            raise ValueError
        limited = Request(request.scope, receive=limited_receive)
        async with limited.form(max_files=1, max_fields=3, max_part_size=80000) as form:
            pairs = list(form.multi_items())
            if len({key for key, _ in pairs}) != len(pairs):
                raise ValueError
            values = dict(pairs)
            file = values.pop("file", None)
            if file is not None and not isinstance(file, UploadFile):
                raise ValueError
            version = values.get("expected_version")
            if not isinstance(version, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", version):
                raise ValueError
            values["expected_version"] = int(version)
            body = EditRequest.model_validate(values)
            content = await file.read(MAX_BYTES + 1) if file is not None else None
    except (ValueError, ValidationError, MultiPartException, HTTPException):
        if received > MAX_BYTES + 131072:
            raise AppError(413, "DRAWING_TOO_LARGE", "요청 크기 초과") from None
        raise AppError(422, "DIARY_EDIT_INVALID", "수정 내용을 확인해 주세요.") from None
    return await request.app.state.edit_service.edit(member_id, token, diary_id, body, content)


@router.delete("/{diary_id}", status_code=204, openapi_extra={"parameters": [
    {"name": "Idempotency-Key", "in": "header", "required": True,
     "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}},
    {"name": "If-Match", "in": "header", "required": True,
     "schema": {"type": "string", "pattern": '^"[1-9][0-9]{0,9}"$'}},
]})
async def delete_diary(diary_id: UUID, request: Request, member_id: MemberId):
    key = request.headers.get("Idempotency-Key", "")
    match = re.fullmatch(r'"([1-9][0-9]{0,9})"', request.headers.get("If-Match", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) or not match or int(match[1]) > 2147483647:
        raise AppError(422, "DIARY_EDIT_INVALID", "요청 ID와 If-Match 버전을 확인해 주세요.")
    await request.app.state.edit_service.delete(member_id, diary_id, key, int(match[1]))
    return Response(status_code=204)
