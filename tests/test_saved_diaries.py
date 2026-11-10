import asyncio
import copy
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.clients.diaries import DiaryRepository
from app.config import Settings
from app.diary_schemas import ConfirmDiaryRequest
from app.errors import AppError
from app.main import create_app
from tests.conftest import FakeAI, FakeAuth, MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY

FAKE_SECRET = "sb_secret_fake_test_only"
RPC_CONFLICTS = re.findall(
    r"raise exception using errcode = '(PT409)', message = '([^']+)',\s*detail = '([^']+)';",
    (Path(__file__).resolve().parents[1] / "supabase" / "migrations"
     / "20261107000300_confirm_member_diary.sql").read_text(encoding="utf-8"),
)


class FakeDatabase:
    """HTTP 계약 모의 저장소. 실제 PostgreSQL/RLS 검증과 구분."""

    def __init__(self):
        self.diaries = {}
        self.messages = {}
        self.requests = []
        self.fail_next = None
        self.fail_message_insert = False
        self.lose_response = False
        self.entered = None
        self.release = None
        self.lock = asyncio.Lock()

    async def __call__(self, request):
        self.requests.append(request)
        if request.url.path == "/rest/v1/rpc/member_room_command":
            return httpx.Response(404, json={"code": "PGRST202"})
        assert not request.headers.get("cookie")
        if self.fail_next is not None:
            outcome, self.fail_next = self.fail_next, None
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if request.method == "POST":
            assert request.url.path == "/rest/v1/rpc/confirm_member_diary"
            assert request.headers["apikey"] == FAKE_SECRET
            assert "authorization" not in request.headers
            if self.entered:
                self.entered.set()
                await self.release.wait()
            async with self.lock:
                return self._save(request)
        assert request.method == "GET"
        assert request.headers["apikey"] == TEST_PUBLISHABLE_KEY
        owner = {"Bearer fake-member-a": str(MEMBER_A),
                 "Bearer fake-member-b": str(MEMBER_B)}[request.headers["authorization"]]
        params = request.url.params
        assert params["user_id"] == f"eq.{owner}"
        if request.url.path == "/rest/v1/diary_assets":
            return httpx.Response(200, json=[])
        if request.url.path == "/rest/v1/diaries":
            rows = [copy.deepcopy(row) for row in self.diaries.values() if row["user_id"] == owner]
            for field in ("id", "source_client_request_id"):
                if field in params:
                    rows = [row for row in rows if f"eq.{row[field]}" == params[field]]
            for criterion in params.get_list("diary_date"):
                operator, value = criterion.split(".", 1)
                rows = [row for row in rows if (row["diary_date"] >= value if operator == "gte"
                                                else row["diary_date"] <= value)]
            if "order" in params:
                assert params["order"] == "diary_date.asc"
                rows.sort(key=lambda row: row["diary_date"])
            columns = params["select"].split(",")
            return httpx.Response(200, json=[{key: {
                "edit_version": 1, "updated_at": row["confirmed_at"],
                "deleted_at": None, "active_drawing_edit_id": None, **row,
            }[key] for key in columns} for row in rows])
        assert request.url.path == "/rest/v1/diary_messages"
        assert params["order"] == "position.asc"
        diary_id = params["diary_id"].removeprefix("eq.")
        row = self.diaries.get(diary_id)
        messages = self.messages.get(diary_id, []) if row and row["user_id"] == owner else []
        return httpx.Response(200, json=sorted(copy.deepcopy(messages), key=lambda item: item["position"]))

    def _save(self, request):
        payload = json.loads(request.content)
        for row in self.diaries.values():
            if (row["user_id"] == payload["p_user_id"]
                    and row["source_client_request_id"] == payload["p_client_request_id"]):
                if row["confirmation_fingerprint"] != payload["p_fingerprint"]:
                    return httpx.Response(409, json={
                        "code": "PT409", "details": "IDEMPOTENCY_CONFLICT",
                    })
                return httpx.Response(200, json=row["id"])
        diary_date = datetime.fromisoformat(payload["p_confirmed_at"]).astimezone(
            timezone(timedelta(hours=9)),
        ).date().isoformat()
        if any(row["user_id"] == payload["p_user_id"] and row["diary_date"] == diary_date
               for row in self.diaries.values()):
            return httpx.Response(409, json={
                "code": "PT409", "details": "DIARY_DATE_CONFLICT",
            })
        diary_id = str(uuid4())
        row = {
            "id": diary_id, "user_id": payload["p_user_id"], "diary_date": diary_date,
            "confirmed_text": payload["p_ai_draft"]["text"], "ai_draft": payload["p_ai_draft"],
            "confirmed_at": payload["p_confirmed_at"], "weather": payload["p_weather"],
            "source_client_request_id": payload["p_client_request_id"],
            "confirmation_fingerprint": payload["p_fingerprint"],
            "edit_version": 1, "updated_at": payload["p_confirmed_at"],
            "deleted_at": None, "active_drawing_edit_id": None,
        }
        # RPC 트랜잭션 실패 응답 모의
        if self.fail_message_insert:
            return httpx.Response(400, json={"code": "23514", "message": "private DB details"})
        self.diaries[diary_id] = row
        self.messages[diary_id] = payload["p_messages"]
        if self.lose_response:
            self.lose_response = False
            raise httpx.ReadTimeout("private transport details")
        return httpx.Response(200, json=diary_id)


