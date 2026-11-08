from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.auth import MemberId
from app.diary_schemas import ConfirmDiaryRequest, DiaryDetail, DiaryMonth
from app.drawing_schemas import DrawingResponse
from app.errors import AppError
from app.services.drawing_images import MAX_BYTES

router = APIRouter(prefix="/api", tags=["diaries (제안 계약)"])


@router.post("/diaries/{diary_id}/drawing", response_model=DrawingResponse, openapi_extra={
    "requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["file"], "properties": {
            "file": {"type": "string", "format": "binary",
                     "description": "JPEG/PNG, 최대 10 MiB"},
        }},
    }}},
})
async def upload_drawing(diary_id: UUID, request: Request, member_id: MemberId):
    token = request.headers["authorization"]
    # 회원 소유권 확인
    await request.app.state.diary_service.repository.owned_diary(member_id, token, diary_id)
    received = 0

    async def limited_receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_BYTES + 65536:
            raise MultiPartException("그림 크기 초과")
        return message

    # multipart 전체 크기 제한
    limited = Request(request.scope, receive=limited_receive)
    try:
        async with limited.form(max_files=1, max_fields=4, max_part_size=1024) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise AppError(422, "DRAWING_INVALID_IMAGE", "그림 형식 오류")
            content = await file.read(MAX_BYTES + 1)
    except AppError:
        raise
    except Exception:
        if received > MAX_BYTES + 65536:
            raise AppError(413, "DRAWING_TOO_LARGE", "그림 크기 초과") from None
        raise AppError(422, "DRAWING_INVALID_IMAGE", "그림 형식 오류") from None
    return await request.app.state.drawing_service.save(member_id, token, diary_id, content)


@router.post("/chat-sessions/{session_id}/diaries", response_model=DiaryDetail)
async def confirm_diary(session_id: str, body: ConfirmDiaryRequest,
                        request: Request, member_id: MemberId):
    return await request.app.state.diary_service.confirm(
        member_id, request.headers["authorization"], session_id, body,
    )


@router.get("/diaries", response_model=DiaryMonth)
async def list_diaries(request: Request, member_id: MemberId,
                       month: Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]):
    return await request.app.state.diary_service.repository.month(
        member_id, request.headers["authorization"], month,
    )


@router.get("/diaries/{diary_id}", response_model=DiaryDetail)
async def get_diary(diary_id: UUID, request: Request, member_id: MemberId):
    return await request.app.state.diary_service.repository.detail(
        member_id, request.headers["authorization"], diary_id,
    )
