from uuid import UUID

from fastapi import APIRouter, Request, Response

from app.auth import MemberId
from app.comic_job_schemas import ComicJobResponse
from app.schemas import RequestKey
from app.comic_schemas import (ComicAttachRequest, ComicPlanRequest, ComicPreviewRequest,
                               ComicResponse, DiaryComicResponse, VisualPlan)

router = APIRouter(prefix="/api", tags=["comics (로컬 후보 계약)"])


@router.post("/chat-sessions/{session_id}/comic-plans", response_model=VisualPlan)
async def plan_comic(session_id: str, body: ComicPlanRequest,
                     request: Request, member_id: MemberId):
    return await request.app.state.comic_service.plan(member_id, session_id, body)


@router.post("/chat-sessions/{session_id}/comic-previews", response_model=ComicJobResponse | ComicResponse,
             responses={202: {"model": ComicJobResponse, "description": "영속 원장에 접수됨; 원래 request ID로 조회"},
                        503: {"description": "worker 비활성 또는 DB 실행 수단 준비 실패"}})
async def create_preview(session_id: str, body: ComicPreviewRequest,
                         request: Request, response: Response, member_id: MemberId):
    result = await request.app.state.comic_service.create_preview(member_id, session_id, body)
    if isinstance(result, ComicJobResponse) and result.job_state in {'queued', 'running'}:
        response.status_code = 202
    return result


@router.get("/chat-sessions/{session_id}/comic-requests/{client_request_id}", response_model=ComicJobResponse)
async def get_comic_request(session_id: str, client_request_id: RequestKey,
                            request: Request, member_id: MemberId):
    return await request.app.state.comic_service.jobs.get(
        member_id, request.headers['authorization'], session_id, client_request_id,
    )


@router.get("/chat-sessions/{session_id}/comic-previews/{preview_id}", response_model=ComicResponse)
async def get_preview(session_id: str, preview_id: UUID, request: Request, member_id: MemberId):
    return await request.app.state.comic_service.preview(
        member_id, request.headers["authorization"], session_id, preview_id,
    )


@router.post("/diaries/{diary_id}/comics", response_model=ComicResponse)
async def attach_comic(diary_id: UUID, body: ComicAttachRequest, request: Request, member_id: MemberId):
    return await request.app.state.comic_service.attach(
        member_id, request.headers["authorization"], diary_id, body,
    )


@router.get("/diaries/{diary_id}/comics", response_model=DiaryComicResponse)
async def get_comic(diary_id: UUID, request: Request, member_id: MemberId):
    return await request.app.state.comic_service.diary(
        member_id, request.headers["authorization"], diary_id,
    )