@pytest.fixture
def saved_api():
    @asynccontextmanager
    async def make(db=None, secret=FAKE_SECRET, auth=None):
        db = db or FakeDatabase()
        ai = FakeAI()

        async def ai_handler(request):
            assert "apikey" not in request.headers and "authorization" not in request.headers
            return await ai(request)

        app = create_app(
            Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY, supabase_secret_key=secret),
            httpx.MockTransport(ai_handler), httpx.MockTransport(auth or FakeAuth()),
            httpx.MockTransport(db),
        )
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://be.test",
            headers={"Authorization": "Bearer fake-member-a"},
        ) as client:
            yield client, app, db, ai
    return make


async def prepare(client, key="start"):
    session = await client.post("/api/chat-sessions", json={}, headers={"Idempotency-Key": key})
    assert session.status_code == 201
    session_id = session.json()["session_id"]
    message = await client.post(f"/api/chat-sessions/{session_id}/messages", json={
        "client_message_id": "message-1", "text": "오늘 친구와 공원에서 놀았다.",
    })
    assert message.status_code == 200
    draft = await client.post(f"/api/chat-sessions/{session_id}/diary-draft", json={
        "client_request_id": "draft-1", "history_revision": 1,
    })
    assert draft.status_code == 200
    body = {"client_request_id": "confirm-1", "history_revision": 1,
            "draft_id": draft.json()["draft"]["draft_id"], "weather": "sunny"}
    return f"/api/chat-sessions/{session_id}/diaries", body, draft.json()


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error", "request_id"}
    assert response.headers["cache-control"] == "no-store"
    assert FAKE_SECRET not in response.text
    assert "private" not in response.text


