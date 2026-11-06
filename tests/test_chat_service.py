import asyncio

import pytest

from app.errors import AppError
from app.schemas import MessageRequest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("is_start", [True, False])
async def test_cancellation_releases_reservation_without_committing(api_factory, is_start):
    async with api_factory() as (client, service, ai):
        session_id = (await service.start("prepared")).session_id
        before = list(service.store.sessions[session_id].messages)
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        ai.outcomes.append(blocked)
        body = MessageRequest(client_message_id="cancelled", text="가상 취소 발언")

        async def operation():
            return await service.start("cancelled") if is_start else await service.send(session_id, body)

        task = asyncio.create_task(operation())
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert service.store.sessions[session_id].messages == before
        assert service.store.sessions[session_id].busy is None
        assert len(service.store.sessions) == 1
        with pytest.raises(AppError) as error:
            await operation()
        assert error.value.code == "AI_OUTCOME_UNKNOWN"
        assert error.value.retryable is False
        assert len(ai.requests) == 2
        assert (await service.send(session_id, MessageRequest(
            client_message_id="next", text="가상 다음 발언",
        ))).reply.text


async def test_unexpected_error_is_cached_and_does_not_leave_busy(api_factory):
    async with api_factory() as (client, service, ai):
        session_id = (await service.start("prepared")).session_id
        ai.outcomes.append(RuntimeError("secret"))
        body = MessageRequest(client_message_id="failed", text="가상 발언")
        for _ in range(2):
            with pytest.raises(AppError) as error:
                await service.send(session_id, body)
            assert error.value.code == "INTERNAL_ERROR"
        assert service.store.sessions[session_id].busy is None
        assert len(service.store.sessions[session_id].messages) == 1
        assert len(ai.requests) == 2
