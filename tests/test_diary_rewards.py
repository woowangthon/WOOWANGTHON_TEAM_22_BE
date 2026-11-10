"""확정→별도 지급→재시작/복구의 HTTP 모형. 실제 PostgreSQL 검증은 아니다."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeAI, FakeAuth, MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY
from tests.fake_room import FakeRoomDatabase
from tests.fake_sessions import FakeSessionRepository
from tests.test_saved_diaries import FakeDatabase, FAKE_SECRET, prepare

pytestmark = pytest.mark.anyio


class CombinedDatabase:
    def __init__(self):
        self.saved = FakeDatabase()
        self.room = FakeRoomDatabase()
        self.room.secret = FAKE_SECRET

    async def __call__(self, request):
        if request.url.path == "/rest/v1/rpc/member_room_command":
            self.refresh()
            return await self.room(request)
        return await self.saved(request)

    def refresh(self):
        self.room.diaries = [
            {"id": row["id"], "member": row["user_id"], "date": row["diary_date"],
             "confirmed": row["confirmation_fingerprint"] is not None}
            for row in self.saved.diaries.values()
        ]


@asynccontextmanager
async def rewards_api(db, mode="memory", sessions=None, deadline=10):
    app = create_app(
        Settings(session_store_mode=mode, supabase_publishable_key=TEST_PUBLISHABLE_KEY,
                 supabase_secret_key=FAKE_SECRET, db_request_timeout_seconds=deadline),
        httpx.MockTransport(FakeAI()), httpx.MockTransport(FakeAuth()), httpx.MockTransport(db),
        session_repository=sessions,
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://be.test",
        headers={"Authorization": "Bearer fake-member-a"},
    ) as client:
        yield client, app


async def sync(client):
    result = await client.post("/api/me/rewards/sync", json={})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("mode", ["memory", "supabase"])
async def test_confirm_grants_once_and_replays_after_restart_without_session(mode):
    db = CombinedDatabase()
    sessions = FakeSessionRepository(db.saved)
    async with rewards_api(db, mode, sessions) as (client, app):
        path, body, _ = await prepare(client)
        assert not db.room.requests  # 대화·초안에는 지급하지 않는다.
        first = await client.post(path, json=body)
        assert first.status_code == 200
        state = db.room.member(MEMBER_A)
        assert (state.coins, state.water) == (30, 1)
        assert set(state.grants) == {first.json()["diary_id"]}
        assert (await client.post(path, json=body)).json() == first.json()
    async with rewards_api(db, mode, sessions) as (client, app):
        assert (await client.post(path, json=body)).json() == first.json()
        assert await sync(client) == {"new_rewards": [], "wallet": {"coins": 30, "water_drops": 1}}
        assert len(db.saved.diaries) == len(db.room.member(MEMBER_A).grants) == 1


@pytest.mark.parametrize("failure", ["rollback", "response_loss", "unexpected", "timeout"])
async def test_reward_failures_keep_saved_diary_and_recover_after_restart(failure, caplog):
    db = CombinedDatabase()
    original = db.room.__class__.__call__

    class FailedRoom(FakeRoomDatabase):
        async def __call__(self, request):
            if failure == "unexpected":
                raise RuntimeError("private-token-child-text")
            if failure == "timeout":
                await asyncio.Event().wait()
            return await original(self, request)

    db.room = FailedRoom()
    db.room.secret = FAKE_SECRET
    if failure == "rollback":
        db.room.fail_during = "sync_rewards"
    if failure == "response_loss":
        db.room.lose_response = "sync_rewards"
    async with rewards_api(db, deadline=0.02) as (client, _):
        path, body, _ = await prepare(client)
        saved = await client.post(path, json=body)
        assert saved.status_code == 200
        assert len(db.saved.diaries) == 1
        assert db.room.member(MEMBER_A).coins == (30 if failure == "response_loss" else 0)
        assert "DIARY_REWARD_SYNC_DEFERRED" in caplog.text
        assert "private-token-child-text" not in caplog.text
    # 장애 주입만 제거하고 DB 상태는 보존한다.
    db.room.__class__ = FakeRoomDatabase
    db.room.fail_during = None
    async with rewards_api(db) as (client, _):
        result = await sync(client)
        assert len(result["new_rewards"]) == (0 if failure == "response_loss" else 1)
        assert result["wallet"] == {"coins": 30, "water_drops": 1}
        assert (await client.post(path, json=body)).json() == saved.json()
        assert (await sync(client))["new_rewards"] == []


@pytest.mark.parametrize("response_lost", [False, True])
async def test_failed_confirmation_or_lost_confirm_response_cannot_double_pay(response_lost):
    db = CombinedDatabase()
    async with rewards_api(db) as (client, _):
        path, body, _ = await prepare(client)
        db.saved.fail_message_insert = not response_lost
        db.saved.lose_response = response_lost
        assert (await client.post(path, json=body)).status_code == 503
        assert not db.room.requests  # 확정 응답 전 프로세스 중단과 같은 복구 경계
        assert len(db.saved.diaries) == int(response_lost)
    db.saved.fail_message_insert = False
    async with rewards_api(db) as (client, _):
        assert (await client.post(path, json=body)).status_code == (200 if response_lost else 404)
        result = await sync(client)
        assert result["wallet"]["coins"] == (30 if response_lost else 0)


async def test_existing_confirmed_diary_replay_is_excluded_but_other_member_new_save_is_paid():
    db = CombinedDatabase()
    async with rewards_api(db) as (client, app):
        path, body, _ = await prepare(client)
        # 이전 버전 BE로 저장한 뒤 SQL 적용 시 보류 목록으로 고정한 상황.
        app.state.diary_service.rewards = None
        old = await client.post(path, json=body)
        db.room.legacy_diary_ids = set(db.saved.diaries)
    async with rewards_api(db) as (client, _):
        assert (await client.post(path, json=body)).json() == old.json()
        assert (await sync(client))["wallet"] == {"coins": 0, "water_drops": 0}
        client.headers["Authorization"] = "Bearer fake-member-b"
        path, body, _ = await prepare(client)
        assert (await client.post(path, json=body)).status_code == 200
        assert (await sync(client))["wallet"] == {"coins": 30, "water_drops": 1}
        assert db.room.member(MEMBER_A).grants == {}
        assert len(db.room.member(MEMBER_B).grants) == 1


async def test_concurrent_save_replay_and_sync_from_two_apps_pay_once():
    db = CombinedDatabase()
    async with rewards_api(db) as (first, _), rewards_api(db) as (second, _):
        path, body, _ = await prepare(first)
        assert (await first.post(path, json=body)).status_code == 200
        responses = await asyncio.gather(*[
            second.post(path, json=body), first.post(path, json=body),
            second.post("/api/me/rewards/sync", json={}), first.post("/api/me/rewards/sync", json={}),
        ])
        assert all(response.status_code == 200 for response in responses)
        assert (db.room.member(MEMBER_A).coins, db.room.member(MEMBER_A).water) == (30, 1)
        assert len(db.room.member(MEMBER_A).grants) == 1


@pytest.mark.parametrize("same_date", [False, True])
async def test_reward_identity_preserves_diary_id_and_prevents_same_date_regrant(same_date):
    # 삭제 후 새 ID가 생겨도 회원/서울 일기 날짜 지급 기록은 유지된다.
    db = CombinedDatabase()
    async with rewards_api(db) as (client, _):
        path, body, _ = await prepare(client)
        assert (await client.post(path, json=body)).status_code == 200
        new_row = deepcopy(next(iter(db.saved.diaries.values())))
        new_row["id"] = "00000000-0000-4000-8000-000000000099"
        if not same_date:
            new_row["diary_date"] = (date.fromisoformat(new_row["diary_date"]) + timedelta(days=1)).isoformat()
        db.saved.diaries[new_row["id"]] = new_row
        count = 1 if same_date else 2
        assert (await sync(client))["wallet"] == {"coins": 30 * count, "water_drops": count}
        assert len(db.room.member(MEMBER_A).grants) == count


async def test_two_distinct_days_confirm_through_unchanged_api(monkeypatch):
    db = CombinedDatabase()
    async with rewards_api(db) as (client, _):
        path, body, _ = await prepare(client)
        assert (await client.post(path, json=body)).status_code == 200

        class Tomorrow(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(days=1)

        monkeypatch.setattr("app.services.diaries.datetime", Tomorrow)
        path, body, _ = await prepare(client, key="second-day")
        body["client_request_id"] = "confirm-second-day"
        assert (await client.post(path, json=body)).status_code == 200
        assert len(db.saved.diaries) == len(db.room.member(MEMBER_A).grants) == 2
        assert (await sync(client))["wallet"] == {"coins": 60, "water_drops": 2}