@pytest.mark.anyio
async def test_members_save_read_and_cross_access(saved_api):
    async with saved_api() as (client, app, db, ai):
        results = []
        for member in ("a", "b"):
            client.headers["Authorization"] = f"Bearer fake-member-{member}"
            url, body, draft = await prepare(client)
            response = await client.post(url, json=body)
            assert response.status_code == 200, response.text
            saved = response.json()
            assert saved["confirmed_text"] == draft["draft"]["text"]
            assert saved["ai_draft"] == draft["draft"]
            assert saved["imageUrl"] is None and saved["assets"] == []
            session_id = url.split("/")[3]
            assert saved["messages"] == [
                {"position": index, **message.model_dump()}
                for index, message in enumerate(app.state.chat_service.store.sessions[session_id].messages)
            ]
            assert [m["role"] for m in saved["messages"]] == ["assistant", "child", "assistant"]
            detail = await client.get(f"/api/diaries/{saved['diary_id']}")
            assert detail.json() == saved
            listing = await client.get("/api/diaries", params={"month": saved["diary_date"][:7]})
            assert listing.json()["diaries"] == [{
                "diary_id": saved["diary_id"], "diary_date": saved["diary_date"],
                "weather": "sunny", "preview": saved["confirmed_text"][:120],
            }]
            results.append((url, body, saved))
        for member, other in (("a", results[1]), ("b", results[0])):
            client.headers["Authorization"] = f"Bearer fake-member-{member}"
            for key in ("foreign", "confirm-1"):
                assert_error(await client.post(other[0], json={**other[1], "client_request_id": key}),
                             404, "SESSION_NOT_FOUND")
            foreign = await client.get(f"/api/diaries/{other[2]['diary_id']}")
            missing = await client.get(f"/api/diaries/{uuid4()}")
            assert_error(foreign, 404, "DIARY_NOT_FOUND")
            assert foreign.json()["error"] == missing.json()["error"]
        assert len(db.diaries) == 2
        assert len([r for r in db.requests if r.url.path == "/rest/v1/rpc/confirm_member_diary"]) == 2
        assert FAKE_SECRET not in json.dumps(ai.requests)


@pytest.mark.anyio
async def test_retry_restart_and_changed_request(saved_api):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        saved = await client.post(url, json=body)
        assert saved.status_code == 200
        # 이후 대화 변경에도 이미 성공한 동일 요청 복구
        await client.post(url.removesuffix("diaries") + "messages", json={
            "client_message_id": "message-2", "text": "그리고 집으로 갔다.",
        })
        assert (await client.post(url, json=body)).json() == saved.json()
    async with saved_api(db=db, secret=None) as (client, app, db, ai):
        assert not app.state.chat_service.store.sessions
        assert (await client.post(url, json=body)).json() == saved.json()
        assert (await client.get(f"/api/diaries/{saved.json()['diary_id']}")).json() == saved.json()
        listing = await client.get("/api/diaries", params={"month": saved.json()["diary_date"][:7]})
        assert len(listing.json()["diaries"]) == 1
        for change in ({"weather": "rainy"}, {"history_revision": 2},
                       {"draft_id": "draft_" + "0" * 32}):
            assert_error(await client.post(url, json={**body, **change}), 409, "IDEMPOTENCY_CONFLICT")
        assert_error(await client.post(url.replace("ses_", "ses_0"), json=body),
                     409, "IDEMPOTENCY_CONFLICT")
        assert len(db.diaries) == 1
        assert len([r for r in db.requests if r.url.path == "/rest/v1/rpc/confirm_member_diary"]) == 1


@pytest.mark.anyio
async def test_same_day_conflict(saved_api):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        assert (await client.post(url, json=body)).status_code == 200
        other, new_body, _ = await prepare(client, "start-2")
        assert_error(await client.post(other, json={**new_body, "client_request_id": "confirm-2"}),
                     409, "DIARY_DATE_CONFLICT")
        assert len(db.diaries) == 1
        assert len(db.messages) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("sqlstate,message,details", RPC_CONFLICTS)
