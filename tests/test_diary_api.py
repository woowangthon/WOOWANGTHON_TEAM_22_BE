import asyncio

import httpx
import pytest

from app.config import Settings
from app.schemas import DiaryDraftRequest
from tests.test_chat_api import assert_error, send, start

pytestmark = pytest.mark.anyio


async def prepare(client, key="start"):
    session = (await start(client, key)).json()
    session_id = session["session_id"]
    assert session["history_revision"] == 0
    response = await send(client, session_id, text="  가상 공놀이\n이야기  ")
    assert response.json()["history_revision"] == 1
    return session_id


async def draft(client, session_id, key="draft-1", revision=1):
    return await client.post(f"/api/chat-sessions/{session_id}/diary-draft", json={
        "client_request_id": key, "history_revision": revision,
    })


async def test_draft_snapshot_full_history_public_response_replay_and_new_revision(api_factory):
    async with api_factory() as (client, service, ai):
        session_id = await prepare(client)
        assert (await send(client, session_id, "turn-2", "가상으로 즐거웠어.")).json()["history_revision"] == 2
        session = service.store.sessions[session_id]
        before = list(session.messages)
        result = await draft(client, session_id, revision=2)
        assert result.status_code == 200, result.text
        body = result.json()
        assert set(body) == {"session_id", "client_request_id", "history_revision", "draft"}
        assert body["session_id"] == session_id
        assert body["client_request_id"] == "draft-1"
        assert body["history_revision"] == 2
        data = body["draft"]
        assert set(data) == {"draft_id", "status", "text", "pages", "sources", "fallback",
                             "requires_confirmation", "mock"}
        assert data["status"] == "draft" and data["draft_id"]
        assert data["requires_confirmation"] is data["fallback"] is data["mock"] is True
        assert data["text"] == "  가상 공놀이\n이야기  \n가상으로 즐거웠어."
        assert data["pages"] == [{"text": data["text"], "source_ids": [1, 3], "imageUrl": None}]
        assert data["sources"] == [{"id": 1, "text": before[1].text}, {"id": 3, "text": before[3].text}]
        assert ai.paths[-1] == "/api/diary"
        assert ai.requests[-1] == {"messages": [item.model_dump() for item in before]}
        assert session.messages == before and session.history_revision == 2 and session.busy is None
        assert (await draft(client, session_id, revision=2)).json() == body
        assert len(ai.requests) == 4
        # 이후 발언/새 초안이 생겨도 이전 ID 재전송은 그때의 초안을 복구합니다.
        assert (await send(client, session_id, "turn-3", "가상 다음 이야기")).json()["history_revision"] == 3
        assert (await draft(client, session_id, revision=2)).json() == body
        assert_error(await draft(client, session_id, revision=3), 409, "IDEMPOTENCY_CONFLICT")
        assert_error(await draft(client, session_id, "stale", revision=2), 409, "HISTORY_REVISION_CONFLICT")
        updated = await draft(client, session_id, "draft-2", revision=3)
        assert updated.status_code == 200
        assert updated.json()["draft"]["draft_id"] != data["draft_id"]
        assert updated.json()["history_revision"] == 3
        assert len(ai.requests) == 6


async def test_no_child_unknown_session_and_uncommitted_failed_child_rejected(api_factory):
    async with api_factory() as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]
        assert_error(await draft(client, "unknown", revision=0), 404, "SESSION_NOT_FOUND")
        assert_error(await draft(client, session_id, revision=0), 422, "NO_CHILD_MESSAGES")
        ai.outcomes.append(httpx.ReadTimeout("secret"))
        assert (await send(client, session_id)).status_code == 504
        assert_error(await draft(client, session_id, revision=0), 422, "NO_CHILD_MESSAGES")
        assert len(ai.requests) == 2 and "/api/diary" not in ai.paths
        assert len(service.store.sessions[session_id].messages) == 1


