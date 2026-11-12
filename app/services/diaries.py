import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from app.clients.diaries import DiaryRepository
from app.diary_schemas import ConfirmDiaryRequest, DiaryDetail
from app.errors import AppError
from app.schemas import DiaryDraftResponse
from app.session_store import RequestRecord, SessionStore
from app.persistent_session_store import PersistentSessionStore
from app.services.room import RoomService

logger = logging.getLogger(__name__)


class DiaryService:
    def __init__(self, store: SessionStore | PersistentSessionStore, repository: DiaryRepository,
                 rewards: RoomService | None = None):
        self.store = store
        self.repository = repository
        self.rewards = rewards

    async def _confirmed_detail(self, owner_id: UUID, token: str, diary_id: UUID) -> DiaryDetail:
        detail = await self.repository.detail(owner_id, token, diary_id)
        if self.rewards is not None:
            try:
                # 확정 저장은 이미 커밋되었다. 별도 RPC 실패가 저장 성공을 취소하면 안 된다.
                # 프로세스가 여기 도달하지 못해도 DB의 일기/보류 목록/지급 기록으로 sync가 복구한다.
                await self.rewards.execute(owner_id, "sync_rewards")
            except Exception:
                # 키/본문/회원 ID/DB 오류 원문은 로그에 남기지 않는다.
                logger.warning("DIARY_REWARD_SYNC_DEFERRED")
        return detail

    async def confirm(self, owner_id: UUID, token: str, session_id: str,
                      body: ConfirmDiaryRequest) -> DiaryDetail:
        if isinstance(self.store, SessionStore) and session_id in self.store.sessions:
            self.store.owned_session(owner_id, session_id)
        fingerprint = sha256(json.dumps(
            {"session_id": session_id, **body.model_dump()},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        # 중복 요청 확인: 재시작 후에도 회원 JWT로 기존 결과 복구
        previous = await self.repository.find_request(owner_id, token, body.client_request_id)
        if previous is not None:
            diary_id, saved_fingerprint = previous
            if fingerprint != saved_fingerprint:
                if isinstance(self.store, PersistentSessionStore):
                    await self.store.repository.check_existing_owner(owner_id, session_id)
                raise AppError(409, "IDEMPOTENCY_CONFLICT", "요청 내용 불일치")
            return await self._confirmed_detail(owner_id, token, diary_id)

        if isinstance(self.store, PersistentSessionStore):
            diary_id = await self.store.confirm(owner_id, session_id, body, fingerprint)
            return await self._confirmed_detail(owner_id, token, diary_id)

        # 회원 소유권 확인
        session = self.store.owned_session(owner_id, session_id)
        if session.busy is not None:
            raise AppError(409, "SESSION_BUSY", "세션 처리 중", True)
        if session.history_revision != body.history_revision:
            raise AppError(409, "HISTORY_REVISION_CONFLICT", "대화 버전 충돌")
        # 확정 초안 검증: 기존 AI 검증을 통과한 세션 결과만 사용
        draft = next((record.result for record in session.diary_requests.values()
                      if isinstance(record.result, DiaryDraftResponse)
                      and record.result.draft.draft_id == body.draft_id), None)
        if draft is None:
            raise AppError(404, "DIARY_DRAFT_NOT_FOUND", "초안 없음")
        if draft.history_revision != body.history_revision:
            raise AppError(409, "HISTORY_REVISION_CONFLICT", "대화 버전 충돌")
        reservation = RequestRecord(fingerprint)
        session.busy = reservation
        try:
            # 원본 대화 저장
            diary_id = await self.repository.save({
                "p_user_id": str(owner_id), "p_session_id": session_id,
                "p_client_request_id": body.client_request_id,
                "p_history_revision": body.history_revision,
                "p_fingerprint": fingerprint, "p_weather": body.weather,
                "p_confirmed_at": datetime.now(timezone.utc).isoformat(),
                "p_ai_draft": draft.draft.model_dump(mode="json"),
                "p_messages": [{"position": position, **message.model_dump()}
                               for position, message in enumerate(session.messages)],
            })
            return await self._confirmed_detail(owner_id, token, diary_id)
        finally:
            if session.busy is reservation:
                session.busy = None
