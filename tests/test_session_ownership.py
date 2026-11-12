import asyncio
from copy import deepcopy

import httpx
import pytest

from conftest import FakeAuth, MEMBER_A, MEMBER_B
from test_chat_api import assert_error

pytestmark = pytest.mark.anyio


def session_snapshot(session):
    def record_snapshot(record):
        error = record.error
        return (record.fingerprint, record.pending,
                record.result.model_dump() if record.result is not None else None,
                (error.status, error.code, error.message, error.retryable) if error else None)

    return (session.owner_id, session.history_revision, id(session.busy),
            [item.model_dump() for item in session.messages],
            {key: record_snapshot(record) for key, record in session.requests.items()},
            {key: record_snapshot(record) for key, record in session.diary_requests.items()})


async def start(client, member="a", key="same"):
    return await client.post("/api/chat-sessions", json={}, headers={
        "Authorization": f"Bearer fake-member-{member}", "Idempotency-Key": key,
    })


async def send(client, session_id, member="a", key="same", text="가상 회원 이야기"):
    return await client.post(f"/api/chat-sessions/{session_id}/messages", json={
        "client_message_id": key, "text": text,
    }, headers={"Authorization": f"Bearer fake-member-{member}"})


async def draft(client, session_id, member="a", key="same", revision=1):
    return await client.post(f"/api/chat-sessions/{session_id}/diary-draft", json={
        "client_request_id": key, "history_revision": revision,
    }, headers={"Authorization": f"Bearer fake-member-{member}"})


async def test_members_have_separate_sessions_history_and_all_request_namespaces(api_factory):
    async with api_factory() as (client, service, ai):
        first, second = await start(client), await start(client, "b")
        a, b = first.json()["session_id"], second.json()["session_id"]
        assert a != b
        assert service.store.sessions[a].owner_id == MEMBER_A
        assert service.store.sessions[b].owner_id == MEMBER_B
        assert set(service.store.starts) == {(MEMBER_A, "same"), (MEMBER_B, "same")}
        assert (await start(client)).json() == first.json()
        assert (await start(client, "b")).json() == second.json()
        replies = {}
        drafts = {}
        for member, session_id in (("a", a), ("b", b)):
            replies[member] = await send(client, session_id, member, text=f"가상 {member} 첫 이야기")
            assert replies[member].status_code == 200
            second_turn = await send(client, session_id, member, key="second", text=f"가상 {member} 다음 이야기")
            assert second_turn.status_code == 200
            assert second_turn.json()["history_revision"] == 2
            drafts[member] = await draft(client, session_id, member, revision=2)
            assert drafts[member].status_code == 200
            assert drafts[member].json()["draft"]["requires_confirmation"] is True
            assert drafts[member].json()["draft"]["text"] == f"가상 {member} 첫 이야기\n가상 {member} 다음 이야기"
            assert service.store.sessions[session_id].history_revision == 2
            assert len(service.store.sessions[session_id].messages) == 5
        calls = len(ai.requests)
        for member, session_id in (("a", a), ("b", b)):
            assert (await send(client, session_id, member, text=f"가상 {member} 첫 이야기")).json() == replies[member].json()
            assert (await draft(client, session_id, member, revision=2)).json() == drafts[member].json()
        assert len(ai.requests) == calls == 8
        assert_error(await send(client, a, text="바뀐 원문"), 409, "IDEMPOTENCY_CONFLICT")
        assert_error(await draft(client, b, "b", revision=1), 409, "IDEMPOTENCY_CONFLICT")


