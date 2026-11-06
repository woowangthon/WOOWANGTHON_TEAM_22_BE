from dataclasses import dataclass, field
from hashlib import sha256

from app.errors import AppError
from app.schemas import (
    AIMessage, MAX_AI_REPLY_LENGTH, MAX_HISTORY_LENGTH, MAX_HISTORY_MESSAGES,
    MessageResponse, SessionResponse,
)


@dataclass
class RequestRecord:
    fingerprint: str
    pending: bool = True
    result: SessionResponse | None = None
    error: AppError | None = None


@dataclass
class Session:
    messages: list[AIMessage] = field(default_factory=list)
    requests: dict[str, RequestRecord] = field(default_factory=dict)
    busy: RequestRecord | None = None


class SessionStore:
    """단일 프로세스/이벤트 루프 전용. 예약·반영 중 await하지 않아 원자적으로 처리합니다."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.starts: dict[str, RequestRecord] = {}

    @staticmethod
    def _existing(records: dict[str, RequestRecord], key: str, fingerprint: str):
        record = records.get(key)
        if record is None:
            return None
        if record.fingerprint != fingerprint:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 ID의 내용이 달라요.")
        if record.pending:
            raise AppError(409, "REQUEST_IN_PROGRESS", "같은 요청을 처리 중이에요.", True)
        if record.result is not None:
            return record
        if record.error is not None and not record.error.retryable:
            raise record.error
        # 전송 전 연결 실패만 같은 ID/원문으로 명시적 재시도할 수 있습니다.
        return None

    def reserve_start(self, key: str) -> RequestRecord:
        previous = self._existing(self.starts, key, "")
        if previous is not None:
            return previous
        record = RequestRecord("")
        self.starts[key] = record
        return record

    def reserve_message(self, session_id: str, key: str, text: str):
        session = self.sessions.get(session_id)
        if session is None:
            raise AppError(404, "SESSION_NOT_FOUND", "대화 세션을 찾지 못했어요.")
        fingerprint = sha256(text.encode("utf-8")).hexdigest()
        previous = self._existing(session.requests, key, fingerprint)
        if previous is not None:
            return session, previous
        if session.busy is not None:
            raise AppError(409, "SESSION_BUSY", "이 대화의 이전 답변을 기다려 주세요.", True)
        if (len(session.messages) + 2 > MAX_HISTORY_MESSAGES
                or sum(len(item.text) for item in session.messages)
                + len(text) + MAX_AI_REPLY_LENGTH > MAX_HISTORY_LENGTH):
            raise AppError(422, "CONVERSATION_LIMIT_REACHED", "대화 이력 한도에 도달했어요.")
        record = RequestRecord(fingerprint)
        session.requests[key] = record
        session.busy = record
        return session, record

    def complete_start(self, key: str, record: RequestRecord, result: SessionResponse):
        if self.starts.get(key) is not record or not record.pending:
            raise RuntimeError("유효하지 않은 시작 예약입니다.")
        self.sessions[result.session_id] = Session(messages=[
            AIMessage(role="assistant", text=result.reply.text),
        ])
        record.result = result
        record.pending = False

    @staticmethod
    def complete_message(session: Session, record: RequestRecord, text: str,
                         result: MessageResponse):
        if session.busy is not record or not record.pending:
            raise RuntimeError("유효하지 않은 메시지 예약입니다.")
        session.messages.extend([
            AIMessage(role="child", text=text),
            AIMessage(role="assistant", text=result.reply.text),
        ])
        record.result = result
        record.pending = False
        session.busy = None

    @staticmethod
    def fail(record: RequestRecord, error: AppError, session: Session | None = None):
        record.error = error
        record.pending = False
        if session is not None and session.busy is record:
            session.busy = None
