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


async def test_empty_room_and_shop_are_readable_without_seeding_products_or_rewards():
    async with room_api() as (client, db, app, ai):
        result = await client.get("/api/me/room")
        assert result.status_code == 200
        body = result.json()
        assert body == {
            "as_of_date": "2026-11-10", "wallet": {"coins": 0, "water_drops": 0},
            "room": {"width": 1440, "height": 900, "theme": {"item_id": None,
                "background_asset_key": "room_default", "diary_cover_asset_key": "cover_default"}},
            "tree": {"stage": 0, "max_stage": 7, "days_watered": 0, "watered_today": False, "asset_key": BASE_TREE[0]},
            "inventory": [],
        }
        assert (await client.get("/api/shop/items")).json() == {"items": []}
        assert (await client.post("/api/me/rewards/sync", json={})).json() == {
            "new_rewards": [], "wallet": {"coins": 0, "water_drops": 0}}
        assert not ai.requests and not db.items
        assert result.headers["cache-control"] == "no-store"


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


async def test_purchase_is_idempotent_and_separate_from_placement_and_theme():
    async with room_api() as (client, db, app, ai):
        db.items = {"chair": item(), "theme_snow": item("theme_snow", "theme", 20)}
        db.member(MEMBER_A).coins = 100
        first = await buy(client)
        assert first.status_code == 200 and first.json() == {
            "item_id": "chair", "owned": True, "wallet": {"coins": 70, "water_drops": 0}}
        assert (await buy(client)).json() == first.json()
        assert_error(await buy(client, request_id="different"), 409, "ITEM_ALREADY_OWNED")
        assert_error(await buy(client, "theme_snow"), 409, "IDEMPOTENCY_CONFLICT")
        assert (await buy(client, "theme_snow", "theme-buy")).status_code == 200
        room = (await client.get("/api/me/room")).json()
        assert room["room"]["theme"]["item_id"] is None
        assert all(value["position"] is None for value in room["inventory"])
        assert room["wallet"]["coins"] == 50
        shop = (await client.get("/api/shop/items")).json()["items"]
        assert all(value["owned"] for value in shop)
        assert set(shop[0]) == {"item_id", "name", "category", "price_coins", "asset_key", "width", "height",
                               "background_asset_key", "diary_cover_asset_key", "adult_tree_asset_key", "owned"}
        # 판매 중지된 보유 상품의 동일 요청도 추가 차감 없이 복구된다.
        db.items["chair"]["is_active"] = False
        assert (await buy(client)).status_code == 200
        assert len((await client.get("/api/shop/items")).json()["items"]) == 1
        assert len((await client.get("/api/me/room")).json()["inventory"]) == 2


@pytest.mark.parametrize("same_item,same_key", [(True, True), (True, False), (False, False), (False, True)])
async def test_concurrent_purchases_from_two_app_instances_share_database_protection(same_item, same_key):
    db = FakeRoomDatabase()
    db.items = {"chair": item(), "lamp": item("lamp", "decoration")}
    db.member(MEMBER_A).coins = 30
    async with room_api(db) as (first, *_), room_api(db) as (second, *_):
        results = await asyncio.gather(buy(first), buy(second, "chair" if same_item else "lamp", "buy1" if same_key else "buy2"))
    assert sorted(result.status_code for result in results) == ([200, 200] if same_key and same_item else [200, 409])
    state = db.member(MEMBER_A)
    assert state.coins == 0 and len(state.owned) == 1
    if not (same_item and same_key):
        expected = "IDEMPOTENCY_CONFLICT" if same_key else "ITEM_ALREADY_OWNED" if same_item else "INSUFFICIENT_COINS"
        assert [result.json()["error"]["code"] for result in results if result.status_code == 409] == [expected]


async def test_unknown_inactive_and_unaffordable_products_do_not_mutate_wallet():
    async with room_api() as (client, db, app, ai):
        db.items["chair"] = item()
        assert_error(await buy(client, "unknown"), 404, "ITEM_NOT_FOUND")
        assert_error(await buy(client), 409, "INSUFFICIENT_COINS")
        db.items["chair"]["is_active"] = False
        assert_error(await buy(client), 404, "ITEM_NOT_FOUND")
        assert db.member(MEMBER_A).coins == 0 and not db.member(MEMBER_A).owned


