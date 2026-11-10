import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import socket
from uuid import UUID

import httpx
import pytest

from app.clients.sessions import SessionRepository
from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.schemas import MessageRequest
from tests.conftest import FakeAI, FakeAuth, MEMBER_A, TEST_PUBLISHABLE_KEY
from tests.fake_sessions import FakeSessionRepository
from tests.test_drawings import DrawingStorage, picture
from tests.test_saved_diaries import FAKE_SECRET, assert_error, prepare

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def forbid_network(monkeypatch, anyio_backend):
    def blocked(*args, **kwargs):
        raise AssertionError("오프라인 검증: 실제 네트워크 금지")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


@asynccontextmanager
async def persistent_api(repo=None, storage=None, ai=None):
    repo, storage, ai = repo or FakeSessionRepository(), storage or DrawingStorage(), ai or FakeAI()
    async def mock_ai(request):
        assert "apikey" not in request.headers and "authorization" not in request.headers
        return await ai(request)
    app = create_app(Settings(session_store_mode="supabase", supabase_secret_key=FAKE_SECRET,
                              supabase_publishable_key=TEST_PUBLISHABLE_KEY),
                     httpx.MockTransport(mock_ai), httpx.MockTransport(FakeAuth()),
                     httpx.MockTransport(repo.db), httpx.MockTransport(storage), repo)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://be.test",
        headers={"Authorization": "Bearer fake-member-a"},
    ) as client:
        yield client, app.state.chat_service, repo, ai


