import asyncio

import httpx
import pytest

from app.config import Settings

pytestmark = pytest.mark.anyio


async def start(client, key="start-1"):
    return await client.post("/api/chat-sessions", json={}, headers={"Idempotency-Key": key})


async def send(client, session_id, key="turn-1", text="가상 공놀이 이야기"):
    return await client.post(f"/api/chat-sessions/{session_id}/messages", json={
        "client_message_id": key, "text": text,
    })


def assert_error(response, status, code, retryable=False):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["retryable"] is retryable
    assert body["request_id"] == response.headers["x-request-id"]
    assert set(body) == {"error", "request_id"}
    assert "secret" not in response.text


async def test_start_two_round_trips_full_history_and_replay(api_factory):
    async with api_factory() as (client, service, ai):
        first = await start(client)
        assert first.status_code == 201
        assert set(first.json()) == {"session_id", "reply", "finished", "mock", "fallback"}
        session_id = first.json()["session_id"]
        replay = await start(client)
        assert replay.status_code == 201 and replay.json() == first.json()
        assert replay.headers["x-request-id"] != first.headers["x-request-id"]
        turn1 = await send(client, session_id, text="  가상 이야기\n")
        turn2 = await send(client, session_id, "turn-2", "가상 다음 이야기")
        assert turn1.status_code == turn2.status_code == 200
        duplicate = await send(client, session_id, text="  가상 이야기\n")
        assert duplicate.json() == turn1.json()
        assert len(ai.requests) == 3
        first_history = [{"role": "assistant", "text": first.json()["reply"]["text"]},
                         {"role": "child", "text": "  가상 이야기\n"}]
        assert ai.requests == [
            {"messages": []}, {"messages": first_history},
            {"messages": [*first_history,
                          {"role": "assistant", "text": turn1.json()["reply"]["text"]},
                          {"role": "child", "text": "가상 다음 이야기"}]},
        ]
        assert len(service.store.sessions[session_id].messages) == 5
        assert turn1.json()["client_message_id"] == "turn-1"
        assert turn1.json()["reply"]["role"] == "assistant"
        assert turn1.json()["mock"] is True
        assert_error(await send(client, session_id, text="다른 원문"),
                     409, "IDEMPOTENCY_CONFLICT")


async def test_sessions_are_separate_and_same_text_with_new_id_is_allowed(api_factory):
    async with api_factory() as (client, service, ai):
        first = (await start(client, "one")).json()
        second = (await start(client, "two")).json()
        assert first["session_id"] != second["session_id"]
        for session in [first, second]:
            assert (await send(client, session["session_id"])).status_code == 200
        assert len(ai.requests[-1]["messages"]) == 2
        assert ai.requests[-1]["messages"][0]["text"] == second["reply"]["text"]
        assert (await send(client, first["session_id"], "new-id")).status_code == 200
        assert len(service.store.sessions[first["session_id"]].messages) == 5
        assert len(service.store.sessions[second["session_id"]].messages) == 3


@pytest.mark.parametrize("body", [
    {}, {"client_message_id": "id"},
    {"client_message_id": "id", "text": ""},
    {"client_message_id": "id", "text": " \n\t\u3000"},
    {"client_message_id": "id", "text": "가" * 2001},
    {"client_message_id": "id", "text": 123},
    {"client_message_id": "id", "text": None},
    {"client_message_id": 123, "text": "가상 이야기"},
    {"client_message_id": "공백 불가", "text": "가상 이야기"},
    {"client_message_id": "a" * 129, "text": "가상 이야기"},
    {"client_message_id": "", "text": "가상 이야기"},
    {"client_message_id": "id", "text": "가상 이야기", "role": "assistant"},
    {"client_message_id": "id", "text": "가상 이야기", "messages": []},
])
async def test_invalid_message_does_not_call_ai(api_factory, body):
    async with api_factory() as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]
        response = await client.post(f"/api/chat-sessions/{session_id}/messages", json=body)
        assert_error(response, 422, "VALIDATION_ERROR")
        assert len(ai.requests) == 1
        assert len(service.store.sessions[session_id].messages) == 1


@pytest.mark.parametrize("key", [None, "", "space key", "한글", "a" * 129, "bad.key"])
async def test_invalid_start_key(api_factory, key):
    async with api_factory() as (client, service, ai):
        # HTTP 헤더의 비ASCII는 실제 전송할 수 없으므로 원시 bytes로 검증합니다.
        headers = {} if key is None else {b"Idempotency-Key": key.encode("utf-8")}
        response = await client.post("/api/chat-sessions", json={}, headers=headers)
        assert_error(response, 422, "VALIDATION_ERROR")
        assert not ai.requests and not service.store.starts


async def test_bad_json_empty_body_extra_fields_and_unknown_session(api_factory):
    async with api_factory() as (client, service, ai):
        for content in [b"", b"{", b"[]", b'{"user_id":"fake"}']:
            response = await client.post("/api/chat-sessions", content=content, headers={
                "Idempotency-Key": "id", "Content-Type": "application/json",
            })
            assert_error(response, 422, "VALIDATION_ERROR")
        assert_error(await send(client, "unknown"), 404, "SESSION_NOT_FOUND")
        assert not ai.requests
        session_id = (await start(client)).json()["session_id"]
        response = await client.post(f"/api/chat-sessions/{session_id}/messages", content=
            b'{"client_message_id":"id","text":"\\ud800"}',
            headers={"Content-Type": "application/json"})
        assert_error(response, 422, "VALIDATION_ERROR")


