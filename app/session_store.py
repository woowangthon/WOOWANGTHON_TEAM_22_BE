from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID

from app.errors import AppError
from app.schemas import (
    AIMessage, MAX_AI_REPLY_LENGTH, MAX_HISTORY_LENGTH, MAX_HISTORY_MESSAGES,
    DiaryDraftResponse, MessageResponse, SessionResponse,
)


@dataclass
class RequestRecord:
    fingerprint: str
    pending: bool = True
    result: SessionResponse | MessageResponse | DiaryDraftResponse | None = None
    error: AppError | None = None


@dataclass
class Session:
    owner_id: UUID
    messages: list[AIMessage] = field(default_factory=list)
    requests: dict[str, RequestRecord] = field(default_factory=dict)
    diary_requests: dict[str, RequestRecord] = field(default_factory=dict)
    history_revision: int = 0
    busy: RequestRecord | None = None


class SessionStore:
    """단일 프로세스·이벤트 루프 전용. await 없는 예약·반영."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.starts: dict[tuple[UUID, str], RequestRecord] = {}

    @staticmethod
    def _existing(records: dict, key: str | tuple[UUID, str], fingerprint: str):
        record = records.get(key)
        if record is None:
            return None
        if record.fingerprint != fingerprint:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "요청 ID 충돌")
        if record.pending:
            raise AppError(409, "REQUEST_IN_PROGRESS", "요청 처리 중", True)
        if record.result is not None:
            return record
        if record.error is not None and not record.error.retryable:
            raise record.error
        # 전송 전 연결 실패만 같은 ID·원문 재시도 허용
        return None

    def reserve_start(self, owner_id: UUID, request_key: str) -> RequestRecord:
        # 회원별 중복 요청 차단
        key = (owner_id, request_key)
        previous = self._existing(self.starts, key, "")
        if previous is not None:
            return previous
        record = RequestRecord("")
        self.starts[key] = record
        return record

    def owned_session(self, owner_id: UUID, session_id: str) -> Session:
        # 캐시 조회·예약 이전 세션 소유권 확인
        session = self.sessions.get(session_id)
        if session is None or session.owner_id != owner_id:
            raise AppError(404, "SESSION_NOT_FOUND", "세션 없음")
        return session

    def reserve_message(self, owner_id: UUID, session_id: str, key: str, text: str):
        session = self.owned_session(owner_id, session_id)
        fingerprint = sha256(text.encode("utf-8")).hexdigest()
        previous = self._existing(session.requests, key, fingerprint)
        if previous is not None:
            return session, previous
        if session.busy is not None:
            raise AppError(409, "SESSION_BUSY", "세션 처리 중", True)
        if (len(session.messages) + 2 > MAX_HISTORY_MESSAGES
                or sum(len(item.text) for item in session.messages)
                + len(text) + MAX_AI_REPLY_LENGTH > MAX_HISTORY_LENGTH):
            raise AppError(422, "CONVERSATION_LIMIT_REACHED", "대화 한도 초과")
        record = RequestRecord(fingerprint)
        session.requests[key] = record
        session.busy = record
        return session, record

    def reserve_diary(self, owner_id: UUID, session_id: str, key: str, history_revision: int):
        session = self.owned_session(owner_id, session_id)
        fingerprint = str(history_revision)
        previous = self._existing(session.diary_requests, key, fingerprint)
        if previous is not None:
            return session, previous
        if session.busy is not None:
            raise AppError(409, "SESSION_BUSY", "세션 처리 중", True)
        if session.history_revision != history_revision:
            raise AppError(409, "HISTORY_REVISION_CONFLICT", "대화 버전 충돌")
        if not any(item.role == "child" for item in session.messages):
            raise AppError(422, "NO_CHILD_MESSAGES", "대화 이력 없음")
        record = RequestRecord(fingerprint)
        session.diary_requests[key] = record
        session.busy = record
        return session, record

    def complete_start(self, owner_id: UUID, request_key: str,
                       record: RequestRecord, result: SessionResponse):
        key = (owner_id, request_key)
        if self.starts.get(key) is not record or not record.pending:
            raise RuntimeError("시작 예약 오류")
        self.sessions[result.session_id] = Session(owner_id=owner_id, messages=[
            AIMessage(role="assistant", text=result.reply.text),
        ])
        record.result = result
        record.pending = False

    @staticmethod
    def complete_message(session: Session, record: RequestRecord, text: str,
                         result: MessageResponse):
        if session.busy is not record or not record.pending:
            raise RuntimeError("메시지 예약 오류")
        session.messages.extend([
            AIMessage(role="child", text=text),
            AIMessage(role="assistant", text=result.reply.text),
        ])
        session.history_revision += 1
        record.result = result
        record.pending = False
        session.busy = None

    @staticmethod
    def complete_diary(session: Session, record: RequestRecord, result: DiaryDraftResponse):
        if session.busy is not record or not record.pending:
            raise RuntimeError("초안 예약 오류")
        # 재전송 캐시만 반영, 확정 이력·버전·사용자 확인 상태 보존
        record.result = result
        record.pending = False
        session.busy = None

    @staticmethod
    def fail(record: RequestRecord, error: AppError, session: Session | None = None):
        record.error = error
        record.pending = False
        if session is not None and session.busy is record:
            session.busy = None
