from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from app.clients.sessions import SessionRepository, invalid_response, saved_failure
from app.errors import AppError
from app.schemas import DiaryDraftResponse, MessageResponse, SessionResponse
from app.session_store import RequestRecord, Session


@dataclass
class PersistentRecord(RequestRecord):
    owner_id: UUID | None = None
    session_id: str = ""
    request_id: UUID | None = None
    token: UUID | None = None
    version: int = 0
    kind: str = ""
    key: str = ""

    def identity(self):
        return {"p_owner_id": str(self.owner_id), "p_session_id": self.session_id,
                "p_request_id": str(self.request_id), "p_attempt_token": str(self.token),
                "p_version": self.version}


class PersistentSessionStore:
    """DB가 유일한 기준이며 프로세스에는 요청 중인 스냅샷만 둡니다."""

    def __init__(self, repository: SessionRepository):
        self.repository = repository

    async def _reserve(self, owner_id, session_id, kind, key, fingerprint, input_data):
        envelope = await self.repository.reserve(owner_id, session_id, kind, key,
                                                 fingerprint, input_data)
        saved, snapshot = envelope.request, envelope.session
        record = PersistentRecord(
            fingerprint=fingerprint, owner_id=owner_id, session_id=snapshot.session_id,
            request_id=saved.id, token=saved.attempt_token, version=snapshot.version,
            kind=kind, key=key,
        )
        if saved.state == "succeeded":
            record.pending = False
            if kind != "confirm":
                record.result = {"start": SessionResponse, "message": MessageResponse,
                                 "draft": DiaryDraftResponse}[kind].model_validate(saved.result)
            else:
                record.result = saved.result
        elif not envelope.acquired:
            if saved.state in {"unknown", "failed"}:
                raise saved_failure(saved.error)
            raise AppError(409, "REQUEST_IN_PROGRESS", "요청 처리 중", True)
        session = Session(owner_id=owner_id, messages=snapshot.messages,
                          history_revision=snapshot.history_revision)
        return session, record

    async def reserve_start(self, owner_id, request_key):
        _, record = await self._reserve(owner_id, f"ses_{uuid4().hex}", "start", request_key, "", {})
        return record

    async def reserve_message(self, owner_id, session_id, key, text):
        return await self._reserve(owner_id, session_id, "message", key,
                                   sha256(text.encode("utf-8")).hexdigest(), {"text": text})

    async def reserve_diary(self, owner_id, session_id, key, history_revision):
        return await self._reserve(owner_id, session_id, "draft", key, str(history_revision),
                                   {"history_revision": history_revision})

    async def dispatch(self, record):
        envelope = await self.repository.transition(record, "dispatched")
        if (envelope.request.state != "dispatched"
                or envelope.request.attempt_token != record.token
                or envelope.session.version != record.version
                or envelope.session.active_request_id != record.request_id):
            raise invalid_response()

    async def complete_start(self, owner_id, key, record, result):
        await self._complete(record, result)

    async def complete_message(self, session, record, text, result):
        await self._complete(record, result)

    async def complete_diary(self, session, record, result):
        await self._complete(record, result)

    async def _complete(self, record, result):
        envelope = await self.repository.transition(record, "succeeded", result.model_dump(mode="json"))
        if (envelope.request.state != "succeeded" or envelope.request.result != result.model_dump(mode="json")
                or envelope.session.history_revision != result.history_revision):
            raise invalid_response()

    async def fail(self, record, error, session=None):
        state = ("retryable_before_send" if error.code == "AI_CONNECTION_FAILED" else
                 "unknown" if error.code == "AI_OUTCOME_UNKNOWN" else "failed")
        await self.repository.transition(record, state, error={
            "status": error.status, "code": error.code,
            "message": error.message, "retryable": error.retryable,
        })

    async def confirm(self, owner_id, session_id, body, fingerprint):
        _, record = await self._reserve(owner_id, session_id, "confirm", body.client_request_id,
                                         fingerprint, body.model_dump(mode="json"))
        if record.result is not None:
            return UUID(record.result["diary_id"])
        return await self.repository.confirm(record)