@pytest.mark.parametrize("owner,other", [("a", "b"), ("b", "a")])
@pytest.mark.parametrize("state", ["new", "cached", "failed", "busy"])
async def test_other_member_cannot_read_cache_or_change_any_session_state(api_factory, owner, other, state):
    async with api_factory() as (client, service, ai):
        session_id = (await start(client, owner)).json()["session_id"]
        assert (await send(client, session_id, owner)).status_code == 200
        entered, release = asyncio.Event(), asyncio.Event()
        pending = None
        if state == "cached":
            assert (await draft(client, session_id, owner)).status_code == 200
        elif state == "failed":
            ai.outcomes.append(httpx.Response(500, text="secret provider error"))
            assert (await draft(client, session_id, owner)).status_code == 502
        elif state == "busy":
            async def delayed(request):
                entered.set()
                await release.wait()
                return httpx.Response(500)

            ai.outcomes.append(delayed)
            pending = asyncio.create_task(draft(client, session_id, owner))
            await asyncio.wait_for(entered.wait(), 2)
        before = session_snapshot(service.store.sessions[session_id])
        starts = deepcopy(service.store.starts)
        calls = len(ai.requests)
        try:
            for request_id in ("same", "new"):
                for response in (
                    await send(client, session_id, other, key=request_id),
                    await draft(client, session_id, other, key=request_id),
                ):
                    assert_error(response, 404, "SESSION_NOT_FOUND")
                    assert response.json()["error"]["message"] == "세션 없음"
            assert_error(await send(client, "unknown", other), 404, "SESSION_NOT_FOUND")
            assert_error(await draft(client, "unknown", other), 404, "SESSION_NOT_FOUND")
            assert session_snapshot(service.store.sessions[session_id]) == before
            assert service.store.starts == starts
            assert len(ai.requests) == calls
        finally:
            release.set()
            if pending is not None:
                await pending


@pytest.mark.parametrize("token", [None, "fake-invalid", "fake-expired"])
async def test_invalid_auth_never_exposes_cached_results_or_mutates_existing_sessions(api_factory, token):
    async with api_factory(authenticated=False) as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]
        await send(client, session_id)
        await draft(client, session_id)
        before = deepcopy(service.store.sessions)
        starts = deepcopy(service.store.starts)
        calls = len(ai.requests)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for path, body in (
            ("/api/chat-sessions", {}),
            (f"/api/chat-sessions/{session_id}/messages", {"client_message_id": "same", "text": "가상 회원 이야기"}),
            (f"/api/chat-sessions/{session_id}/diary-draft", {"client_request_id": "same", "history_revision": 1}),
        ):
            response = await client.post(path, json=body, headers={"Idempotency-Key": "same", **headers})
            assert_error(response, 401, "AUTHENTICATION_FAILED")
        assert service.store.sessions == before and service.store.starts == starts
        assert len(ai.requests) == calls


async def test_pending_start_key_is_scoped_to_member(api_factory):
    async with api_factory() as (client, service, ai):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={
                "question": "가상 질문", "finished": False, "mock": True, "fallback": False,
            })

        ai.outcomes.append(delayed)
        pending = asyncio.create_task(start(client))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            assert_error(await start(client), 409, "REQUEST_IN_PROGRESS", True)
            second = await start(client, "b")
            assert second.status_code == 201
        finally:
            release.set()
        first = await pending
        assert first.json()["session_id"] != second.json()["session_id"]
        assert len(ai.requests) == 2


async def test_auth_server_failure_does_not_touch_owner_history_or_cached_draft(api_factory):
    auth = FakeAuth()
    async with api_factory(auth=auth) as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]
        await send(client, session_id)
        result = await draft(client, session_id)
        before = deepcopy(service.store.sessions)
        calls = len(ai.requests)
        for operation in (lambda: send(client, session_id, key="new"),
                          lambda: draft(client, session_id)):
            auth.outcomes.append(httpx.ConnectError("secret token"))
            assert_error(await operation(), 503, "AUTH_UNAVAILABLE", True)
        assert service.store.sessions == before and len(ai.requests) == calls
        assert (await draft(client, session_id)).json() == result.json()
        assert len(ai.requests) == calls


async def test_member_id_in_body_is_rejected(api_factory):
    async with api_factory() as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]
        for path, body in (
            ("/api/chat-sessions", {}),
            (f"/api/chat-sessions/{session_id}/messages", {"client_message_id": "same", "text": "가상 이야기"}),
            (f"/api/chat-sessions/{session_id}/diary-draft", {"client_request_id": "same", "history_revision": 0}),
        ):
            response = await client.post(path, json={**body, "user_id": str(MEMBER_B)},
                                         headers={"Idempotency-Key": "same"})
            assert_error(response, 422, "VALIDATION_ERROR")
        assert len(ai.requests) == 1
        assert service.store.sessions[session_id].owner_id == MEMBER_A