async def test_placement_bounds_upsert_and_delete_preserve_inventory():
    async with room_api() as (client, db, app, ai):
        db.items["chair"] = item()
        db.member(MEMBER_A).coins = 30
        assert (await buy(client)).status_code == 200
        path = "/api/me/room/placements/chair"
        for x, y in [(230, 310), (1340, 820), (0, 0), (230, 311)]:
            result = await client.put(path, json={"x": x, "y": y})
            assert result.status_code == 200
            assert result.json() == {"item_id": "chair", "position": {"x": x, "y": y}}
        before = deepcopy(db.member(MEMBER_A).placements)
        for x, y in [(1341, 0), (0, 821), (1440, 900)]:
            assert_error(await client.put(path, json={"x": x, "y": y}), 422, "ROOM_POSITION_INVALID")
        assert db.member(MEMBER_A).placements == before
        assert len(before) == 1
        for _ in range(2):
            removed = await client.delete(path)
            assert removed.status_code == 204 and removed.content == b""
        room = (await client.get("/api/me/room")).json()
        assert room["inventory"][0]["position"] is None and len(room["inventory"]) == 1
        assert room["wallet"]["coins"] == 0


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/api/me/room/placements/chair", {"x": 0, "y": 0}),
    ("DELETE", "/api/me/room/placements/chair", None),
    ("PUT", "/api/me/room/theme", {"item_id": "snow"}),
])
async def test_other_members_inventory_cannot_be_placed_removed_or_applied(method, path, body):
    async with room_api() as (client, db, app, ai):
        db.items = {"chair": item(), "snow": item("snow", "theme")}
        db.member(MEMBER_A).coins = 100
        assert (await buy(client)).status_code == 200
        assert (await buy(client, "snow", "theme")).status_code == 200
        response = await client.request(method, path, json=body, headers={"Authorization": "Bearer fake-member-b"})
        assert_error(response, 409, "ITEM_NOT_OWNED")
        other = (await client.get("/api/me/room", headers={"Authorization": "Bearer fake-member-b"})).json()
        assert other["inventory"] == [] and other["wallet"]["coins"] == 0
        shop = await client.get("/api/shop/items", headers={"Authorization": "Bearer fake-member-b"})
        assert all(not product["owned"] for product in shop.json()["items"])


@pytest.mark.parametrize("stage", range(8))
async def test_theme_changes_background_cover_and_only_adult_tree_asset_without_changing_growth(stage):
    async with room_api() as (client, db, app, ai):
        db.items = {"snow": item("snow", "theme"), "chair": item()}
        state = db.member(MEMBER_A)
        state.coins, state.stage = 90, stage
        assert (await buy(client, "snow")).status_code == 200
        assert (await buy(client, "chair", "other")).status_code == 200
        assert_error(await client.put("/api/me/room/placements/snow", json={"x": 0, "y": 0}), 422, "ITEM_NOT_PLACEABLE")
        assert_error(await client.put("/api/me/room/theme", json={"item_id": "chair"}), 404, "ITEM_NOT_FOUND")
        applied = await client.put("/api/me/room/theme", json={"item_id": "snow"})
        assert applied.status_code == 200
        body = applied.json()
        assert body["theme"] == {"item_id": "snow", "background_asset_key": "test/snow/room", "diary_cover_asset_key": "test/snow/cover"}
        assert body["tree"]["stage"] == body["tree"]["days_watered"] == stage
        assert body["tree"]["asset_key"] == ("test/snow/adult" if stage == 7 else BASE_TREE[stage])
        room = (await client.get("/api/me/room")).json()
        assert room["tree"] == body["tree"] and room["room"]["theme"] == body["theme"]
        reset = (await client.put("/api/me/room/theme", json={"item_id": None})).json()
        assert reset["tree"]["asset_key"] == BASE_TREE[stage] and reset["tree"]["stage"] == stage
        assert reset["theme"]["item_id"] is None


