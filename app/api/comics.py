from uuid import UUID

from fastapi import APIRouter, Request

from app.auth import MemberId
from app.comic_schemas import ComicAttachRequest, ComicPreviewRequest, ComicResponse, DiaryComicResponse

router = APIRouter(prefix="/api", tags=["comics (로컬 후보 계약)"])


@router.post("/chat-sessions/{session_id}/comic-previews", response_model=ComicResponse,
             responses={503: {"description": "확인 계약·지속성 실행 수단 합의 전 접수 보류"}})
async def create_preview(session_id: str, body: ComicPreviewRequest,
                         request: Request, member_id: MemberId):
    return await request.app.state.comic_service.create_preview(member_id, session_id, body)


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
