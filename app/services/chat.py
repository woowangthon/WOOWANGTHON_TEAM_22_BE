import asyncio
from uuid import uuid4

from app.clients.ai import AIClient, outcome_unknown
from app.errors import AppError
from app.schemas import (
    AIMessage, DiaryDraft, DiaryDraftRequest, DiaryDraftResponse,
    MessageRequest, MessageResponse, Reply, SessionResponse,
)
from app.session_store import SessionStore


def internal_error() -> AppError:
    return AppError(500, "INTERNAL_ERROR", "서버에서 요청을 처리하지 못했어요.")


class ChatService:
    def __init__(self, store: SessionStore, ai: AIClient):
        self.store = store
        self.ai = ai

    async def start(self, key: str) -> SessionResponse:
        record = self.store.reserve_start(key)
        if record.result is not None:
            return record.result
        try:
            ai_reply = await self.ai.chat([])
            result = SessionResponse(
                session_id=f"ses_{uuid4().hex}",
                history_revision=0,
                reply=Reply(message_id=f"msg_{uuid4().hex}", text=ai_reply.question),
                **ai_reply.model_dump(exclude={"question"}),
            )
            self.store.complete_start(key, record, result)
            return result
        except asyncio.CancelledError:
            self.store.fail(record, outcome_unknown())
            raise
        except AppError as error:
            self.store.fail(record, error)
            raise
        except Exception as error:
            failure = internal_error()
            self.store.fail(record, failure)
            raise failure from error

    async def send(self, session_id: str, body: MessageRequest) -> MessageResponse:
        session, record = self.store.reserve_message(
            session_id, body.client_message_id, body.text,
        )
        if record.result is not None:
            return record.result
        # AI에는 확정 이력의 스냅샷과 새 발언만 전송합니다.
        candidate = [*session.messages, AIMessage(role="child", text=body.text)]
        try:
            ai_reply = await self.ai.chat(candidate)
            result = MessageResponse(
                session_id=session_id, client_message_id=body.client_message_id,
                history_revision=session.history_revision + 1,
                reply=Reply(message_id=f"msg_{uuid4().hex}", text=ai_reply.question),
                **ai_reply.model_dump(exclude={"question"}),
            )
            self.store.complete_message(session, record, body.text, result)
            return result
        except asyncio.CancelledError:
            self.store.fail(record, outcome_unknown(), session)
            raise
        except AppError as error:
            self.store.fail(record, error, session)
            raise
        except Exception as error:
            failure = internal_error()
            self.store.fail(record, failure, session)
            raise failure from error

    async def diary_draft(self, session_id: str, body: DiaryDraftRequest) -> DiaryDraftResponse:
        session, record = self.store.reserve_diary(
            session_id, body.client_request_id, body.history_revision,
        )
        if record.result is not None:
            return record.result
        # 예약부터 완료까지 같은 세션의 새 대화/초안은 차단됩니다.
        snapshot = list(session.messages)
        try:
            ai_reply = await self.ai.diary(snapshot)
            result = DiaryDraftResponse(
                session_id=session_id, client_request_id=body.client_request_id,
                history_revision=body.history_revision,
                draft=DiaryDraft(draft_id=f"draft_{uuid4().hex}", **ai_reply.model_dump()),
            )
            self.store.complete_diary(session, record, result)
            return result
        except asyncio.CancelledError:
            self.store.fail(record, outcome_unknown(), session)
            raise
        except AppError as error:
            self.store.fail(record, error, session)
            raise
        except Exception as error:
            failure = internal_error()
            self.store.fail(record, failure, session)
            raise failure from error