async def test_water_once_per_seoul_day_cumulative_nonconsecutive_seven_and_full_grown_limit():
    async with room_api() as (client, db, app, ai):
        db.items["snow"] = item("snow", "theme")
        db.member(MEMBER_A).coins, db.member(MEMBER_A).water = 30, 10
        assert (await buy(client, "snow")).status_code == 200
        await client.put("/api/me/room/theme", json={"item_id": "snow"})
        for stage in range(1, 8):
            request = {"client_request_id": f"water{stage}"}
            first = await client.post("/api/me/tree/water", json=request)
            assert first.status_code == 200
            assert first.json()["tree"] == {"stage": stage, "max_stage": 7, "days_watered": stage,
                "watered_today": True, "asset_key": "test/snow/adult" if stage == 7 else BASE_TREE[stage]}
            assert first.json()["wallet"]["water_drops"] == 10 - stage
            assert (await client.post("/api/me/tree/water", json=request)).json() == first.json()
            assert_error(await client.post("/api/me/tree/water", json={"client_request_id": "extra"}),
                         409, "TREE_FULL_GROWN" if stage == 7 else "TREE_ALREADY_WATERED_TODAY")
            db.now += timedelta(days=2)
        assert_error(await client.post("/api/me/tree/water", json={"client_request_id": "adult"}), 409, "TREE_FULL_GROWN")
        room = (await client.get("/api/me/room")).json()
        assert room["tree"]["stage"] == 7 and room["tree"]["watered_today"] is False
        assert len(db.member(MEMBER_A).events) == 7 and db.member(MEMBER_A).water == 3


async def test_seoul_midnight_and_retry_on_later_day_do_not_consume_twice():
    async with room_api() as (client, db, app, ai):
        db.now = datetime(2026, 11, 10, 14, 59, 59, tzinfo=timezone.utc)
        db.member(MEMBER_A).water = 3
        first = await client.post("/api/me/tree/water", json={"client_request_id": "first"})
        assert first.json()["watered_on"] == "2026-11-10"
        db.now += timedelta(seconds=1)
        room = (await client.get("/api/me/room")).json()
        assert room["as_of_date"] == "2026-11-11" and room["tree"]["watered_today"] is False
        replay = (await client.post("/api/me/tree/water", json={"client_request_id": "first"})).json()
        assert replay["watered_on"] == "2026-11-10" and replay["wallet"]["water_drops"] == 2
        assert replay["tree"]["watered_today"] is False
        second = await client.post("/api/me/tree/water", json={"client_request_id": "second"})
        assert second.json()["watered_on"] == "2026-11-11" and second.json()["tree"]["stage"] == 2
        assert all("date" not in str(request["p_payload"]) for request in db.requests)


@pytest.mark.parametrize("same_key", [False, True])
async def test_concurrent_watering_and_zero_balance_never_overspend(same_key):
    async with room_api() as (client, db, app, ai):
        assert_error(await client.post("/api/me/tree/water", json={"client_request_id": "empty"}), 409, "INSUFFICIENT_WATER")
        db.member(MEMBER_A).water = 1
        results = await asyncio.gather(*(client.post("/api/me/tree/water", json={
            "client_request_id": "same" if same_key else f"water{i}"}) for i in range(6)))
        assert sum(result.status_code == 200 for result in results) == (6 if same_key else 1)
        assert all(result.status_code in {200, 409} for result in results)
        assert db.member(MEMBER_A).water == 0 and db.member(MEMBER_A).stage == 1
        assert len(db.member(MEMBER_A).events) == 1