async def start(client, key="start"):
    response = await client.post("/api/chat-sessions", json={}, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


async def test_restart_complete_flow_replays_and_drawing_regression():
    repo, storage = FakeSessionRepository(), DrawingStorage()
    async with persistent_api(repo, storage) as (client, service, _, ai):
        first = await start(client)
        sid = first["session_id"]
        base = f"/api/chat-sessions/{sid}"
        original = []
        for i in range(2):
            body = {"client_message_id": f"message-{i}", "text": f"가상 발언 {i} 원문 보존"}
            result = await client.post(base + "/messages", json=body)
            assert result.status_code == 200
            original.append((body, result.json()))
        draft_body = {"client_request_id": "draft", "history_revision": 2}
        draft = (await client.post(base + "/diary-draft", json=draft_body)).json()
        assert draft["history_revision"] == 2
        before = deepcopy(repo.sessions[sid])
        assert len(ai.requests) == 4
    # 새 앱/새 SessionStore/새 AI. 저장소만 공유하므로 프로세스 캐시로 복구할 수 없습니다.
    async with persistent_api(repo, storage) as (client, service, _, ai):
        assert await start(client) == first
        for body, result in original:
            assert (await client.post(base + "/messages", json=body)).json() == result
        assert (await client.post(base + "/diary-draft", json=draft_body)).json() == draft
        assert repo.sessions[sid] == before and not ai.requests
        assert_error(await client.post(base + "/messages", json={**original[0][0], "text": "다른 원문"}),
                     409, "IDEMPOTENCY_CONFLICT")
        confirm = {"client_request_id": "confirm", "history_revision": 2,
                   "draft_id": draft["draft"]["draft_id"], "weather": "sunny"}
        saved = await client.post(base + "/diaries", json=confirm)
        assert saved.status_code == 200, saved.text
        detail = saved.json()
        assert [m["text"] for m in detail["messages"] if m["role"] == "child"] == [b["text"] for b, _ in original]
        assert detail["ai_draft"] == draft["draft"]
        assert repo.sessions[sid]["lifecycle"] == "confirmed"
        did = detail["diary_id"]
        image = picture()
        uploaded = await client.post(f"/api/diaries/{did}/drawing", files={"file": ("fake.png", image, "image/png")})
        assert uploaded.status_code == 200, uploaded.text
        repeated_upload = await client.post(f"/api/diaries/{did}/drawing", files={"file": ("fake.png", image)})
        assert repeated_upload.status_code == 200
        # 재발급되는 URL/만료 시각은 영구 자산 메타데이터와 구분합니다.
        permanent = lambda asset: {k: v for k, v in asset.items() if k not in {"imageUrl", "expires_at"}}
        assert permanent(repeated_upload.json()["asset"]) == permanent(uploaded.json()["asset"])
        assert len(storage.objects) == 1
        assert_error(await client.post(base + "/messages", json={"client_message_id": "after", "text": "확정 후"}),
                     409, "SESSION_ALREADY_CONFIRMED")
        assert not ai.requests
    async with persistent_api(repo, storage) as (client, _, _, ai):
        repeated = await client.post(base + "/diaries", json=confirm)
        assert repeated.status_code == 200
        assert repeated.json()["diary_id"] == did
        assert repeated.json()["messages"] == detail["messages"]
        assert len(repo.db.diaries) == 1
        assert (await client.get(f"/api/diaries/{did}")).json()["assets"]
        assert (await client.get("/api/diaries", params={"month": detail["diary_date"][:7]})).status_code == 200
        assert not ai.requests


@pytest.mark.parametrize("stage", ["start", "message", "draft"])
@pytest.mark.parametrize("failure", ["timeout", "cancel", "crash"])
async def test_unknown_survives_restart_and_blocks_new_ids(stage, failure):
    repo = FakeSessionRepository()
    async with persistent_api(repo) as (client, service, _, ai):
        if stage != "start":
            url, body, draft = await prepare(client)
            sid = url.split("/")[3]
        entered = asyncio.Event()
        release = asyncio.Event()
        async def blocked(request):
            entered.set()
            await release.wait()
            if failure == "timeout":
                raise httpx.ReadTimeout("private external outcome")
            return httpx.Response(503)
        ai.outcomes.append(blocked)
        async def operation(target_service):
            if stage == "start":
                return await target_service.start(MEMBER_A, "unknown")
            if stage == "message":
                return await target_service.send(MEMBER_A, sid, MessageRequest(client_message_id="unknown", text="처리 중 원문"))
            from app.schemas import DiaryDraftRequest
            return await target_service.diary_draft(MEMBER_A, sid, DiaryDraftRequest(client_request_id="unknown", history_revision=1))
        task = asyncio.create_task(operation(service))
        await asyncio.wait_for(entered.wait(), 1)
        request = next(r for r in repo.requests.values() if r["client_key"] == "unknown")
        sid = request["session_id"]
        assert request["state"] == "dispatched"
        if stage == "message":
            assert request["input"] == {"text": "처리 중 원문"}
            assert "처리 중 원문" not in str(repo.sessions[sid]["messages"])
        if failure == "crash":
            # 종료 hook이 실행되지 않은 강제 종료를 모형화: 취소 기록 쓰기도 실패시킵니다.
            repo.failure = ("unknown", "before")
        if failure != "timeout":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            with pytest.raises(AppError) as error:
                await task
            assert error.value.code == "AI_OUTCOME_UNKNOWN"
        assert request["state"] == ("dispatched" if failure == "crash" else "unknown")
    async with persistent_api(repo) as (client, service, _, ai):
        if failure == "crash":
            with pytest.raises(AppError) as error:
                await operation(service)
            assert error.value.code == "REQUEST_IN_PROGRESS"
            repo.clock += 181
        for _ in range(2):
            with pytest.raises(AppError) as error:
                await operation(service)
            assert error.value.code == "AI_OUTCOME_UNKNOWN"
        base = f"/api/chat-sessions/{sid}"
        for path, body in [("/messages", {"client_message_id": "new", "text": "새 ID 우회"}),
                           ("/diary-draft", {"client_request_id": "new", "history_revision": 1}),
                           ("/diaries", {"client_request_id": "new", "history_revision": 1,
                                         "draft_id": "draft_" + "1" * 32, "weather": None})]:
            assert_error(await client.post(base + path, json=body), 502, "AI_OUTCOME_UNKNOWN")
        assert not ai.requests


@pytest.mark.parametrize("phase", ["reserve", "dispatched", "succeeded"])
@pytest.mark.parametrize("boundary", ["before", "after", "rollback"])
@pytest.mark.parametrize("stage", ["start", "message", "draft"])
async def test_database_failure_and_lost_responses_never_repeat_ai(phase, boundary, stage):
    repo = FakeSessionRepository()
    async with persistent_api(repo) as (client, _, _, ai):
        if stage == "start":
            path, data, headers = "/api/chat-sessions", {}, {"Idempotency-Key": "loss"}
        else:
            prepared, _, _ = await prepare(client)
            base = prepared.removesuffix("/diaries")
            path = base + ("/messages" if stage == "message" else "/diary-draft")
            data = ({"client_message_id": "loss", "text": "완료 기록 장애 원문"} if stage == "message" else
                    {"client_request_id": "loss", "history_revision": 1})
            headers = {}
        count = len(ai.requests)
        repo.failure = (phase, boundary)
        result = await client.post(path, json=data, headers=headers)
        assert_error(result, 503, "SESSION_STORAGE_UNAVAILABLE")
        assert len(ai.requests) - count == (1 if phase == "succeeded" else 0)
    async with persistent_api(repo) as (client, _, _, ai):
        result = await client.post(path, json=data, headers=headers)
        success_status = 201 if stage == "start" else 200
        if phase == "reserve" and boundary != "after":
            assert result.status_code == success_status and len(ai.requests) == 1
        elif phase == "succeeded" and boundary == "after":
            assert result.status_code == success_status and not ai.requests
        else:
            assert_error(result, 409, "REQUEST_IN_PROGRESS")
            repo.clock += 181
            result = await client.post(path, json=data, headers=headers)
            if phase == "succeeded" or (phase == "dispatched" and boundary == "after"):
                assert_error(result, 502, "AI_OUTCOME_UNKNOWN")
                assert not ai.requests
            else:
                assert result.status_code == success_status and len(ai.requests) == 1
        assert len(repo.sessions) == 1


@pytest.mark.parametrize("same_key", [True, False])
@pytest.mark.parametrize("kind", ["start", "message", "draft"])
async def test_concurrent_requests_dispatch_only_once(same_key, kind):
    repo = FakeSessionRepository()
    async with persistent_api(repo) as (client, service, _, ai):
        if kind != "start":
            url, body, draft = await prepare(client)
            sid = url.split("/")[3]
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(request):
            entered.set()
            await release.wait()
            if kind == "draft":
                sources = [{"id": 1, "text": "오늘 친구와 공원에서 놀았다."}]
                return httpx.Response(200, json={"text": sources[0]["text"], "sources": sources,
                    "pages": [{"text": sources[0]["text"], "source_ids": [1], "imageUrl": None}],
                    "requires_confirmation": True, "mock": True, "fallback": True})
            return httpx.Response(200, json={"question": "가상 질문", "finished": False, "mock": True, "fallback": False})
        ai.outcomes.append(blocked)
        def post(client, key):
            if kind == "start":
                return client.post("/api/chat-sessions", json={}, headers={"Idempotency-Key": key})
            body = ({"client_message_id": key, "text": "동시 발언"} if kind == "message" else
                    {"client_request_id": key, "history_revision": 1})
            return client.post(f"/api/chat-sessions/{sid}/" + ("messages" if kind == "message" else "diary-draft"), json=body)
        first = asyncio.create_task(post(client, "concurrent"))
        await asyncio.wait_for(entered.wait(), 1)
        async with persistent_api(repo) as (second_client, _, _, second_ai):
            second = await post(second_client, "concurrent" if same_key else "different")
            if kind == "start" and not same_key:
                assert second.status_code == 201
                assert len(second_ai.requests) == 1
            else:
                assert_error(second, 409, "REQUEST_IN_PROGRESS" if same_key else "SESSION_BUSY")
                assert not second_ai.requests
        release.set()
        assert (await first).status_code == (201 if kind == "start" else 200)


async def test_connection_failure_retry_keeps_base_revision():
    async with persistent_api() as (client, service, repo, ai):
        sid = (await start(client))["session_id"]
        path = f"/api/chat-sessions/{sid}/messages"
        body = {"client_message_id": "connection", "text": "가상 원문"}
        ai.outcomes.append(httpx.ConnectError("private before send"))
        assert_error(await client.post(path, json=body), 503, "AI_CONNECTION_FAILED")
        assert (await client.post(path, json=body)).status_code == 200
        ai.outcomes.append(httpx.ConnectTimeout("private before send"))
        old = {"client_message_id": "old", "text": "옛 원문"}
        assert_error(await client.post(path, json=old), 503, "AI_CONNECTION_FAILED")
        assert (await client.post(path, json={"client_message_id": "new", "text": "다음 발언"})).status_code == 200
        count = len(ai.requests)
        assert_error(await client.post(path, json=old), 409, "HISTORY_REVISION_CONFLICT")
        assert len(ai.requests) == count


async def test_older_draft_replay_keeps_latest_snapshot_but_cannot_confirm():
    async with persistent_api() as (client, _, repo, ai):
        path, confirm, draft = await prepare(client)
        base = path.removesuffix("/diaries")
        sid = base.split("/")[3]
        assert (await client.post(base + "/messages", json={"client_message_id":"later", "text":"나중 발언"})).status_code == 200
        snapshot = deepcopy(repo.sessions[sid])
        count = len(ai.requests)
        assert (await client.post(base + "/diary-draft", json={"client_request_id":"draft-1", "history_revision":1})).json() == draft
        assert repo.sessions[sid] == snapshot and len(ai.requests) == count
        assert_error(await client.post(path, json=confirm),409,"HISTORY_REVISION_CONFLICT")


@pytest.mark.parametrize("status,code", [(503,"AI_UNAVAILABLE"),(400,"AI_REQUEST_REJECTED"),(500,"AI_BAD_RESPONSE")])
async def test_known_ai_failure_is_cached_across_restart(status, code):
    repo = FakeSessionRepository()
    async with persistent_api(repo) as (client, _, _, ai):
        ai.outcomes.append(httpx.Response(status))
        assert_error(await client.post("/api/chat-sessions",json={},headers={"Idempotency-Key":"failed"}),
                     503 if status == 503 else 502,code)
    async with persistent_api(repo) as (client, _, _, ai):
        assert_error(await client.post("/api/chat-sessions",json={},headers={"Idempotency-Key":"failed"}),
                     503 if status == 503 else 502,code)
        assert not ai.requests


@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_confirmation_uses_one_diary_transaction(same_key):
    repo = FakeSessionRepository()
    entered, release = asyncio.Event(), asyncio.Event()
    original = repo.confirm
    calls = 0
    async def blocked(record):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return await original(record)
    repo.confirm = blocked
    async with persistent_api(repo) as (client, _, _, ai):
        path, body, _ = await prepare(client)
        first = asyncio.create_task(client.post(path,json=body))
        await asyncio.wait_for(entered.wait(),1)
        async with persistent_api(repo) as (other, _, _, other_ai):
            second = await other.post(path,json=body if same_key else {**body,"client_request_id":"other"})
            if same_key:
                assert second.status_code == 200,second.text
            else:
                assert_error(second,409,"SESSION_BUSY")
            assert not other_ai.requests
        release.set()
        first_result = await first
        assert first_result.status_code == 200,first_result.text
        if same_key:
            assert first_result.json()["diary_id"] == second.json()["diary_id"]
        assert len(repo.db.diaries) == len(repo.db.messages) == 1


async def test_known_confirmation_conflict_is_cached_and_releases_session():
    async with persistent_api() as (client, _, repo, ai):
        path, body, _ = await prepare(client,"one")
        assert (await client.post(path,json=body)).status_code == 200
        path2, body2, _ = await prepare(client,"two")
        sid = path2.split("/")[3]
        body2["client_request_id"] = "confirm-2"
        for _ in range(2):
            assert_error(await client.post(path2,json=body2),409,"DIARY_DATE_CONFLICT")
        assert repo.sessions[sid]["active_request_id"] is None
        assert len(repo.db.diaries) == 1
        assert (await client.post(path2.removesuffix("/diaries") + "/messages",json={"client_message_id":"after-conflict","text":"후속 발언"})).status_code == 200


async def test_production_repository_wiring_with_mock_http_and_restart():
    fake = FakeSessionRepository()
    async def database(request):
        if request.url.path.startswith("/rest/v1/rpc/") and request.url.path.split("/")[-1] in {
            "reserve_chat_request","transition_chat_request","confirm_chat_session_diary"}:
            assert request.headers["apikey"] == FAKE_SECRET and "authorization" not in request.headers
            try:
                data = await fake.rpc(request.url.path.split("/")[-1], json.loads(request.content))
            except AppError as error:
                return httpx.Response(200,json={"error":error.code})
            return httpx.Response(200,json=data)
        return await fake.db(request)
    async def forbidden(request):
        raise AssertionError("Storage 호출 금지")
    settings = Settings(session_store_mode="supabase",supabase_secret_key=FAKE_SECRET,
                        supabase_publishable_key=TEST_PUBLISHABLE_KEY)
    for index in range(2):
        ai = FakeAI()
        app = create_app(settings,httpx.MockTransport(ai),httpx.MockTransport(FakeAuth()),
                         httpx.MockTransport(database),httpx.MockTransport(forbidden))
        async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app),
            base_url="http://be.test",headers={"Authorization":"Bearer fake-member-a"}) as client:
            if index == 0:
                path,body,_ = await prepare(client)
                result = await client.post(path,json=body)
                assert result.status_code == 200,result.text
                saved = result.json()
                assert len(ai.requests) == 3
            else:
                assert (await client.post(path,json=body)).json() == saved
                assert len(ai.requests) == 0
        assert type(app.state.chat_service.store.repository) is SessionRepository


