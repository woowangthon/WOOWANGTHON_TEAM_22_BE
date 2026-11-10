import asyncio
import inspect
from uuid import UUID, uuid4

from app.clients.ai import AIClient, outcome_unknown
from app.errors import AppError
from app.schemas import (
    AIMessage, DiaryDraft, DiaryDraftRequest, DiaryDraftResponse,
    MessageRequest, MessageResponse, Reply, SessionResponse,
)
from app.session_store import SessionStore
from app.persistent_session_store import PersistentSessionStore


def internal_error() -> AppError:
    return AppError(500, "INTERNAL_ERROR", "서버에서 요청을 처리하지 못했어요.")


class ChatService:
    def __init__(self, store: SessionStore | PersistentSessionStore, ai: AIClient):
        self.store = store
        self.ai = ai

    async def _store(self, method, *args):
        value = getattr(self.store, method)(*args)
        return await value if inspect.isawaitable(value) else value

    async def _dispatch(self, record):
        if hasattr(self.store, "dispatch"):
            await self.store.dispatch(record)

    async def _cancel(self, record, session=None):
        try:
            await self._store("fail", record, outcome_unknown(), session)
        except AppError:
            # DB 장애여도 전송 기록은 남고 기한 뒤 unknown으로 전환됩니다.
            pass

    async def start(self, owner_id: UUID, key: str) -> SessionResponse:
        record = await self._store("reserve_start", owner_id, key)
        if record.result is not None:
            return record.result
        await self._dispatch(record)
        try:
            ai_reply = await self.ai.chat([])
            result = SessionResponse(
                session_id=getattr(record, "session_id", None) or f"ses_{uuid4().hex}",
                history_revision=0,
                reply=Reply(message_id=f"msg_{uuid4().hex}", text=ai_reply.question),
                **ai_reply.model_dump(exclude={"question"}),
            )
        except asyncio.CancelledError:
            await self._cancel(record)
            raise
        except AppError as error:
            await self._store("fail", record, error)
            raise
        except Exception as error:
            failure = outcome_unknown() if hasattr(self.store, "dispatch") else internal_error()
            await self._store("fail", record, failure)
            raise failure from error
        # 완료 응답 유실을 AI 실패로 덮어쓰거나 메모리 성공으로 반환하지 않습니다.
        await self._store("complete_start", owner_id, key, record, result)
        return result

    async def send(self, owner_id: UUID, session_id: str, body: MessageRequest) -> MessageResponse:
        session, record = await self._store("reserve_message",
            owner_id, session_id, body.client_message_id, body.text,
        )
        if record.result is not None:
            return record.result
        # 확정 이력 스냅샷과 새 발언 전달
        candidate = [*session.messages, AIMessage(role="child", text=body.text)]
        await self._dispatch(record)
        try:
            ai_reply = await self.ai.chat(candidate)
            result = MessageResponse(
                session_id=session_id, client_message_id=body.client_message_id,
                history_revision=session.history_revision + 1,
                reply=Reply(message_id=f"msg_{uuid4().hex}", text=ai_reply.question),
                **ai_reply.model_dump(exclude={"question"}),
            )
        except asyncio.CancelledError:
            await self._cancel(record, session)
            raise
        except AppError as error:
            await self._store("fail", record, error, session)
            raise
        except Exception as error:
            failure = outcome_unknown() if hasattr(self.store, "dispatch") else internal_error()
            await self._store("fail", record, failure, session)
            raise failure from error
        await self._store("complete_message", session, record, body.text, result)
        return result

    async def diary_draft(self, owner_id: UUID, session_id: str,
                          body: DiaryDraftRequest) -> DiaryDraftResponse:
        session, record = await self._store("reserve_diary",
            owner_id, session_id, body.client_request_id, body.history_revision,
        )
        if record.result is not None:
            return record.result
        # 초안 완료까지 같은 세션의 대화·초안 중복 요청 차단
        snapshot = list(session.messages)
        await self._dispatch(record)
        try:
            ai_reply = await self.ai.diary(snapshot)
            result = DiaryDraftResponse(
                session_id=session_id, client_request_id=body.client_request_id,
                history_revision=body.history_revision,
                draft=DiaryDraft(draft_id=f"draft_{uuid4().hex}", **ai_reply.model_dump()),
            )
        except asyncio.CancelledError:
            await self._cancel(record, session)
            raise
        except AppError as error:
            await self._store("fail", record, error, session)
            raise
        except Exception as error:
            failure = outcome_unknown() if hasattr(self.store, "dispatch") else internal_error()
            await self._store("fail", record, failure, session)
            raise failure from error
        await self._store("complete_diary", session, record, result)
        return result
