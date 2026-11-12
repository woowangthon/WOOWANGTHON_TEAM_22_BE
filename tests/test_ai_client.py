import httpx
import pytest

pytestmark = pytest.mark.anyio

GOOD = {"question": "가상 응답", "finished": False, "mock": True, "fallback": False}


@pytest.mark.parametrize("outcome,status,code", [
    (httpx.ReadTimeout("secret"), 504, "AI_OUTCOME_UNKNOWN"),
    (httpx.WriteTimeout("secret"), 504, "AI_OUTCOME_UNKNOWN"),
    (httpx.ReadError("secret"), 502, "AI_OUTCOME_UNKNOWN"),
    (httpx.WriteError("secret"), 502, "AI_OUTCOME_UNKNOWN"),
    (httpx.RemoteProtocolError("secret"), 502, "AI_OUTCOME_UNKNOWN"),
    (httpx.Response(422, json={"detail": "secret"}), 502, "AI_REQUEST_REJECTED"),
    (httpx.Response(401, json={"error": "secret"}), 502, "AI_REQUEST_REJECTED"),
    (httpx.Response(429, json={"error": "secret"}), 503, "AI_UNAVAILABLE"),
    (httpx.Response(503, json={"error": "secret"}), 503, "AI_UNAVAILABLE"),
    (httpx.Response(502, text="secret"), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(302, headers={"Location": "http://external.invalid"}), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(204), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, text="not-json-secret"), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json=[]), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={**GOOD, "question": ""}), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={**GOOD, "question": " \n"}), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={**GOOD, "question": "가" * 151}), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={**GOOD, "question": 123}), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={**GOOD, "finished": "false"}), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={**GOOD, "mock": 1}), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={**GOOD, "fallback": None}), 502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={"question": "가상", "finished": False, "mock": True}),
     502, "AI_BAD_RESPONSE"),
    (httpx.Response(200, json={**GOOD, "extra": "secret"}), 502, "AI_BAD_RESPONSE"),
])
async def test_terminal_failure_cached_and_history_preserved(api_factory, outcome, status, code):
    async with api_factory() as (client, service, ai):
        start = await client.post("/api/chat-sessions", json={},
                                  headers={"Idempotency-Key": "start"})
        session_id = start.json()["session_id"]
        path = f"/api/chat-sessions/{session_id}/messages"
        first_body = {"client_message_id": "first", "text": "가상 확정 발언"}
        assert (await client.post(path, json=first_body)).status_code == 200
        before = list(service.store.sessions[session_id].messages)
        ai.outcomes.append(outcome)
        body = {"client_message_id": "failed", "text": "가상 실패 발언"}
        failed = await client.post(path, json=body)
        replay = await client.post(path, json=body)
        assert failed.status_code == replay.status_code == status
        assert failed.json()["error"]["code"] == replay.json()["error"]["code"] == code
        assert failed.json()["error"]["retryable"] is False
        assert failed.json()["request_id"] == failed.headers["x-request-id"]
        assert "secret" not in failed.text
        assert len(ai.requests) == 3
        assert service.store.sessions[session_id].messages == before
        assert service.store.sessions[session_id].busy is None
        # 기존 성공 캐시는 실패 이후에도 그대로 복구됩니다.
        assert (await client.post(path, json=first_body)).status_code == 200
        assert len(ai.requests) == 3
        following = await client.post(path, json={
            "client_message_id": "following", "text": "가상 다음 발언",
        })
        assert following.status_code == 200
        assert ai.requests[-1]["messages"] == [
            *[m.model_dump() for m in before], {"role": "child", "text": "가상 다음 발언"},
        ]


@pytest.mark.parametrize("error", [httpx.ConnectError("secret"),
                                  httpx.ConnectTimeout("secret"),
                                  httpx.PoolTimeout("secret")])
async def test_pre_send_failure_allows_explicit_same_id_retry(api_factory, error):
    async with api_factory() as (client, service, ai):
        ai.outcomes.append(error)
        headers = {"Idempotency-Key": "start", "Origin": "http://localhost:5500"}
        failed = await client.post("/api/chat-sessions", json={}, headers=headers)
        assert failed.status_code == 503
        assert failed.json()["error"] == {
            "code": "AI_CONNECTION_FAILED", "message": "AI 서버에 연결하지 못했어요.",
            "retryable": True,
        }
        assert failed.headers["access-control-allow-origin"] == "http://localhost:5500"
        assert not service.store.sessions
        session_id = (await client.post("/api/chat-sessions", json={}, headers=headers)).json()["session_id"]
        ai.outcomes.append(error)
        path = f"/api/chat-sessions/{session_id}/messages"
        body = {"client_message_id": "retry", "text": "가상 발언"}
        assert (await client.post(path, json=body)).status_code == 503
        assert len(service.store.sessions[session_id].messages) == 1
        conflict = await client.post(path, json={**body, "text": "다른 원문"})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert (await client.post(path, json=body)).status_code == 200
        assert len(ai.requests) == 4


async def test_failed_start_does_not_publish_session_or_repeat_ai(api_factory):
    async with api_factory() as (client, service, ai):
        ai.outcomes.append(httpx.Response(200, json={"invalid": "secret"}))
        for _ in range(2):
            response = await client.post("/api/chat-sessions", json={},
                                         headers={"Idempotency-Key": "failed"})
            assert response.status_code == 502
        assert not service.store.sessions
        assert len(ai.requests) == 1
        assert (await client.post("/api/chat-sessions", json={},
                                  headers={"Idempotency-Key": "new"})).status_code == 201


async def test_http_client_is_closed_after_lifespan(api_factory):
    async with api_factory() as (client, service, ai):
        http = service.ai.http
        assert not http.is_closed
    assert http.is_closed