async def test_large_valid_six_page_draft_fits_one_mib_candidate_budget():
    async with persistent_api() as (client, _, repo, ai):
        sid = (await start(client))["session_id"]
        base = f"/api/chat-sessions/{sid}"
        for index in range(9):
            response = await client.post(base + "/messages",json={
                "client_message_id":f"max-{index}","text":"\x1c" * 1999 + "😀"})
            assert response.status_code == 200
        sources = [{"id":i,"text":m["text"]} for i,m in enumerate(repo.sessions[sid]["messages"]) if m["role"] == "child"]
        text = "\x1c" * 19989 + " ".join(["😀"] * 6)
        assert len(text) == 20000
        ai.outcomes.append(httpx.Response(200,json={"text":text,"sources":sources,
            "pages":[{"text":"\x1c" * 19999 + "😀","source_ids":[1],"imageUrl":None} for _ in range(6)],
            "fallback":False,"mock":True,"requires_confirmation":True}))
        result = await client.post(base + "/diary-draft",json={"client_request_id":"max-draft","history_revision":9})
        assert result.status_code == 200,result.text[:200]
        size = len(json.dumps(result.json(),ensure_ascii=False).encode("utf-8"))
        assert 524288 < size <= 1048576
        assert (await client.post(base + "/diary-draft",json={"client_request_id":"max-draft","history_revision":9})).json() == result.json()