async def test_rpc_conflicts_from_sql_return_api_409(saved_api, sqlstate, message, details):
    db = FakeDatabase()

    async def handler(request):
        if request.method == "POST":
            return httpx.Response(int(sqlstate[2:]), json={
                "code": sqlstate, "message": message, "details": details, "hint": None,
            })
        return await db(request)

    async with saved_api(db=handler) as (client, app, _, ai):
        url, body, _ = await prepare(client)
        response = await client.post(url, json=body)
        assert_error(response, 409, details)
        assert response.json()["error"] == {
            "code": details, "message": {
                "IDEMPOTENCY_CONFLICT": "요청 내용 불일치",
                "DIARY_DATE_CONFLICT": "날짜 중복",
            }[details], "retryable": False,
        }
        assert not app.state.chat_service.store.sessions[url.split("/")[3]].busy
        assert not db.diaries and not db.messages


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [
    {"code": "PT409"},
    {"code": "PT409", "details": "unknown", "message": "날짜 중복"},
    {"code": "PT409", "details": None},
    {"code": "PT409", "details": ["DIARY_DATE_CONFLICT"]},
    {"code": "PT409", "details": {"code": "DIARY_DATE_CONFLICT"}},
    {"code": "23505", "details": "DIARY_DATE_CONFLICT"},
    {"code": "PT410", "details": "DIARY_DATE_CONFLICT"},
])
async def test_unknown_rpc_conflict_fails_safely(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(409, json=payload)),
        base_url="http://db.test",
    ) as writer:
        repository = DiaryRepository(None, writer, 1)
        with pytest.raises(AppError) as error:
            await repository.save({})
        assert error.value.status == 503
        assert error.value.code == "DIARY_STORAGE_FAILED"


@pytest.mark.anyio
@pytest.mark.parametrize("tamper", ["user_id", "confirmed_text", "messages", "ai_draft", "diary_date"])
async def test_reject_client_injection(saved_api, tamper):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        assert_error(await client.post(url, json={**body, tamper: "injected"}), 422, "VALIDATION_ERROR")
        assert not db.requests


@pytest.mark.anyio
@pytest.mark.parametrize("scenario,code", [
    ("missing", "DIARY_DRAFT_NOT_FOUND"), ("revision", "HISTORY_REVISION_CONFLICT"),
    ("stale_draft", "HISTORY_REVISION_CONFLICT"),
])
async def test_invalid_draft_or_history(saved_api, scenario, code):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        if scenario == "missing":
            body["draft_id"] = "draft_" + "0" * 32
        elif scenario == "revision":
            body["history_revision"] = 2
        else:
            await client.post(url.removesuffix("diaries") + "messages", json={
                "client_message_id": "message-2", "text": "집으로 갔다.",
            })
            body["history_revision"] = 2
        assert_error(await client.post(url, json=body), 404 if scenario == "missing" else 409, code)
        assert not any(r.method == "POST" for r in db.requests)


@pytest.mark.anyio
async def test_other_session_draft_rejected(saved_api):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        _, other_body, _ = await prepare(client, "start-2")
        assert_error(await client.post(url, json={**body, "draft_id": other_body["draft_id"]}),
                     404, "DIARY_DRAFT_NOT_FOUND")
        assert not any(r.method == "POST" for r in db.requests)


@pytest.mark.anyio
@pytest.mark.parametrize("token", [None, "fake-invalid", "fake-expired"])
@pytest.mark.parametrize("endpoint", ["save", "month", "detail"])
async def test_auth_before_database(saved_api, token, endpoint):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        client.headers.pop("Authorization")
        if token:
            client.headers["Authorization"] = f"Bearer {token}"
        if endpoint == "save":
            response = await client.post(url, json=body)
        elif endpoint == "month":
            response = await client.get("/api/diaries?month=2026-11")
        else:
            response = await client.get(f"/api/diaries/{uuid4()}")
        assert_error(response, 401, "AUTHENTICATION_FAILED")
        assert not db.requests


@pytest.mark.anyio
async def test_missing_secret_keeps_reads_and_chat(saved_api):
    async with saved_api(secret=None) as (client, app, db, ai):
        url, body, _ = await prepare(client)
        assert_error(await client.post(url, json=body), 503, "DIARY_STORAGE_NOT_CONFIGURED")
        assert (await client.get("/api/diaries?month=2026-11")).json() == {"month": "2026-11", "diaries": []}
        assert not any(r.method == "POST" for r in db.requests)
        assert not app.state.chat_service.store.sessions[url.split("/")[3]].busy


