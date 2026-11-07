from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.auth import MemberId
from app.diary_schemas import ConfirmDiaryRequest, DiaryDetail, DiaryMonth

router = APIRouter(prefix="/api", tags=["diaries (제안 계약)"])


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