async def test_reserved_takeover_rejects_old_token_and_late_completion():
    repo = FakeSessionRepository()
    async with persistent_api(repo) as (client, service, _, ai):
        old = await service.store.reserve_start(MEMBER_A, "reservation")
        repo.clock += 31
        new = await service.store.reserve_start(MEMBER_A, "reservation")
        assert old.session_id == new.session_id and old.token != new.token
        with pytest.raises(AppError) as error:
            await service.store.dispatch(old)
        assert error.value.code == "REQUEST_IN_PROGRESS"
        await service.store.dispatch(new)
        repo.clock += 181
        with pytest.raises(AppError) as error:
            await repo.transition(new, "succeeded", {"fake": "late result"})
        assert error.value.code == "AI_OUTCOME_UNKNOWN"
        assert repo.sessions[new.session_id]["messages"] == []
        assert not ai.requests


@pytest.mark.parametrize("phase", ["reserve", "confirm"])
@pytest.mark.parametrize("boundary", ["before", "after", "rollback"])
async def test_confirmation_failure_is_atomic_and_keeps_original_time(phase, boundary):
    repo = FakeSessionRepository()
    async with persistent_api(repo) as (client, _, _, ai):
        path, body, draft = await prepare(client)
        repo.failure = (phase, boundary)
        assert_error(await client.post(path, json=body), 503, "SESSION_STORAGE_UNAVAILABLE")
        if phase == "confirm" and boundary != "after":
            assert not repo.db.diaries and not repo.db.messages
        reserved = next((r for r in repo.requests.values() if r["kind"] == "confirm"), None)
        original_time = reserved["confirmed_at"] if reserved else None
        repo.clock += 86400
    async with persistent_api(repo) as (client, _, _, ai):
        result = await client.post(path, json=body)
        assert result.status_code == 200, result.text
        if original_time:
            assert datetime_equal(result.json()["confirmed_at"], original_time)
        assert len(repo.db.diaries) == 1 and len(repo.db.messages) == 1
        assert len(result.json()["messages"]) == 3 and not ai.requests