@pytest.mark.anyio
async def test_partial_failure_retry_and_response_loss(saved_api):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        db.fail_message_insert = True
        assert_error(await client.post(url, json=body), 503, "DIARY_STORAGE_FAILED")
        assert not db.diaries and not db.messages
        db.fail_message_insert = False
        db.lose_response = True
        assert_error(await client.post(url, json=body), 503, "DIARY_STORAGE_UNAVAILABLE")
        assert len(db.diaries) == len(db.messages) == 1
        assert not app.state.chat_service.store.sessions[url.split("/")[3]].busy
    async with saved_api(db=db) as (client, app, db, ai):
        assert (await client.post(url, json=body)).status_code == 200
        assert len(db.diaries) == 1


@pytest.mark.anyio
async def test_save_blocks_concurrent_session_mutation(saved_api):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        db.entered, db.release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(client.post(url, json=body))
        await asyncio.wait_for(db.entered.wait(), 2)
        try:
            assert_error(await client.post(url, json=body), 409, "SESSION_BUSY")
            assert_error(await client.post(url.removesuffix("diaries") + "messages", json={
                "client_message_id": "concurrent", "text": "추가 발언",
            }), 409, "SESSION_BUSY")
            assert_error(await client.post(url.removesuffix("diaries") + "diary-draft", json={
                "client_request_id": "new-draft", "history_revision": 1,
            }), 409, "SESSION_BUSY")
        finally:
            db.release.set()
        assert (await task).status_code == 200
        assert len(db.diaries) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("outcome,status,code", [
    (httpx.ConnectError("private error"), 503, "DIARY_STORAGE_UNAVAILABLE"),
    (httpx.ReadTimeout("private timeout"), 503, "DIARY_STORAGE_UNAVAILABLE"),
    (httpx.Response(503, text="private error"), 503, "DIARY_STORAGE_UNAVAILABLE"),
    (httpx.Response(429, text="private error"), 503, "DIARY_STORAGE_UNAVAILABLE"),
    (httpx.Response(401, text="private JWT"), 401, "AUTHENTICATION_FAILED"),
    (httpx.Response(200, text="bad JSON"), 502, "DIARY_STORAGE_INVALID_RESPONSE"),
    (httpx.Response(200, json={}), 502, "DIARY_STORAGE_INVALID_RESPONSE"),
    (httpx.Response(307, headers={"location": "https://elsewhere.test"}), 503, "DIARY_STORAGE_FAILED"),
    (httpx.Response(400, json={"code": "42703", "message": "private schema"}), 503, "DIARY_STORAGE_FAILED"),
])
async def test_storage_errors_redacted(saved_api, outcome, status, code):
    async with saved_api() as (client, app, db, ai):
        db.fail_next = outcome
        assert_error(await client.get("/api/diaries?month=2026-11"), status, code)


@pytest.mark.anyio
@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026-2", "2026-11-01", "0000-01"])
async def test_invalid_month(saved_api, month):
    async with saved_api() as (client, app, db, ai):
        assert_error(await client.get("/api/diaries", params={"month": month}), 422, "VALIDATION_ERROR")
        assert not db.requests


@pytest.mark.anyio
@pytest.mark.parametrize("change", [{"weather": "storm"}, {"weather": 1},
    {"history_revision": True}, {"history_revision": 0}, {"draft_id": "invalid"},
    {"client_request_id": "bad key"}])
async def test_invalid_confirmation(saved_api, change):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        assert_error(await client.post(url, json={**body, **change}), 422, "VALIDATION_ERROR")
        assert not db.requests


@pytest.mark.parametrize("value", ["sb_publishable_fake", "legacy-jwt", "", "sb_secret_bad\n"])
def test_secret_config_validation_redacts(value):
    with pytest.raises(ValidationError) as error:
        Settings(supabase_secret_key=value)
    assert repr(value) not in str(error.value)
    assert FAKE_SECRET not in repr(Settings(supabase_secret_key=FAKE_SECRET))