@pytest.mark.parametrize("action,path,body", [
    ("purchase", "/api/shop/purchases", {"client_request_id": "buy", "item_id": "chair"}),
    ("water", "/api/me/tree/water", {"client_request_id": "water"}),
    ("sync_rewards", "/api/me/rewards/sync", {}),
])
async def test_fake_transaction_rollback_and_response_loss_are_distinguished(action, path, body):
    db = FakeRoomDatabase()
    db.items["chair"] = item()
    db.member(MEMBER_A).coins, db.member(MEMBER_A).water = 30, 1
    diaries(db)
    async with room_api(db) as (client, *_):
        before = deepcopy(db.member(MEMBER_A))
        db.fail_during = action
        failed = await client.post(path, json=body)
        assert_error(failed, 503, "ROOM_STORAGE_UNAVAILABLE", True)
        assert db.member(MEMBER_A) == before and len(db.requests) == 1
        db.fail_during = None
        db.lose_response = action
        assert_error(await client.post(path, json=body), 503, "ROOM_STORAGE_UNAVAILABLE", True)
        committed = deepcopy(db.member(MEMBER_A))
        assert committed != before and len(db.requests) == 2
    # 앱 재생성 후에도 SQL 저장소의 요청 기록을 기준으로 복구한다.
    async with room_api(db) as (client, *_):
        response = await client.post(path, json=body)
        assert response.status_code == 200 and db.member(MEMBER_A) == committed
        if action == "sync_rewards":
            assert response.json()["new_rewards"] == []


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("scenario,status,code", [
    ("missing", 401, "AUTHENTICATION_FAILED"), ("invalid", 401, "AUTHENTICATION_FAILED"),
    ("anonymous", 403, "ACCESS_DENIED"), ("unavailable", 503, "AUTH_UNAVAILABLE"),
])
async def test_all_eight_endpoints_require_verified_nonanonymous_member(method, path, body, scenario, status, code):
    auth = FakeAuth()
    if scenario == "anonymous":
        auth.outcomes.append(httpx.Response(200, json={"id": str(MEMBER_A), "is_anonymous": True,
                                                     "role": "authenticated", "aud": "authenticated"}))
    if scenario == "unavailable":
        auth.outcomes.append(httpx.Response(503))
    async with room_api(auth=auth) as (client, db, app, ai):
        if scenario == "missing":
            client.headers.pop("authorization")
        if scenario == "invalid":
            client.headers["authorization"] = "Bearer fake-invalid"
        assert_error(await client.request(method, path, json=body), status, code, scenario == "unavailable")
        assert not db.requests and not ai.requests


@pytest.mark.parametrize("path,body", [
    ("/api/me/rewards/sync", {"member_id": str(MEMBER_B)}),
    ("/api/me/rewards/sync", {"coins": 999}),
    ("/api/shop/purchases", {"item_id": "chair", "client_request_id": "id", "price_coins": 0}),
    ("/api/shop/purchases", {"item_id": "chair", "client_request_id": "id", "member_id": str(MEMBER_B)}),
    ("/api/shop/purchases", {"item_id": "chair", "client_request_id": "id", "wallet": {"coins": 999}}),
    ("/api/shop/purchases", {"item_id": "chair", "client_request_id": ""}),
    ("/api/shop/purchases", {"item_id": "chair", "client_request_id": "x" * 129}),
    ("/api/shop/purchases", {"item_id": "chair", "client_request_id": True}),
    ("/api/me/tree/water", {"client_request_id": "id", "watered_on": "2000-01-01"}),
    ("/api/me/tree/water", {"client_request_id": "id", "growth_points": 7}),
])
async def test_client_cannot_override_member_prices_balances_dates_or_rewards(path, body):
    async with room_api() as (client, db, app, ai):
        assert_error(await client.post(path, json=body), 422, "VALIDATION_ERROR")
        assert not db.requests


@pytest.mark.parametrize("body", [{"x": True, "y": 0}, {"x": "0", "y": 0}, {"x": -1, "y": 0},
                                  {"x": 0, "y": 901}, {"x": 0}, {"x": None, "y": 0},
                                  {"x": 1.0, "y": 0}, {"x": 0, "y": 1.5}, {"x": 0, "y": False},
                                  {"x": 0, "y": 0, "member_id": str(MEMBER_B)}])