def datetime_equal(a, b):
    from datetime import datetime
    return datetime.fromisoformat(a) == datetime.fromisoformat(b)


async def test_cross_member_cannot_read_cached_or_reserve_session():
    async with persistent_api() as (client, _, repo, ai):
        path, body, draft = await prepare(client)
        sid = path.split("/")[3]
        before = deepcopy((repo.sessions, repo.requests))
        count = len(ai.requests)
        client.headers["Authorization"] = "Bearer fake-member-b"
        for route, data in [("messages", {"client_message_id": "message-1", "text": "오늘 친구와 공원에서 놀았다."}),
                            ("diary-draft", {"client_request_id": "draft-1", "history_revision": 1}),
                            ("diaries", body)]:
            assert_error(await client.post(f"/api/chat-sessions/{sid}/{route}", json=data), 404, "SESSION_NOT_FOUND")
        assert (repo.sessions, repo.requests) == before and len(ai.requests) == count
        b = await start(client)
        assert b["session_id"] != sid


async def test_foreign_session_with_own_saved_confirmation_key_still_returns_404():
    async with persistent_api() as (client, _, repo, ai):
        own_path, own_body, _ = await prepare(client)
        assert (await client.post(own_path,json=own_body)).status_code == 200
        client.headers["Authorization"] = "Bearer fake-member-b"
        foreign_path, _, _ = await prepare(client)
        client.headers["Authorization"] = "Bearer fake-member-a"
        before = deepcopy((repo.sessions,repo.requests))
        assert_error(await client.post(foreign_path,json=own_body),404,"SESSION_NOT_FOUND")
        assert (repo.sessions,repo.requests) == before