@pytest.mark.anyio
async def test_seoul_date_month_boundaries_order_and_omitted_weather(saved_api, monkeypatch):
    class Clock:
        value = datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz):
            return cls.value

    monkeypatch.setattr("app.services.diaries.datetime", Clock)
    async with saved_api() as (client, app, db, ai):
        results = []
        for index, instant in enumerate([
            datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 30, 14, 59, 59, tzinfo=timezone.utc),
            datetime(2026, 11, 29, 14, 59, 59, tzinfo=timezone.utc),
        ]):
            Clock.value = instant
            url, body, _ = await prepare(client, f"start-{index}")
            body["client_request_id"] = f"confirmation-{index}"
            body.pop("weather")
            response = await client.post(url, json=body)
            assert response.status_code == 200
            assert response.json()["weather"] is None
            assert (await client.post(url, json={**body, "weather": None})).json() == response.json()
            results.append(response.json())
        assert [row["diary_date"] for row in results] == ["2026-12-01", "2026-11-30", "2026-11-29"]
        november = await client.get("/api/diaries?month=2026-11")
        assert [row["diary_date"] for row in november.json()["diaries"]] == ["2026-11-29", "2026-11-30"]
        december = await client.get("/api/diaries?month=2026-12")
        assert [row["diary_date"] for row in december.json()["diaries"]] == ["2026-12-01"]
        assert (await client.get("/api/diaries?month=2026-10")).json()["diaries"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("month,last", [("2028-02", "2028-02-29"), ("2026-02", "2026-02-28"),
                                       ("9999-12", "9999-12-31"), ("0001-01", "0001-01-31")])
async def test_month_range(saved_api, month, last):
    async with saved_api() as (client, app, db, ai):
        assert (await client.get("/api/diaries", params={"month": month})).status_code == 200
        assert db.requests[-1].url.params.get_list("diary_date") == [f"gte.{month}-01", f"lte.{last}"]


@pytest.mark.anyio
async def test_cancellation_releases_session_and_preserves_source(saved_api):
    async with saved_api() as (client, app, db, ai):
        url, body, _ = await prepare(client)
        db.entered, db.release = asyncio.Event(), asyncio.Event()
        session = app.state.chat_service.store.sessions[url.split("/")[3]]
        before = copy.deepcopy(session.messages)
        task = asyncio.create_task(app.state.diary_service.confirm(
            MEMBER_A, "Bearer fake-member-a", url.split("/")[3],
            ConfirmDiaryRequest(**body),
        ))
        await asyncio.wait_for(db.entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.busy is None and session.messages == before
        db.release.set()
        assert (await client.post(url, json=body)).status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("change,conflict", [
    ({}, None), ({"weather": "rainy"}, "IDEMPOTENCY_CONFLICT"),
    ({"client_request_id": "another-request"}, "DIARY_DATE_CONFLICT"),
])
async def test_independent_process_rpc_race(saved_api, change, conflict):
    db = FakeDatabase()
    async with saved_api(db=db) as left, saved_api(db=db) as right:
        client_a, app_a, _, _ = left
        client_b, app_b, _, _ = right
        url, body, _ = await prepare(client_a)
        app_b.state.chat_service.store.sessions.update(copy.deepcopy(app_a.state.chat_service.store.sessions))
        db.entered, db.release = asyncio.Event(), asyncio.Event()
        task_a = asyncio.create_task(client_a.post(url, json=body))
        await asyncio.wait_for(db.entered.wait(), 2)
        db.entered.clear()
        task_b = asyncio.create_task(client_b.post(url, json={**body, **change}))
        try:
            await asyncio.wait_for(db.entered.wait(), 2)
        finally:
            db.release.set()
        responses = await asyncio.gather(task_a, task_b)
        successes = [r for r in responses if r.status_code == 200]
        if conflict is None:
            assert len(successes) == 2 and successes[0].json() == successes[1].json()
        else:
            assert len(successes) == 1
            assert_error(next(r for r in responses if r.status_code != 200), 409, conflict)
        assert len(db.diaries) == len(db.messages) == 1
        assert len(next(iter(db.messages.values()))) == 3
        assert len([r for r in db.requests if r.url.path == "/rest/v1/rpc/confirm_member_diary"]) == 2