async def test_placement_input_rejects_non_numeric_missing_and_forged_fields(body):
    async with room_api() as (client, db, app, ai):
        assert_error(await client.put("/api/me/room/placements/chair", json=body), 422, "ROOM_POSITION_INVALID")
        assert not db.requests


@pytest.mark.parametrize("body", [{}, {"item_id": True}, {"item_id": "snow", "stage": 7}])
async def test_theme_requires_explicit_nullable_item_id(body):
    async with room_api() as (client, db, app, ai):
        assert_error(await client.put("/api/me/room/theme", json=body), 422, "VALIDATION_ERROR")
        assert not db.requests


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


async def test_openapi_eight_routes_cors_put_delete_and_no_client_member_field():
    async with room_api() as (client, db, app, ai):
        schema = (await client.get("/openapi.json")).json()
        for method, path, _ in ROUTES:
            route = path.replace("placements/chair", "placements/{item_id}")
            operation = schema["paths"][route][method.lower()]
            assert operation["security"] == [{"HTTPBearer": []}]
            assert str(204 if method == "DELETE" else 200) in operation["responses"]
        for method in ("PUT", "DELETE"):
            response = await client.options("/api/me/room/placements/chair", headers={
                "Origin": "http://127.0.0.1:5500", "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Content-Type,Authorization"})
            assert response.status_code == 200
        assert not db.requests


async def test_rewards_need_no_configuration():
    assert not any("reward" in field for field in Settings.model_fields)


async def test_room_environment_configuration_keeps_session_default(monkeypatch):
    monkeypatch.delenv("SESSION_STORE_MODE", raising=False)
    assert Settings.from_env().session_store_mode == "memory"


async def test_overlapping_placements_and_theme_changes_preserve_member_layout():
    async with room_api() as (client, db, app, ai):
        db.items = {"chair": item(), "lamp": item("lamp", "decoration"), "snow": item("snow", "theme")}
        db.member(MEMBER_A).coins = 90
        for key in db.items:
            assert (await buy(client, key, key)).status_code == 200
        for key in ("chair", "lamp"):
            placed = await client.put(f"/api/me/room/placements/{key}", json={"x": 230, "y": 310})
            assert placed.status_code == 200
            assert type(placed.json()["position"]["x"]) is int
        before = deepcopy(db.member(MEMBER_A).placements)
        await client.put("/api/me/room/theme", json={"item_id": "snow"})
        await client.put("/api/me/room/theme", json={"item_id": None})
        assert db.member(MEMBER_A).placements == before and len(before) == 2
        # query의 회원 ID도 검증된 토큰의 회원을 바꾸지 못한다.
        room = await client.get("/api/me/room", params={"member_id": str(MEMBER_B)})
        assert len(room.json()["inventory"]) == 3
        assert db.requests[-1]["p_member_id"] == str(MEMBER_A)


async def test_reward_purchase_and_water_concurrency_does_not_lose_wallet_updates_in_fake_model():
    db = FakeRoomDatabase()
    db.items["chair"] = item()
    db.member(MEMBER_A).coins, db.member(MEMBER_A).water = 30, 1
    diaries(db)
    async with room_api(db) as (client, *_):
        results = await asyncio.gather(
            client.post("/api/me/rewards/sync", json={}), buy(client),
            client.post("/api/me/tree/water", json={"client_request_id": "water"}),
        )
        assert all(result.status_code == 200 for result in results)
        state = db.member(MEMBER_A)
        assert (state.coins, state.water, state.stage) == (30, 1, 1)
        assert len(state.grants) == len(state.owned) == len(state.events) == 1


@pytest.mark.parametrize("content", [b'{"x":NaN,"y":0}', b'{"x":Infinity,"y":0}',
                                     b'{"x":0,"y":', b'null', b'{}'])
async def test_invalid_placement_json_is_sanitized_as_position_error(content):
    async with room_api() as (client, db, app, ai):
        response = await client.put("/api/me/room/placements/chair", content=content,
                                    headers={"Content-Type": "application/json"})
        assert_error(response, 422, "ROOM_POSITION_INVALID")
        assert not db.requests
