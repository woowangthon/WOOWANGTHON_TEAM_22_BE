from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from app.auth import MemberId
from app.schemas import RequestKey
from app.worry_schemas import (NOTICE, NOTICE_VERSION, WorryStart, WorryMessageRequest,
                               WorrySession, WorryList)

router = APIRouter(prefix="/api/worry", tags=["worry (제안 계약)"])


@router.get("/policy")
async def policy(request: Request, member_id: MemberId):
    return {"notice_version": NOTICE_VERSION, "notice": NOTICE, "retention_days": 7,
            "guardian_access": False, "enabled": request.app.state.worry_service.enabled,
            "server_authorization_required": True}


@router.post("/sessions", response_model=WorrySession, status_code=201)
async def start(body: WorryStart, request: Request, member_id: MemberId,
                key: Annotated[RequestKey, Header(alias="Idempotency-Key")]):
    return await request.app.state.worry_service.start(member_id, key, body)


@router.post("/sessions/{session_id}/messages", response_model=WorrySession)
async def send(session_id: UUID, body: WorryMessageRequest, request: Request, member_id: MemberId):
    return await request.app.state.worry_service.send(member_id, session_id, body)


@router.post("/sessions/{session_id}/finish", response_model=WorrySession)
async def finish(session_id: UUID, request: Request, member_id: MemberId):
    return await request.app.state.worry_service.finish(member_id, session_id)


@router.get("/sessions", response_model=WorryList)
async def sessions(request: Request, member_id: MemberId,
                   limit: Annotated[int, Query(ge=1, le=100)] = 50,
                   offset: Annotated[int, Query(ge=0, le=10000)] = 0):
    return await request.app.state.worry_service.list(member_id, limit, offset)


@router.get("/sessions/{session_id}", response_model=WorrySession)
async def detail(session_id: UUID, request: Request, member_id: MemberId):
    return await request.app.state.worry_service.get(member_id, session_id)