@pytest.mark.parametrize("body", [
    {}, {"client_request_id": "id"}, {"history_revision": 1},
    {"client_request_id": "", "history_revision": 1},
    {"client_request_id": "bad key", "history_revision": 1},
    {"client_request_id": "a" * 129, "history_revision": 1},
    {"client_request_id": 1, "history_revision": 1},
    *[{"client_request_id": "id", "history_revision": value} for value in [-1, True, "1", 1.0, None]],
    {"client_request_id": "id", "history_revision": 1, "messages": []},
    {"client_request_id": "id", "history_revision": 1, "confirmed": True},
    {"client_request_id": "id", "history_revision": 1, "text": "FE 작성 일기"},
])
async def test_invalid_draft_request_does_not_reserve_or_call_ai(api_factory, body):
    async with api_factory() as (client, service, ai):
        session_id = await prepare(client)
        response = await client.post(f"/api/chat-sessions/{session_id}/diary-draft", json=body)
        assert_error(response, 422, "VALIDATION_ERROR")
        assert len(ai.requests) == 2
        assert not service.store.sessions[session_id].diary_requests


@pytest.mark.parametrize("operation", ["diary", "message"])
async def test_pending_duplicate_and_bidirectional_busy_collision(api_factory, operation):
    async with api_factory() as (client, service, ai):
        session_id = await prepare(client)
        other = await prepare(client, "other-session")
        entered, release = asyncio.Event(), asyncio.Event()
        before = list(service.store.sessions[session_id].messages)
        cached = (await draft(client, session_id, "cached")).json()

        async def delayed(request):
            entered.set()
            await release.wait()
            if operation == "message":
                return httpx.Response(200, json={"question": "가상 응답", "finished": False,
                                                 "fallback": False, "mock": True})
            return httpx.Response(200, json={
                "text": before[1].text,
                "pages": [{"text": before[1].text, "source_ids": [1], "imageUrl": None}],
                "sources": [{"id": 1, "text": before[1].text}],
                "fallback": True, "requires_confirmation": True, "mock": True,
            })

        ai.outcomes.append(delayed)
        task = asyncio.create_task(draft(client, session_id) if operation == "diary"
                                   else send(client, session_id, "new-turn"))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            assert (await draft(client, session_id, "cached")).json() == cached
            if operation == "diary":
                assert_error(await draft(client, session_id), 409, "REQUEST_IN_PROGRESS", True)
                assert_error(await draft(client, session_id, revision=2), 409, "IDEMPOTENCY_CONFLICT")
                assert_error(await draft(client, session_id, "other-draft"), 409, "SESSION_BUSY", True)
                assert_error(await send(client, session_id, "new-turn"), 409, "SESSION_BUSY", True)
            else:
                assert_error(await draft(client, session_id), 409, "SESSION_BUSY", True)
                assert set(service.store.sessions[session_id].diary_requests) == {"cached"}
            assert service.store.sessions[session_id].messages == before
            assert (await draft(client, other)).status_code == 200
        finally:
            release.set()
        assert (await task).status_code == 200
        if operation == "message":
            assert_error(await draft(client, session_id), 409, "HISTORY_REVISION_CONFLICT")
            assert (await draft(client, session_id, revision=2)).status_code == 200


@pytest.mark.parametrize("outcome,status,code", [
    (httpx.ReadTimeout("secret"), 504, "AI_OUTCOME_UNKNOWN"),
    (httpx.WriteTimeout("secret"), 504, "AI_OUTCOME_UNKNOWN"),
    (httpx.ReadError("secret"), 502, "AI_OUTCOME_UNKNOWN"),
    (httpx.Response(503, json={"error": "secret"}), 503, "AI_UNAVAILABLE"),
    (httpx.Response(422, json={"detail": "secret"}), 502, "AI_REQUEST_REJECTED"),
    (httpx.Response(200, json={"text": ""}), 502, "AI_BAD_RESPONSE"),
    (RuntimeError("secret"), 500, "INTERNAL_ERROR"),
])
async def test_draft_failure_cached_preserves_history_and_previous_draft(api_factory, outcome, status, code):
    async with api_factory() as (client, service, ai):
        session_id = await prepare(client)
        good = (await draft(client, session_id, "good")).json()
        before = list(service.store.sessions[session_id].messages)
        ai.outcomes.append(outcome)
        assert_error(await draft(client, session_id), status, code)
        assert_error(await draft(client, session_id), status, code)
        assert len(ai.requests) == 4
        assert (await draft(client, session_id, "good")).json() == good
        session = service.store.sessions[session_id]
        assert session.messages == before and session.busy is None and session.history_revision == 1
        assert (await send(client, session_id, "following")).status_code == 200
        assert ai.requests[-1]["messages"][:-1] == [item.model_dump() for item in before]


