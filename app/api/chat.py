from typing import Annotated

from fastapi import APIRouter, Header, Request

from app.auth import MemberId
from app.schemas import (
    DiaryDraftRequest, DiaryDraftResponse, MessageRequest, MessageResponse,
    RequestKey, SessionResponse, StartSessionRequest,
)

router = APIRouter(prefix="/api/chat-sessions", tags=["chat (잠정 계약)"])


@router.post("", status_code=201, response_model=SessionResponse)
async def start_session(
    body: StartSessionRequest, request: Request, member_id: MemberId,
    idempotency_key: Annotated[RequestKey, Header(alias="Idempotency-Key")],
):
    return await request.app.state.chat_service.start(idempotency_key)


@router.post("/{session_id}/messages", response_model=MessageResponse)
async def send_message(session_id: str, body: MessageRequest, request: Request, member_id: MemberId):
    return await request.app.state.chat_service.send(session_id, body)


@router.post("/{session_id}/diary-draft", response_model=DiaryDraftResponse)
async def create_diary_draft(session_id: str, body: DiaryDraftRequest, request: Request,
                             member_id: MemberId):
    return await request.app.state.chat_service.diary_draft(session_id, body)
