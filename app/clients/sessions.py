import asyncio
import json
import re
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import AppError
from app.schemas import (
    AIMessage, AIDiaryReply, DiaryDraftResponse, MessageResponse, SessionResponse,
    AIReply,
    MAX_AI_REPLY_LENGTH, MAX_HISTORY_LENGTH, MAX_HISTORY_MESSAGES, RequestKey,
    validate_text,
)


ERRORS = {
    "SESSION_NOT_FOUND": (404, "세션 없음", False),
    "IDEMPOTENCY_CONFLICT": (409, "요청 ID 충돌", False),
    "REQUEST_IN_PROGRESS": (409, "요청 처리 중", True),
    "SESSION_BUSY": (409, "세션 처리 중", True),
    "HISTORY_REVISION_CONFLICT": (409, "대화 버전 충돌", False),
    "NO_CHILD_MESSAGES": (422, "대화 이력 없음", False),
    "DIARY_DRAFT_NOT_FOUND": (404, "초안 없음", False),
    "CONVERSATION_LIMIT_REACHED": (422, "대화 한도 초과", False),
    "SESSION_LIMIT_REACHED": (422, "세션 저장 한도 초과", False),
    "SESSION_ALREADY_CONFIRMED": (409, "이미 확정된 세션", False),
    "AI_OUTCOME_UNKNOWN": (502, "응답을 확인하지 못했어요. 자동으로 다시 보내지 않습니다.", False),
    "DIARY_DATE_CONFLICT": (409, "날짜 중복", False),
}

SAFE_ERRORS = {
    "AI_CONNECTION_FAILED": ({503}, {"AI 서버에 연결하지 못했어요."}, True),
    "AI_UNAVAILABLE": ({503}, {"AI 서버가 요청을 처리하지 못했어요."}, False),
    "AI_REQUEST_REJECTED": ({502}, {"AI 서버와 요청 계약을 확인해야 해요."}, False),
    "AI_BAD_RESPONSE": ({502}, {"AI 서버 응답 형식이 올바르지 않아요.", "AI 서버 응답을 확인하지 못했어요."}, False),
    "AI_OUTCOME_UNKNOWN": ({502, 504}, {ERRORS["AI_OUTCOME_UNKNOWN"][1]}, False),
    "DIARY_DATE_CONFLICT": ({409}, {"날짜 중복"}, False),
    "IDEMPOTENCY_CONFLICT": ({409}, {"요청 ID 충돌"}, False),
}


def saved_failure(error):
    if (not isinstance(error, dict) or set(error) != {"status", "code", "message", "retryable"}
            or not isinstance(error["code"], str) or error["code"] not in SAFE_ERRORS):
        raise invalid_response()
    statuses, messages, retryable = SAFE_ERRORS[error["code"]]
    if (type(error["status"]) is not int or error["status"] not in statuses
            or not isinstance(error["message"], str) or error["message"] not in messages
            or type(error["retryable"]) is not bool or error["retryable"] != retryable):
        raise invalid_response()
    return AppError(**error)


def unavailable():
    return AppError(503, "SESSION_STORAGE_UNAVAILABLE", "세션 저장소 연결 실패", True)


def invalid_response():
    return AppError(503, "SESSION_STORAGE_INVALID_RESPONSE", "세션 저장소 응답 오류")


