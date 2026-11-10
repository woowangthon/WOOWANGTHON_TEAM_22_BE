import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from copy import deepcopy

import httpx
import pytest

from app.clients.room import DOMAIN_ERRORS
from app.config import Settings
from app.main import create_app
from conftest import FakeAuth, FakeAI, MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY
from fake_room import FAKE_SECRET, FakeRoomDatabase, item
from test_chat_api import assert_error

pytestmark = pytest.mark.anyio
BASE_TREE = tuple(f"tree_stage_{i}" for i in range(7)) + ("tree_adult_default",)


@asynccontextmanager
async def room_api(db=None, auth=None, **settings):
    db = db if db is not None else FakeRoomDatabase()
    auth = auth if auth is not None else FakeAuth()
    ai = FakeAI()
    config = Settings(**{
        "supabase_publishable_key": TEST_PUBLISHABLE_KEY, "supabase_secret_key": FAKE_SECRET,
        **settings,
    })
    app = create_app(config, httpx.MockTransport(ai), httpx.MockTransport(auth), httpx.MockTransport(db))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://be.test",
        headers={"Authorization": "Bearer fake-member-a"},
    ) as client:
        yield client, db, app, ai


ROUTES = [
    ("GET", "/api/me/room", None),
    ("POST", "/api/me/rewards/sync", {}),
    ("GET", "/api/shop/items", None),
    ("POST", "/api/shop/purchases", {"client_request_id": "buy1", "item_id": "chair"}),
    ("PUT", "/api/me/room/placements/chair", {"x": 230, "y": 310}),
    ("DELETE", "/api/me/room/placements/chair", None),
    ("PUT", "/api/me/room/theme", {"item_id": None}),
    ("POST", "/api/me/tree/water", {"client_request_id": "water1"}),
]


async def buy(client, key="chair", request_id="buy1"):
    return await client.post("/api/shop/purchases", json={"client_request_id": request_id, "item_id": key})




def diaries(db):
    db.diaries = [
        {"member": str(MEMBER_A), "date": "2026-11-08", "confirmed_on": "2026-11-08", "id": "past"},
        {"member": str(MEMBER_A), "date": "2026-11-09", "confirmed_on": "2026-11-09", "id": "missed"},
        {"member": str(MEMBER_A), "date": "2026-11-10", "confirmed_on": "2026-11-10", "id": "today"},
        {"member": str(MEMBER_A), "date": "2026-11-11", "confirmed": False, "id": "draft"},
        {"member": str(MEMBER_B), "date": "2026-11-10", "confirmed_on": "2026-11-10", "id": "other"},
    ]
    db.legacy_diary_ids = {"past", "missed"}


async def test_legacy_ids_never_backfill_even_with_new_settings_or_old_request_replay():
    async with room_api() as (client, db, app, ai):
        diaries(db)
        db.legacy_diary_ids = {d["id"] for d in db.diaries}
        for _ in range(2):
            body = (await client.post("/api/me/rewards/sync", json={})).json()
            assert body == {"new_rewards": [], "wallet": {"coins": 0, "water_drops": 0}}
        assert not db.member(MEMBER_A).grants
        assert all(set(request) == {"p_member_id", "p_action", "p_payload"} for request in db.requests)


async def test_new_confirmed_diary_ids_grant_once_including_concurrent_requests():
    async with room_api() as (client, db, app, ai):
        diaries(db)
        db.legacy_diary_ids.remove("missed")
        responses = await asyncio.gather(*(client.post("/api/me/rewards/sync", json={}) for _ in range(8)))
        assert all(response.status_code == 200 for response in responses)
        rewards = [reward for response in responses for reward in response.json()["new_rewards"]]
        assert rewards == [
            {"diary_date": "2026-11-09", "coins": 30, "water_drops": 1},
            {"diary_date": "2026-11-10", "coins": 30, "water_drops": 1}]
        assert db.member(MEMBER_A).coins == 60 and db.member(MEMBER_A).water == 2
        assert not db.member(MEMBER_B).grants
        assert (await client.post("/api/me/rewards/sync", json={})).json()["new_rewards"] == []
        await client.get("/api/me/room")
        assert db.requests[-1]["p_action"] == "get_room"


async def test_get_does_not_grant_and_new_confirmation_needs_no_date_setting():
    async with room_api() as (client, db, app, ai):
        diaries(db)
        assert (await client.get("/api/me/room")).json()["wallet"]["coins"] == 0
        assert (await client.post("/api/me/rewards/sync", json={})).json()["wallet"] == {"coins": 30, "water_drops": 1}






























@pytest.mark.parametrize("code", list(DOMAIN_ERRORS))
async def test_only_known_postgrest_domain_errors_are_exposed(code):
    status = DOMAIN_ERRORS[code][0]
    async def broken(request):
        return httpx.Response(status, json={"code": f"PT{status}", "details": code, "message": "private internal"})
    async with room_api(db=broken) as (client, *_):
        response = await client.get("/api/me/room")
        assert_error(response, status, code)
        assert "private internal" not in response.text


@pytest.mark.parametrize("outcome", [
    httpx.Response(500, text="private"), httpx.Response(429, text="private"),
    httpx.Response(404, json={"code": "PGRST202", "message": "private"}),
    httpx.Response(409, json={"code": "PT409", "details": "unknown", "message": "private"}),
    httpx.Response(400, json={"code": "PT409", "details": "INSUFFICIENT_COINS"}),
    httpx.Response(302, headers={"Location": "https://untrusted.test"}),
    httpx.Response(200, text="not json"), httpx.Response(200, json=[]), httpx.Response(200, json={}),
    httpx.Response(204), httpx.ConnectError("private"), httpx.ReadTimeout("private"),
])
async def test_unavailable_or_malformed_room_store_never_leaks_or_retries(outcome):
    calls = []
    async def broken(request):
        calls.append(request)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    async with room_api(db=broken) as (client, *_):
        response = await client.get("/api/me/room")
        assert_error(response, 503, "ROOM_STORAGE_UNAVAILABLE", True)
        assert len(calls) == 1 and "private" not in response.text


async def test_timeout_deadline_and_missing_writer_fail_closed():
    async def blocked(request):
        await asyncio.Event().wait()
    async with room_api(db=blocked, db_request_timeout_seconds=0.01) as (client, *_):
        assert_error(await client.get("/api/me/room"), 503, "ROOM_STORAGE_UNAVAILABLE", True)
    async with room_api(supabase_secret_key=None) as (client, db, app, ai):
        assert_error(await client.get("/api/me/room"), 503, "ROOM_STORAGE_UNAVAILABLE", True)
        assert not db.requests


async def test_default_asset_identifiers_are_contract_keys_without_catalog_products():
    async with room_api() as (client, db, app, ai):
        room = (await client.get("/api/me/room")).json()
        assert room["tree"]["asset_key"] == "tree_stage_0"
        assert room["room"]["theme"] == {"item_id": None, "background_asset_key": "room_default", "diary_cover_asset_key": "cover_default"}
        assert not db.items




async def test_rewards_need_no_configuration():
    assert not any("reward" in field for field in Settings.model_fields)


async def test_room_environment_configuration_keeps_session_default(monkeypatch):
    monkeypatch.delenv("SESSION_STORE_MODE", raising=False)
    assert Settings.from_env().session_store_mode == "memory"