@pytest.mark.parametrize("error", [httpx.ConnectError("secret"), httpx.ConnectTimeout("secret"),
                                  httpx.PoolTimeout("secret")])
async def test_draft_pre_send_failure_explicit_retry_and_revision_conflict(api_factory, error):
    async with api_factory() as (client, service, ai):
        session_id = await prepare(client)
        ai.outcomes.append(error)
        assert_error(await draft(client, session_id), 503, "AI_CONNECTION_FAILED", True)
        assert (await draft(client, session_id)).status_code == 200
        ai.outcomes.append(error)
        assert_error(await draft(client, session_id, "retry-later"), 503, "AI_CONNECTION_FAILED", True)
        assert (await send(client, session_id, "new-turn")).status_code == 200
        assert_error(await draft(client, session_id, "retry-later"), 409, "HISTORY_REVISION_CONFLICT")
        assert_error(await draft(client, session_id, "retry-later", revision=2), 409, "IDEMPOTENCY_CONFLICT")
        assert len(ai.requests) == 6


@pytest.mark.parametrize("cancel", [False, True])
async def test_draft_deadline_or_cancellation_caches_unknown_and_releases_busy(api_factory, cancel):
    async with api_factory(Settings(ai_request_timeout_seconds=1 if cancel else 0.02)) as (client, service, ai):
        session_id = await prepare(client)
        before = list(service.store.sessions[session_id].messages)
        entered = asyncio.Event()

        async def blocked(request):
            entered.set()
            await asyncio.Event().wait()

        ai.outcomes.append(blocked)
        if cancel:
            task = asyncio.create_task(service.diary_draft(session_id, DiaryDraftRequest(
                client_request_id="draft-1", history_revision=1,
            )))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert_error(await draft(client, session_id), 504, "AI_OUTCOME_UNKNOWN")
        assert_error(await draft(client, session_id), 502 if cancel else 504, "AI_OUTCOME_UNKNOWN")
        assert len(ai.requests) == 3
        assert service.store.sessions[session_id].messages == before
        assert service.store.sessions[session_id].busy is None
        assert (await send(client, session_id, "following")).status_code == 200


async def test_draft_namespace_and_restarted_app_loses_drafts(api_factory):
    async with api_factory() as (client, service, ai):
        session_id = await prepare(client)
        # 발언 ID와 초안 ID는 세션 내 각 작업 종류별 namespace입니다.
        assert (await draft(client, session_id, "turn-1")).status_code == 200
    async with api_factory() as (client, service, ai):
        assert_error(await draft(client, session_id, "turn-1"), 404, "SESSION_NOT_FOUND")
        assert not ai.requests


@pytest.mark.parametrize("turns,text", [(29, "가상 이야기"), (9, "가" * 2000)])
async def test_draft_includes_entire_history_at_chat_limits_without_image_text_limit(api_factory, turns, text):
    async with api_factory() as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]
        for index in range(turns):
            assert (await send(client, session_id, f"turn-{index}", text)).status_code == 200
        response = await draft(client, session_id, revision=turns)
        assert response.status_code == 200, response.text
        result = response.json()["draft"]
        assert result["text"] == "\n".join([text] * turns)
        assert [source["id"] for source in result["sources"]] == list(range(1, turns * 2, 2))
        assert ai.requests[-1] == {"messages": [item.model_dump()
                                               for item in service.store.sessions[session_id].messages]}