class Row(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class Snapshot(Row):
    session_id: str = Field(pattern=r"^ses_[0-9a-f]{32}$")
    owner_id: UUID
    schema_version: Literal[1]
    messages: list[AIMessage]
    history_revision: int = Field(ge=0)
    version: int = Field(ge=0)
    lifecycle: Literal["starting", "active", "confirmed", "expired"]
    active_request_id: UUID | None


class StoredRequest(Row):
    id: UUID
    owner_id: UUID
    session_id: str
    kind: Literal["start", "message", "draft", "confirm"]
    client_key: RequestKey
    fingerprint: str
    input: dict
    base_revision: int = Field(ge=0)
    state: Literal["reserved", "dispatched", "succeeded", "retryable_before_send", "failed", "unknown"]
    attempt_token: UUID
    result: dict | None
    error: dict | None


class Envelope(Row):
    session: Snapshot
    request: StoredRequest
    acquired: bool


class SessionRepository:
    """Secret 전용 RPC. DB 장애 시 메모리 우회·HTTP 재시도 없음."""

    def __init__(self, writer: httpx.AsyncClient | None, timeout_seconds: float,
                 ai_timeout_seconds: float):
        self.writer = writer
        self.timeout_seconds = timeout_seconds
        # AI 전체 제한 + 완료 DB 예산 + 여유. 실제 기한은 DB 시각으로 생성합니다.
        self.dispatch_seconds = max(180, int(ai_timeout_seconds + timeout_seconds + 30) + 1)

    async def _request(self, method, path, **kwargs):
        if self.writer is None:
            raise AppError(503, "SESSION_STORAGE_NOT_CONFIGURED", "세션 저장소 설정 누락")
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.writer.request(method, path, **kwargs)
        except (httpx.HTTPError, TimeoutError):
            raise unavailable() from None
        if not 200 <= response.status_code < 300:
            if response.status_code == 409:
                try:
                    code = response.json().get("details")
                    if isinstance(code, str) and code in ERRORS:
                        raise AppError(ERRORS[code][0], code, *ERRORS[code][1:])
                except (ValueError, AttributeError):
                    pass
            raise unavailable()
        try:
            data = response.json()
        except ValueError:
            raise invalid_response() from None
        return data

    async def rpc(self, name: str, payload: dict):
        return self.check_error(await self._request("POST", "/rest/v1/rpc/" + name, json=payload))

    async def check_existing_owner(self, owner_id, session_id):
        # 003 복구를 유지하면서, 다른 회원 세션에 내 확정 키를 대입해도 404로 통일합니다.
        rows = await self._request("GET", "/rest/v1/chat_sessions", params={
            "select": "owner_id", "session_id": "eq." + session_id, "limit": "2",
        })
        try:
            if not isinstance(rows, list) or len(rows) > 1:
                raise ValueError()
            existing = UUID(rows[0]["owner_id"]) if rows else None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise invalid_response() from None
        if existing is not None and existing != owner_id:
            raise AppError(404, "SESSION_NOT_FOUND", "세션 없음")

    @staticmethod
    def check_error(data):
        if isinstance(data, dict) and "error" in data:
            code = data["error"]
            if not isinstance(code, str) or code not in ERRORS:
                raise invalid_response()
            raise AppError(ERRORS[code][0], code, *ERRORS[code][1:])
        return data

    @staticmethod
    def decode(data, owner_id, session_id, kind, key, fingerprint):
        try:
            envelope = Envelope.model_validate_json(json.dumps(data))
            session, request = envelope.session, envelope.request
            if (session.owner_id != owner_id or request.owner_id != owner_id
                    or request.session_id != session.session_id
                    or (session_id is not None and session.session_id != session_id)
                    or (request.kind, request.client_key, request.fingerprint) != (kind, key, fingerprint)):
                raise ValueError()
            messages = session.messages
            if (len(messages) > MAX_HISTORY_MESSAGES
                    or sum(len(m.text) for m in messages) > MAX_HISTORY_LENGTH
                    or (session.lifecycle == "starting" and (messages or session.history_revision != 0))
                    or (session.lifecycle != "starting" and len(messages) != 2 * session.history_revision + 1)):
                raise ValueError()
            for index, message in enumerate(messages):
                validate_text(message.text)
                if (message.role != ("assistant" if index % 2 == 0 else "child")
                        or len(message.text) > (MAX_AI_REPLY_LENGTH if index % 2 == 0 else 2000)):
                    raise ValueError()
            if envelope.acquired and (request.state != "reserved"
                                      or session.active_request_id != request.id):
                raise ValueError()
            if request.base_revision > session.history_revision:
                raise ValueError()
            if request.state in {"reserved", "dispatched"} and request.error is not None:
                raise ValueError()
            if request.state in {"unknown", "failed", "retryable_before_send"}:
                failure = saved_failure(request.error)
                if ((request.state == "unknown" and failure.code != "AI_OUTCOME_UNKNOWN")
                        or (request.state == "retryable_before_send" and failure.code != "AI_CONNECTION_FAILED")):
                    raise ValueError()
            if request.state == "succeeded":
                if request.result is None or request.error is not None:
                    raise ValueError()
                if kind == "confirm":
                    UUID(request.result["diary_id"])
                else:
                    dto = {"start": SessionResponse, "message": MessageResponse,
                           "draft": DiaryDraftResponse}[kind].model_validate(request.result)
                    if dto.session_id != session.session_id:
                        raise ValueError()
                    if kind == "start" and dto.history_revision != 0:
                        raise ValueError()
                    if kind in {"start", "message"}:
                        AIReply(question=dto.reply.text, finished=dto.finished,
                                mock=dto.mock, fallback=dto.fallback)
                        if not re.fullmatch(r"msg_[0-9a-f]{32}", dto.reply.message_id):
                            raise ValueError()
                    if kind == "message" and (dto.client_message_id != key
                                               or dto.history_revision != request.base_revision + 1):
                        raise ValueError()
                    if kind == "draft":
                        if dto.client_request_id != key or dto.history_revision != request.base_revision:
                            raise ValueError()
                        draft = AIDiaryReply.model_validate(dto.draft.model_dump(exclude={"draft_id", "status"}))
                        expected = [{"id": i, "text": m.text} for i, m in enumerate(messages)
                                    if m.role == "child" and i <= 2 * request.base_revision]
                        if [s.model_dump() for s in draft.sources] != expected:
                            raise ValueError()
                        allowed = {s["id"] for s in expected}
                        if (any(not set(p.source_ids) <= allowed or len(p.source_ids) != len(set(p.source_ids))
                                for p in draft.pages)
                                or " ".join(draft.text.split()) != " ".join(" ".join(p.text for p in draft.pages).split())
                                or not re.fullmatch(r"draft_[0-9a-f]{32}", dto.draft.draft_id)):
                            raise ValueError()
            elif request.result is not None:
                raise ValueError()
            return envelope
        except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
            raise invalid_response() from None

    async def reserve(self, owner_id, session_id, kind, key, fingerprint, input_data):
        data = await self.rpc("reserve_chat_request", {
            "p_owner_id": str(owner_id), "p_session_id": session_id,
            "p_kind": kind, "p_client_key": key, "p_fingerprint": fingerprint,
            "p_input": input_data, "p_attempt_token": str(uuid4()),
        })
        envelope = self.decode(data, owner_id, None if kind == "start" else session_id,
                               kind, key, fingerprint)
        if envelope.request.input != input_data:
            raise invalid_response()
        return envelope

    async def transition(self, record, action, result=None, error=None):
        data = await self.rpc("transition_chat_request", {
            **record.identity(), "p_action": action, "p_result": result,
            "p_error": error, "p_dispatch_seconds": self.dispatch_seconds,
        })
        envelope = self.decode(data, record.owner_id, record.session_id, record.kind,
                               record.key, record.fingerprint)
        if envelope.request.id != record.request_id or envelope.request.attempt_token != record.token:
            raise invalid_response()
        return envelope

    async def confirm(self, record):
        data = await self.rpc("confirm_chat_session_diary", record.identity())
        envelope = self.decode(data, record.owner_id, record.session_id, "confirm",
                               record.key, record.fingerprint)
        if envelope.request.state != "succeeded" or envelope.request.id != record.request_id:
            raise invalid_response()
        return UUID(envelope.request.result["diary_id"])
