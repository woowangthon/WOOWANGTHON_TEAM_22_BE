"""요청별 복구 HTTP 모형. 실제 PostgreSQL/RLS/트랜잭션 검증을 대신하지 않는다."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import uuid4

import httpx
import pytest

from tests.conftest import MEMBER_A, MEMBER_B
from tests.fake_edits import EditDatabase
from tests.test_drawings import picture, saved_diary, upload
from tests.test_edits_refunds import api, edit
from tests.test_saved_diaries import FAKE_SECRET, assert_error
from app.clients.request_recovery import RequestRecoveryRepository
from app.clients.room import room_storage_unavailable
from app.errors import AppError

pytestmark = pytest.mark.anyio
WATER = "/api/me/tree/water"


def fingerprint(key="edit-1", version=1, text="수정한 일기", image=None):
    # 기존 PUT fingerprint와 독립적으로 구성한 FE 측 입력 모형.
    value = {"client_request_id": key, "confirmed_text": text, "expected_version": version,
             "file_sha256": sha256(image).hexdigest() if image is not None else None}
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


async def lookup(client, did, key="edit-1", version=1, text="수정한 일기", image=None, **kwargs):
    return await client.get(f"/api/diaries/{did}/edits/{key}", params={
        "fingerprint": fingerprint(key, version, text, image), "expected_version": version,
    }, **kwargs)


class RecoveryDatabase(EditDatabase):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.read_outcome = None
        self.pause_action = None
        self.started, self.release = asyncio.Event(), asyncio.Event()

    async def __call__(self, request):
        params, path = request.url.params, request.url.path
        if request.method == "POST" and self.pause_action:
            action = json.loads(request.content).get("p_action")
            if action == self.pause_action:
                self.started.set()
                await self.release.wait()
        if (path in {"/rest/v1/diary_edit_requests", "/rest/v1/member_tree_watering_events"}
                and "client_request_id" in params):
            self.reads.append(request)
            assert request.method == "GET"
            assert request.headers["apikey"] == FAKE_SECRET
            assert "authorization" not in request.headers
            assert params["limit"] == "2"
            key = params["client_request_id"][3:]
            if path.endswith("diary_edit_requests"):
                assert set(params) == {"select", "user_id", "client_request_id", "limit"}
                rows = [r for (owner, k), r in self.edits.items()
                        if owner == params["user_id"][3:] and k == key]
            else:
                # 오늘 날짜 필터를 넣으면 이 검사가 실패한다.
                assert set(params) == {"select", "member_id", "client_request_id", "limit"}
                owner = params["member_id"][3:]
                member = self.room.members.get(owner)
                rows = [{"member_id": owner, "client_request_id": key, "watered_on": day}
                        for k, day in (member.events.items() if member else []) if k == key]
            rows = [{k: deepcopy(r[k]) for k in params["select"].split(",")} for r in rows]
            if self.read_outcome is not None:
                if isinstance(self.read_outcome, Exception):
                    raise self.read_outcome
                if callable(self.read_outcome):
                    rows = self.read_outcome(rows)
                else:
                    return self.read_outcome
            return httpx.Response(200, json=rows)
        return await super().__call__(request)


def snapshot(db, storage):
    return deepcopy((db.diaries, db.edits, db.room.members, storage.objects,
                     len(db.requests), len(db.room.requests), len(storage.requests)))


@pytest.mark.parametrize("failure,expected", [
    ("finish_lost", "completed"), ("reserve_lost", "unknown"),
    ("finish_rollback", "unknown"), ("reserve_rollback", "unknown"),
    ("upload_lost", "unknown"), ("sign", "completed"),
])
async def test_lost_edit_response_is_read_only_and_survives_restart(failure, expected):
    db = RecoveryDatabase()
    image = picture()
    async with api(db) as (client, _, storage):
        did = (await saved_diary(client))["diary_id"]
        if failure in {"upload_lost", "sign"}:
            storage.failure = failure
        else:
            db.edit_failure = failure
        assert (await edit(client, did, image=image)).status_code == 503
    async with api(db, storage) as (client, _, _):
        before = snapshot(db, storage)
        results = await asyncio.gather(*(lookup(client, did, image=image) for _ in range(3)))
        for result in results:
            assert result.status_code == 200, result.text
            assert result.json()["status"] == expected
            assert result.json()["edit_version"] == (2 if expected == "completed" else None)
            assert result.headers["cache-control"] == "no-store"
        # owned_diary SELECT만 증가한다. RPC/Storage/영수증/본문은 그대로다.
        after = snapshot(db, storage)
        assert after[:4] == before[:4] and after[5:] == before[5:]
        assert all(r.method == "GET" for r in db.requests[before[4]:])


@pytest.mark.parametrize("pending", [False, True])
async def test_other_request_same_text_different_picture_never_completes_mine(pending):
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        if pending:
            db.edit_failure = "reserve_lost"
            assert (await edit(client, did, image=picture())).status_code == 503
        assert (await edit(client, did, key="other", image=picture(color="blue"))).status_code == 200
        result = await lookup(client, did, image=picture())
        assert result.status_code == 200 and result.json()["status"] == "unknown"
        assert result.json()["edit_version"] is None
        assert db.diaries[did]["edit_version"] == 2


async def test_original_receipt_remains_completed_after_later_edit():
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        assert (await edit(client, did, image=picture())).status_code == 200
        assert (await edit(client, did, version=2, key="later", image=picture(color="blue"))).status_code == 200
        result = await lookup(client, did, image=picture())
        assert result.json()["status"] == "completed" and result.json()["edit_version"] == 2
        assert db.diaries[did]["edit_version"] == 3


@pytest.mark.parametrize("mode", ["text_only", "keep_original", "keep_edit"])
async def test_text_receipt_with_optional_inherited_drawing(mode):
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        version = 1
        if mode == "keep_original":
            assert (await upload(client, did)).status_code == 200
        elif mode == "keep_edit":
            assert (await edit(client, did, key="first", image=picture())).status_code == 200
            version = 2
        assert (await edit(client, did, version=version)).status_code == 200
        result = await lookup(client, did, version=version)
        assert result.status_code == 200 and result.json()["status"] == "completed"


@pytest.mark.parametrize("state", ["pending", "completed"])
@pytest.mark.parametrize("mismatch", ["text", "image", "version", "diary"])
async def test_same_key_mismatched_input_is_conflict(state, mismatch):
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        if state == "pending":
            db.edit_failure = "reserve_lost"
        await edit(client, did, image=picture())
        kwargs = {"image": picture()}
        if mismatch == "text":
            kwargs["text"] = "다른 글"
        elif mismatch == "image":
            kwargs["image"] = picture(color="blue")
        elif mismatch == "version":
            kwargs["version"] = 2
        else:
            other = str(uuid4())
            db.diaries[other] = {**deepcopy(db.diaries[did]), "id": other}
            did = other
        assert_error(await lookup(client, did, **kwargs), 409, "IDEMPOTENCY_CONFLICT")


async def test_edit_foreign_missing_deleted_diary_never_reads_receipts():
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        await edit(client, did)
        assert_error(await lookup(client, did, headers={"Authorization": "Bearer fake-member-b"}),
                     404, "DIARY_NOT_FOUND")
        assert_error(await lookup(client, str(uuid4())), 404, "DIARY_NOT_FOUND")
        db.diaries[did]["deleted_at"] = "2026-11-11T00:00:00+00:00"
        assert_error(await lookup(client, did), 404, "DIARY_NOT_FOUND")
        assert not db.reads


@pytest.mark.parametrize("day_offset", [0, 1, 30])
async def test_water_lost_response_recovers_original_day_only(day_offset):
    db = RecoveryDatabase()
    db.room.now = datetime(2026, 11, 10, 14, 59, 59, tzinfo=timezone.utc)
    async with api(db) as (client, _, storage):
        await saved_diary(client)
        db.room.lose_response = "water"
        assert_error(await client.post(WATER, json={"client_request_id": "water-1"}),
                     503, "ROOM_STORAGE_UNAVAILABLE")
        db.room.now += timedelta(days=day_offset, seconds=1 if day_offset else 0)
        room = (await client.get("/api/me/room")).json()
        assert room["tree"]["watered_today"] == (day_offset == 0)
        before = snapshot(db, storage)
        for _ in range(3):
            result = await client.get(WATER + "/water-1")
            assert result.json() == {"client_request_id": "water-1", "status": "completed",
                                     "watered_on": "2026-11-10"}
        assert snapshot(db, storage) == before
        assert result.headers["cache-control"] == "no-store"


async def test_water_seoul_midnight_and_different_key_owner():
    async with api(RecoveryDatabase()) as (client, db, storage):
        await saved_diary(client)
        db.room.now = datetime(2026, 11, 10, 14, 59, 59, tzinfo=timezone.utc)
        assert (await client.post(WATER, json={"client_request_id": "yesterday"})).status_code == 200
        db.room.now += timedelta(seconds=1)
        # 오늘 false이거나 다른 키로 오늘 물을 줘도 missing 요청의 증거가 되지 않는다.
        for watered in [False, True]:
            if watered:
                db.room.member(MEMBER_A).water = 1
                assert (await client.post(WATER, json={"client_request_id": "today"})).status_code == 200
            assert (await client.get(WATER + "/missing")).json()["status"] == "unknown"
            assert (await client.get(WATER + "/yesterday")).json()["watered_on"] == "2026-11-10"
        assert (await client.get(WATER + "/today")).json()["watered_on"] == "2026-11-11"
        result = await client.get(WATER + "/yesterday", headers={"Authorization": "Bearer fake-member-b"})
        assert result.json() == {"client_request_id": "yesterday", "status": "unknown", "watered_on": None}


@pytest.mark.parametrize("kind", ["edit", "water"])
async def test_inflight_write_absence_stays_unknown_then_completes(kind):
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        db.pause_action = "reserve" if kind == "edit" else "water"
        pending = asyncio.create_task(edit(client, did) if kind == "edit" else
                                      client.post(WATER, json={"client_request_id": "water-1"}))
        try:
            await asyncio.wait_for(db.started.wait(), 2)
            for _ in range(2):
                result = await (lookup(client, did) if kind == "edit" else client.get(WATER + "/water-1"))
                assert result.status_code == 200 and result.json()["status"] == "unknown"
        finally:
            db.release.set()
            finished = await pending
        assert finished.status_code == 200
        result = await (lookup(client, did) if kind == "edit" else client.get(WATER + "/water-1"))
        assert result.json()["status"] == "completed"


@pytest.mark.parametrize("kind", ["edit", "water"])
@pytest.mark.parametrize("failure", ["timeout", "db500", "permission", "missing_table", "partial",
                                    "invalid_json", "object", "duplicates", "wrong_owner", "wrong_key"])
async def test_read_failure_never_becomes_absence_or_completed(kind, failure):
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        await edit(client, did)
        await client.post(WATER, json={"client_request_id": "water-1"})
        db.read_outcome = {
            "timeout": httpx.ReadTimeout("synthetic"), "db500": httpx.Response(500),
            "permission": httpx.Response(403), "missing_table": httpx.Response(404),
            "partial": httpx.Response(206, json=[]), "invalid_json": httpx.Response(200, text="oops"),
            "object": httpx.Response(200, json={}), "duplicates": lambda rows: rows * 2,
            "wrong_owner": lambda rows: [{**rows[0], "user_id" if kind == "edit" else "member_id": str(MEMBER_B)}],
            "wrong_key": lambda rows: [{**rows[0], "client_request_id": "different"}],
        }[failure]
        before = len(db.reads)
        result = await (lookup(client, did) if kind == "edit" else client.get(WATER + "/water-1"))
        assert_error(result, 503, "DIARY_EDIT_STORAGE_UNAVAILABLE" if kind == "edit" else "ROOM_STORAGE_UNAVAILABLE")
        assert len(db.reads) == before + 1  # 자동 재시도 없음.


@pytest.mark.parametrize("corruption", ["missing_result", "version", "text", "result_diary", "drawing",
                                       "not_ready", "request_id", "state", "string_version"])
async def test_inconsistent_edit_receipt_cannot_confirm_success(corruption):
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        await edit(client, did, image=picture())
        r = db.edits[str(MEMBER_A), "edit-1"]
        if corruption == "missing_result":
            r["result"] = None
        elif corruption == "version":
            r["result"]["edit_version"] = 3
        elif corruption == "text":
            r["result"]["confirmed_text"] = "다른 글"
        elif corruption == "result_diary":
            r["result"]["diary_id"] = str(uuid4())
        elif corruption == "drawing":
            r["result"]["original_drawing"]["sha256"] = "0" * 64
        elif corruption == "not_ready":
            r["result"]["original_drawing"]["upload_status"] = "pending"
        elif corruption == "request_id":
            r["request_id"] = str(uuid4())
        elif corruption == "state":
            r["state"] = "pending"
        else:
            r["expected_version"] = "1"
        assert_error(await lookup(client, did, image=picture()), 503, "DIARY_EDIT_STORAGE_UNAVAILABLE")


@pytest.mark.parametrize("kind", ["edit", "water"])
async def test_authentication_required(kind):
    async with api(RecoveryDatabase()) as (client, db, storage):
        path = f"/api/diaries/{uuid4()}/edits/key" if kind == "edit" else WATER + "/key"
        assert_error(await client.get(path, headers={"Authorization": ""}), 401, "AUTHENTICATION_FAILED")
        assert not db.reads


@pytest.mark.parametrize("params", [{}, {"fingerprint": "x", "expected_version": 1},
    {"fingerprint": "a" * 64, "expected_version": 0},
    {"fingerprint": "a" * 64, "expected_version": 2147483647},
    {"fingerprint": "a" * 64, "expected_version": 1, "user_id": str(MEMBER_A)}])
async def test_invalid_edit_query(params):
    async with api(RecoveryDatabase()) as (client, db, storage):
        assert_error(await client.get(f"/api/diaries/{uuid4()}/edits/key", params=params),
                     422, "VALIDATION_ERROR")
        assert not db.reads


async def test_openapi_marks_both_routes_as_proposals():
    async with api(RecoveryDatabase()) as (client, db, storage):
        paths = (await client.get("/openapi.json")).json()["paths"]
        for path in ["/api/diaries/{diary_id}/edits/{client_request_id}", WATER + "/{client_request_id}"]:
            assert set(paths[path]) == {"get"}
            assert "제안 계약" in paths[path]["get"]["description"]


@pytest.mark.parametrize("day", [None, "not-a-date", "2026-02-30", 20261111])
async def test_invalid_water_receipt_date_is_unavailable(day):
    async with api(RecoveryDatabase()) as (client, db, storage):
        await saved_diary(client)
        await client.post(WATER, json={"client_request_id": "water-1"})
        db.read_outcome = lambda rows: [{**rows[0], "watered_on": day}]
        assert_error(await client.get(WATER + "/water-1"), 503, "ROOM_STORAGE_UNAVAILABLE")


@pytest.mark.parametrize("kind", ["edit", "water"])
async def test_invalid_request_key_is_rejected(kind):
    async with api(RecoveryDatabase()) as (client, db, storage):
        path = f"/api/diaries/{uuid4()}/edits/bad.key" if kind == "edit" else WATER + "/bad.key"
        assert_error(await client.get(path, params={"fingerprint": "a" * 64, "expected_version": 1}),
                     422, "VALIDATION_ERROR")
        assert not db.reads


async def test_repository_total_timeout_and_missing_configuration():
    calls = []

    async def stalled(request):
        calls.append(request)
        await asyncio.sleep(1)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(base_url="https://db.test", transport=httpx.MockTransport(stalled)) as writer:
        for connection in [None, writer]:
            repository = RequestRecoveryRepository(connection, 0.01)
            with pytest.raises(AppError) as error:
                await repository.receipt("member_tree_watering_events", "member_id,client_request_id,watered_on",
                                         "member_id", MEMBER_A, "key", room_storage_unavailable)
            assert error.value.code == "ROOM_STORAGE_UNAVAILABLE"
        assert len(calls) == 1


async def test_expected_version_is_checked_independently_of_fingerprint():
    async with api(RecoveryDatabase()) as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        await edit(client, did)
        assert_error(await client.get(f"/api/diaries/{did}/edits/edit-1", params={
            "fingerprint": fingerprint(), "expected_version": 2,
        }), 409, "IDEMPOTENCY_CONFLICT")
