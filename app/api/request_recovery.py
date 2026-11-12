from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request

from app.auth import MemberId
from app.request_recovery_schemas import EditLookupQuery, EditLookupResponse, WaterLookupResponse
from app.schemas import RequestKey

router = APIRouter(prefix="/api", tags=["request recovery (proposal)"])
RequestPath = Annotated[RequestKey, Path()]
PROPOSAL = "FE 미합의 제안 계약. unknown/조회 오류는 실패 확정이나 재전송 승인이 아닙니다."


@router.get("/diaries/{diary_id}/edits/{client_request_id}",
            response_model=EditLookupResponse, description=PROPOSAL)
async def edit_receipt(diary_id: UUID, client_request_id: RequestPath,
                       query: Annotated[EditLookupQuery, Query()], request: Request,
                       member_id: MemberId):
    return await request.app.state.request_recovery_service.edit(
        member_id, request.headers["authorization"], diary_id, client_request_id, query,
    )


@router.get("/me/tree/water/{client_request_id}",
            response_model=WaterLookupResponse, description=PROPOSAL)
async def water_receipt(client_request_id: RequestPath, request: Request, member_id: MemberId):
    return await request.app.state.request_recovery_service.water(member_id, client_request_id)