async def test_unconfigured_persistent_mode_fails_closed_health_stays_public():
    async def forbidden(request):
        raise AssertionError("외부 전송 금지")
    app = create_app(Settings(session_store_mode="supabase", supabase_publishable_key=TEST_PUBLISHABLE_KEY),
                     httpx.MockTransport(forbidden), httpx.MockTransport(FakeAuth()), httpx.MockTransport(forbidden))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://be.test") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert_error(await client.post("/api/chat-sessions", json={},
            headers={"Authorization": "Bearer fake-member-a", "Idempotency-Key": "x"}), 503, "SESSION_STORAGE_NOT_CONFIGURED")


@pytest.mark.parametrize("corrupt", ["schema", "owner", "order", "revision", "reply_id", "draft_sources", "input"])
async def test_corrupt_database_response_never_becomes_empty_session(corrupt):
    repo = FakeSessionRepository()
    async with persistent_api(repo) as (client, _, _, ai):
        path, body, draft = await prepare(client)
        sid = path.split("/")[3]
        if corrupt == "schema": repo.sessions[sid]["schema_version"] = 2
        elif corrupt == "owner":
            # 원본 DB는 유지한 채 응답의 소유자만 손상시킵니다.
            original = repo.envelope
            def envelope(s, r, acquired=False):
                data = original(s, r, acquired)
                data["request"]["owner_id"] = "33333333-3333-4333-8333-333333333333"
                return data
            repo.envelope = envelope
        elif corrupt == "order": repo.sessions[sid]["messages"][0]["role"] = "child"
        elif corrupt == "revision": repo.sessions[sid]["history_revision"] = 8
        elif corrupt == "reply_id":
            next(r for r in repo.requests.values() if r["kind"] == "message")["result"]["reply"]["message_id"] = "bad"
        elif corrupt == "draft_sources":
            next(r for r in repo.requests.values() if r["kind"] == "draft")["result"]["draft"]["sources"][0]["text"] = "변조"
        else:
            next(r for r in repo.requests.values() if r["kind"] == "draft")["input"] = {"history_revision": 99}
        count = len(ai.requests)
        target = ("messages", {"client_message_id": "message-1", "text": "오늘 친구와 공원에서 놀았다."}) if corrupt == "reply_id" else ("diary-draft", {"client_request_id": "draft-1", "history_revision": 1})
        response = await client.post(f"/api/chat-sessions/{sid}/{target[0]}", json=target[1])
        assert_error(response, 409 if corrupt == "input" else 503,
                     "IDEMPOTENCY_CONFLICT" if corrupt == "input" else "SESSION_STORAGE_INVALID_RESPONSE")
        assert len(ai.requests) == count


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, text="bad"), httpx.Response(200, json={}),
                                       httpx.ReadTimeout("private"), httpx.Response(200, json={"error": "SESSION_BUSY"})])
async def test_real_repository_http_boundary(response):
    calls = []
    async def handler(request):
        calls.append(request)
        assert request.headers["apikey"] == FAKE_SECRET
        assert "authorization" not in request.headers
        assert request.url.path == "/rest/v1/rpc/reserve_chat_request"
        if isinstance(response, Exception): raise response
        return response
    async with httpx.AsyncClient(base_url="https://offline.invalid", headers={"apikey": FAKE_SECRET},
                                 transport=httpx.MockTransport(handler), trust_env=False) as http:
        repo = SessionRepository(http, 0.1, 135)
        with pytest.raises(AppError) as error:
            await repo.reserve(MEMBER_A, "ses_" + "1" * 32, "start", "key", "", {})
        assert error.value.code in {"SESSION_STORAGE_UNAVAILABLE", "SESSION_STORAGE_INVALID_RESPONSE", "SESSION_BUSY"}
        assert len(calls) == 1
