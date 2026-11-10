"""오프라인 API 검증. 실제 PostgreSQL 권한·RLS·경합/Storage 테스트가 아니다."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeAI, FakeAuth, MEMBER_A, TEST_PUBLISHABLE_KEY
from tests.fake_edits import EditDatabase
from tests.fake_room import item
from tests.test_drawings import DrawingStorage, picture, saved_diary, upload
from tests.test_saved_diaries import FAKE_SECRET, assert_error, prepare

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def api(db=None, storage=None):
    db, storage = db or EditDatabase(), storage or DrawingStorage()
    app = create_app(Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY,
                              supabase_secret_key=FAKE_SECRET),
                     httpx.MockTransport(FakeAI()), httpx.MockTransport(FakeAuth()),
                     httpx.MockTransport(db), httpx.MockTransport(storage))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        base_url="http://be.test", transport=httpx.ASGITransport(app),
        headers={"Authorization": "Bearer fake-member-a"},
    ) as client:
        yield client, db, storage


async def edit(client, did, text="수정한 일기", version=1, key="edit-1", image=None, extra=None):
    data = {"client_request_id": key, "expected_version": str(version), "confirmed_text": text,
            **(extra or {})}
    files = {name: (None, value) for name, value in data.items()}
    if image is not None:
        files["file"] = ("drawing.png", image, "image/png")
    return await client.put(f"/api/diaries/{did}/edits", files=files)


def delete_headers(version=1, key="delete-1"):
    return {"Idempotency-Key": key, "If-Match": f'"{version}"'}


@pytest.mark.parametrize("mode", ["text", "drawing", "both"])
async def test_atomic_edit_preserves_originals_and_no_rewards(mode):
    async with api() as (client, db, storage):
        original = await saved_diary(client)
        did = original["diary_id"]
        old_asset = (await upload(client, did)).json()["asset"]
        old_bytes = deepcopy(storage.objects)
        before = deepcopy(db.diaries[did])
        messages = deepcopy(db.messages[did])
        text = original["confirmed_text"] if mode == "drawing" else "수정한 일기"
        image = None if mode == "text" else picture(color="blue")
        result = await edit(client, did, text=text, image=image)
        assert result.status_code == 200, result.text
        body = result.json()
        assert set(body) == {"diary_id", "edit_version", "confirmed_text", "updated_at", "original_drawing"}
        assert body["edit_version"] == 2 and body["confirmed_text"] == text
        assert (body["original_drawing"]["asset_id"] == old_asset["asset_id"]) == (mode == "text")
        detail = (await client.get(f"/api/diaries/{did}")).json()
        assert detail["edit_version"] == 2 and detail["updated_at"] == body["updated_at"]
        assert detail["assets"][0]["asset_id"] == body["original_drawing"]["asset_id"]
        assert detail["ai_draft"] == original["ai_draft"] and db.messages[did] == messages
        assert db.diaries[did]["confirmation_fingerprint"] == before["confirmation_fingerprint"]
        assert all(storage.objects[k] == v for k, v in old_bytes.items())
        state = db.room.member(MEMBER_A)
        assert (state.coins, state.water) == (30, 1)
        assert len(db.room.requests) == 1


@pytest.mark.parametrize("failure", ["reserve_lost", "finish_lost", "finish_rollback", "reserve_rollback", "upload_lost", "offline", "sign"])
async def test_failed_or_unknown_edit_recovers_with_same_id_after_restart(failure):
    db, storage = EditDatabase(), DrawingStorage()
    async with api(db, storage) as (client, _, _):
        diary = await saved_diary(client)
        did = diary["diary_id"]
        if failure in {"upload_lost", "offline", "sign"}:
            storage.failure = failure
        else:
            db.edit_failure = failure
        result = await edit(client, did, image=picture())
        assert result.status_code == 503
        expected = 2 if failure in {"finish_lost", "sign"} else 1
        assert db.diaries[did]["edit_version"] == expected
        if expected == 1:
            assert db.diaries[did]["confirmed_text"] == diary["confirmed_text"]
            assert db.diaries[did]["active_drawing_edit_id"] is None
    storage.failure = None
    async with api(db, storage) as (client, _, _):
        result = await edit(client, did, image=picture())
        assert result.status_code == 200, result.text
        assert result.json()["edit_version"] == 2
        assert len(db.edits) == 1 and len(storage.objects) == 1
        assert_error(await edit(client, did, text="다른 본문", image=picture()), 409, "IDEMPOTENCY_CONFLICT")
        assert_error(await edit(client, did, image=picture(color="blue")), 409, "IDEMPOTENCY_CONFLICT")


async def test_replay_returns_original_revision_and_optimistic_concurrent_conflict():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        first = await edit(client, did)
        assert first.status_code == 200
        results = await asyncio.gather(edit(client, did, version=2, key="next-1"),
                                       edit(client, did, version=2, key="next-2"))
        assert sorted(r.status_code for r in results) == [200, 409]
        assert (await edit(client, did)).json() == first.json()
        assert db.diaries[did]["edit_version"] == 3


async def test_text_edit_without_drawing_and_after_drawing_revision():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        assert (await edit(client, did)).json()["original_drawing"] is None
        image = await edit(client, did, version=2, key="image", image=picture())
        text = await edit(client, did, version=3, key="text")
        assert text.json()["original_drawing"]["asset_id"] == image.json()["original_drawing"]["asset_id"]
        assert db.diaries[did]["edit_version"] == 4


@pytest.mark.parametrize("fields", [{"confirmed_text": " "}, {"expected_version": "0"},
                                  {"expected_version": "1.0"}, {"expected_version": "true"},
                                  {"expected_version": "2147483647"}, {"user_id": str(MEMBER_A)},
                                  {"confirmed_text": "a" * 20001}, {"client_request_id": "../bad"}])
async def test_invalid_edits_never_write(fields):
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        assert_error(await edit(client, did, extra=fields), 422, "DIARY_EDIT_INVALID")
        assert not db.edits and not storage.objects


@pytest.mark.parametrize("data", [b"", b"not png", b"<svg/>"])
async def test_invalid_image_is_rejected_before_reservation(data):
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        assert_error(await edit(client, did, image=data), 422, "DRAWING_INVALID_IMAGE")
        assert not db.edits








async def purchase(client, key="buy-1", product="chair"):
    return await client.post("/api/shop/purchases", json={"client_request_id": key, "item_id": product})


async def refund(client, key="refund-1", product="chair"):
    return await client.post("/api/shop/refunds", json={"client_request_id": key, "item_id": product})










async def test_conflicting_pending_edit_never_changes_visible_drawing_and_corrupt_blob_blocks_finish():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        await upload(client, did)
        db.edit_failure = "finish_rollback"
        assert (await edit(client, did, image=picture(color="blue"))).status_code == 503
        pending = next(iter(db.edits.values()))
        key = pending["drawing"]["storage_key"]
        storage.objects[key] = b"corrupt synthetic object"
        assert (await edit(client, did, image=picture(color="blue"))).status_code == 503
        assert db.diaries[did]["edit_version"] == 1
        assert (await edit(client, did, key="another", text="먼저 저장")).status_code == 200
        assert_error(await edit(client, did, image=picture(color="blue")), 409, "DIARY_EDIT_VERSION_CONFLICT")
        assert db.diaries[did]["confirmed_text"] == "먼저 저장"


async def test_initial_drawing_upload_cannot_change_edited_revision():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        await edit(client, did)
        assert_error(await upload(client, did), 409, "DIARY_EDIT_VERSION_CONFLICT")
        assert not storage.objects








@pytest.mark.parametrize("kind", ["duplicate", "missing", "json", "empty_file_field"])
async def test_multipart_contract_rejects_ambiguous_or_missing_input(kind):
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        path = f"/api/diaries/{did}/edits"
        fields = [("client_request_id", (None, "edit")), ("expected_version", (None, "1")),
                  ("confirmed_text", (None, "본문"))]
        if kind == "duplicate":
            fields.append(("confirmed_text", (None, "다른 본문")))
        elif kind == "missing":
            fields.pop()
        elif kind == "empty_file_field":
            fields.append(("file", (None, "")))
        result = await client.put(path, json={}) if kind == "json" else await client.put(path, files=fields)
        assert_error(result, 422, "DIARY_EDIT_INVALID")
        assert not db.edits


@pytest.mark.parametrize("outcome", [httpx.Response(200, json=[]), httpx.Response(200, json={}),
                                    httpx.Response(200, text="bad json"), httpx.Response(500),
                                    httpx.Response(409, json={"code": "PT422", "details": "IDEMPOTENCY_CONFLICT"}),
                                    httpx.Response(200, json={"state": "completed", "result": {}})])
async def test_edit_bad_repository_responses_never_return_success(outcome):
    db = EditDatabase()
    async with api(db) as (client, _, storage):
        did = (await saved_diary(client))["diary_id"]
    async def broken(request):
        if request.url.path.endswith("/member_diary_edit"):
            return outcome
        return await db(request)
    async with api(broken) as (client, _, storage):
        result = await edit(client, did)
        assert_error(result, 503, "DIARY_EDIT_STORAGE_UNAVAILABLE")
        assert result.json()["error"]["retryable"] is True






async def test_legacy_confirmed_diary_without_request_fingerprint_can_be_edited_without_rewards():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        db.diaries[did]["confirmation_fingerprint"] = None
        before = len(db.room.requests)
        result = await edit(client, did)
        assert result.status_code == 200
        assert db.diaries[did]["confirmation_fingerprint"] is None
        assert len(db.room.requests) == before