async def test_unicode_2000_code_points_and_count_limit_with_cached_replay(api_factory):
    async with api_factory() as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]
        assert (await send(client, session_id, text="😀" * 2000)).status_code == 200
        assert len(ai.requests[-1]["messages"][-1]["text"]) == 2000
        last = None
        for index in range(2, 30):
            last = await send(client, session_id, f"turn-{index}", "가")
            assert last.status_code == 200
        assert len(service.store.sessions[session_id].messages) == 59
        assert_error(await send(client, session_id, "overflow", "가"),
                     422, "CONVERSATION_LIMIT_REACHED")
        replay = await send(client, session_id, "turn-29", "가")
        assert replay.json() == last.json()
        assert len(ai.requests) == 30


async def test_total_length_boundary_reserves_150_for_reply(api_factory):
    async with api_factory() as (client, service, ai):
        ai.outcomes.append(httpx.Response(200, json={
            "question": "가" * 150, "finished": False, "mock": True, "fallback": False,
        }))
        session_id = (await start(client)).json()["session_id"]
        for index in range(9):
            ai.outcomes.append(httpx.Response(200, json={
                "question": "가" * 150, "finished": False, "mock": True, "fallback": False,
            }))
            assert (await send(client, session_id, f"long-{index}", "가" * 2000)).status_code == 200
        assert sum(len(m.text) for m in service.store.sessions[session_id].messages) == 19500
        assert_error(await send(client, session_id, "edge", "가" * 351),
                     422, "CONVERSATION_LIMIT_REACHED")
        ai.outcomes.append(httpx.Response(200, json={
            "question": "가" * 150, "finished": True, "mock": True, "fallback": True,
        }))
        edge = await send(client, session_id, "edge", "가" * 350)
        assert edge.status_code == 200
        assert edge.json()["finished"] is edge.json()["fallback"] is True
        assert sum(len(m.text) for m in service.store.sessions[session_id].messages) == 20000
        assert (await send(client, session_id, "edge", "가" * 350)).json() == edge.json()
        assert_error(await send(client, session_id, "beyond", "가"),
                     422, "CONVERSATION_LIMIT_REACHED")


async def test_finished_is_forwarded_without_closing_session(api_factory):
    async with api_factory() as (client, service, ai):
        ai.outcomes.append(httpx.Response(200, json={
            "question": "가상 종료 응답", "finished": True, "mock": True, "fallback": True,
        }))
        first = (await start(client)).json()
        assert first["finished"] is first["fallback"] is True
        assert (await send(client, first["session_id"])).status_code == 200


async def test_start_and_message_in_progress_and_other_session_independence(api_factory):
    async with api_factory() as (client, service, ai):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"question": "가상 지연 응답", "finished": False,
                                             "mock": True, "fallback": False})

        ai.outcomes.append(delayed)
        task = asyncio.create_task(start(client))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            assert_error(await start(client), 409, "REQUEST_IN_PROGRESS", True)
            other = (await start(client, "other")).json()["session_id"]
        finally:
            release.set()
        session_id = (await task).json()["session_id"]
        entered.clear()
        release.clear()
        ai.outcomes.append(delayed)
        task = asyncio.create_task(send(client, session_id))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            assert_error(await send(client, session_id), 409, "REQUEST_IN_PROGRESS", True)
            assert_error(await send(client, session_id, text="다른 원문"),
                         409, "IDEMPOTENCY_CONFLICT")
            assert_error(await send(client, session_id, "other-turn"), 409, "SESSION_BUSY", True)
            assert (await send(client, other)).status_code == 200
        finally:
            release.set()
        assert (await task).status_code == 200
        assert len(ai.requests) == 4


async def test_total_deadline_keeps_history_and_caches_unknown_outcome(api_factory):
    async with api_factory(Settings(ai_request_timeout_seconds=0.02)) as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]

        async def delayed(request):
            await asyncio.Event().wait()

        ai.outcomes.append(delayed)
        response = await send(client, session_id)
        assert_error(response, 504, "AI_OUTCOME_UNKNOWN")
        assert_error(await send(client, session_id), 504, "AI_OUTCOME_UNKNOWN")
        assert len(ai.requests) == 2
        assert len(service.store.sessions[session_id].messages) == 1
        assert service.store.sessions[session_id].busy is None
        assert (await send(client, session_id, "new-turn")).status_code == 200
        assert len(ai.requests[-1]["messages"]) == 2


async def test_lifespan_restart_loses_sessions_and_start_cache(api_factory):
    async with api_factory() as (client, service, ai):
        session_id = (await start(client)).json()["session_id"]
    async with api_factory() as (client, service, ai):
        assert_error(await send(client, session_id), 404, "SESSION_NOT_FOUND")
        new_id = (await start(client)).json()["session_id"]
        assert new_id != session_id
