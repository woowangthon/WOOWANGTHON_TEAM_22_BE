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


@pytest.mark.parametrize("lost", [False, True])
async def test_delete_hides_reads_keeps_assets_rewards_and_same_date_rewrite(lost):
    async with api() as (client, db, storage):
        original = await saved_diary(client)
        did = original["diary_id"]
        await upload(client, did)
        objects = deepcopy(storage.objects)
        messages = deepcopy(db.messages)
        if lost:
            db.edit_failure = "delete_lost"
        result = await client.delete(f"/api/diaries/{did}", headers=delete_headers())
        assert result.status_code == (503 if lost else 204)
        retry = await client.delete(f"/api/diaries/{did}", headers=delete_headers())
        assert retry.status_code == 204 and retry.content == b""
        assert_error(await client.get(f"/api/diaries/{did}"), 404, "DIARY_NOT_FOUND")
        assert_error(await edit(client, did), 404, "DIARY_NOT_FOUND")
        assert_error(await upload(client, did), 404, "DIARY_NOT_FOUND")
        listing = await client.get("/api/diaries", params={"month": original["diary_date"][:7]})
        assert listing.json()["diaries"] == []
        assert messages == db.messages and objects == storage.objects
        path, body, _ = await prepare(client, key="rewrite")
        body["client_request_id"] = "rewrite"
        rewritten = await client.post(path, json=body)
        assert rewritten.status_code == 200, rewritten.text
        assert rewritten.json()["diary_id"] != did
        assert len(db.room.member(MEMBER_A).grants) == 1
        for _ in range(2):
            assert (await client.post("/api/me/rewards/sync", json={})).json()["wallet"] == {"coins": 30, "water_drops": 1}
        assert len(db.diaries) == 2


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "x"}, {"If-Match": '"1"'},
                                   {"Idempotency-Key": "x", "If-Match": "1"},
                                   {"Idempotency-Key": "x", "If-Match": 'W/"1"'}])
async def test_delete_requires_valid_request_and_explicit_version(headers):
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        assert_error(await client.delete(f"/api/diaries/{did}", headers=headers), 422, "DIARY_EDIT_INVALID")
        assert not db.deletes and db.diaries[did]["deleted_at"] is None


async def test_delete_stale_version_and_member_isolation():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        await edit(client, did)
        assert_error(await client.delete(f"/api/diaries/{did}", headers=delete_headers()), 409, "DIARY_EDIT_VERSION_CONFLICT")
        client.headers["Authorization"] = "Bearer fake-member-b"
        assert_error(await edit(client, did), 404, "DIARY_NOT_FOUND")
        assert_error(await client.delete(f"/api/diaries/{did}", headers=delete_headers(2)), 404, "DIARY_NOT_FOUND")
        assert db.diaries[did]["deleted_at"] is None


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


async def test_delete_replay_does_not_delete_new_diary_and_key_conflicts_are_detected():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        assert (await client.delete(f"/api/diaries/{did}", headers=delete_headers())).status_code == 204
        assert_error(await client.delete(f"/api/diaries/{did}", headers=delete_headers(2)), 409, "IDEMPOTENCY_CONFLICT")
        assert_error(await client.delete(f"/api/diaries/{did}", headers=delete_headers(key="other")), 404, "DIARY_NOT_FOUND")
        path, body, _ = await prepare(client, key="recreate")
        body["client_request_id"] = "recreate"
        new = (await client.post(path, json=body)).json()["diary_id"]
        assert_error(await client.delete(f"/api/diaries/{new}", headers=delete_headers()), 409, "IDEMPOTENCY_CONFLICT")
        assert (await client.delete(f"/api/diaries/{did}", headers=delete_headers())).status_code == 204
        assert (await client.get(f"/api/diaries/{new}")).status_code == 200


@pytest.mark.parametrize("legacy", [False, True])
async def test_deferred_first_reward_survives_delete_and_legacy_day_cannot_be_bypassed(legacy):
    async with api() as (client, db, storage):
        db.room.fail_during = "sync_rewards"
        did = (await saved_diary(client))["diary_id"]
        if legacy:
            db.room.legacy_diary_ids.add(did)
        assert (await client.delete(f"/api/diaries/{did}", headers=delete_headers())).status_code == 204
        db.room.fail_during = None
        path, body, _ = await prepare(client, key="new")
        body["client_request_id"] = "new"
        assert (await client.post(path, json=body)).status_code == 200
        wallet = (await client.post("/api/me/rewards/sync", json={})).json()["wallet"]
        assert wallet == {"coins": 0 if legacy else 30, "water_drops": 0 if legacy else 1}




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


async def test_comic_read_marks_previous_revision_stale_without_regeneration_or_deletion():
    from tests.fake_comics import comic_api, prepared
    async with comic_api() as (client, service, db, storage, ai):
        path, confirm, sid, body, plan, rendered = await prepared(client, service)
        preview = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        did = (await client.post(path, json=confirm)).json()["diary_id"]
        attached = await client.post(f"/api/diaries/{did}/comics", json={
            "client_request_id": "attach", "preview_id": str(preview.preview_id)})
        assert attached.status_code == 200
        current = (await client.get(f"/api/diaries/{did}/comics")).json()
        assert current["content_status"] == "current" and current["source_edit_version"] == 1
        # 부모 revision 변경을 DB fixture에 주입해 조회 진단만 검증한다.
        before = deepcopy((db.previews, db.links, storage.objects, ai.requests))
        db.diaries[did]["edit_version"] = 2
        stale = (await client.get(f"/api/diaries/{did}/comics")).json()
        assert stale["content_status"] == "stale" and stale["current_edit_version"] == 2
        assert before == (db.previews, db.links, storage.objects, ai.requests)
        assert (await client.post(f"/api/diaries/{did}/comics", json={
            "client_request_id": "again", "preview_id": str(preview.preview_id)})).status_code == 409
        db.diaries[did]["deleted_at"] = "2026-11-10T14:00:00Z"
        assert_error(await client.get(f"/api/diaries/{did}/comics"), 404, "DIARY_NOT_FOUND")


async def test_openapi_and_browser_preflight_include_edit_delete_headers():
    async with api() as (client, db, storage):
        schema = (await client.get("/openapi.json")).json()
        request = schema["paths"]["/api/diaries/{diary_id}/edits"]["put"]["requestBody"]
        assert "multipart/form-data" in request["content"]
        assert "application/json" not in request["content"]
        cors = await client.options(f"/api/diaries/{uuid4()}", headers={
            "Origin": "http://localhost:5500", "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "Authorization,Idempotency-Key,If-Match"})
        assert cors.status_code == 200
        assert "If-Match" in cors.headers["access-control-allow-headers"]


async def test_legacy_confirmed_diary_without_request_fingerprint_can_be_edited_without_rewards():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        db.diaries[did]["confirmation_fingerprint"] = None
        before = len(db.room.requests)
        result = await edit(client, did)
        assert result.status_code == 200
        assert db.diaries[did]["confirmation_fingerprint"] is None
        assert len(db.room.requests) == before


async def test_delete_rollback_keeps_visible_diary_until_successful_retry():
    async with api() as (client, db, storage):
        did = (await saved_diary(client))["diary_id"]
        db.edit_failure = "delete_rollback"
        path = f"/api/diaries/{did}"
        assert_error(await client.delete(path, headers=delete_headers()), 503, "DIARY_EDIT_STORAGE_UNAVAILABLE")
        assert not db.deletes and (await client.get(path)).status_code == 200
        assert (await client.delete(path, headers=delete_headers())).status_code == 204


@pytest.mark.parametrize("operation", ["edit", "delete"])
async def test_malformed_success_cannot_hide_missing_drawing_or_fake_delete_flag(operation):
    db = EditDatabase()
    async def broken(request):
        response = await db(request)
        if request.url.path.endswith("/member_diary_edit") and response.status_code == 200:
            value = response.json()
            if operation == "edit" and value.get("state") == "completed":
                value["result"]["original_drawing"] = None
            elif operation == "delete":
                value = {"deleted": 1}
            return httpx.Response(200, json=value)
        return response
    async with api(broken) as (client, _, storage):
        did = (await saved_diary(client))["diary_id"]
        result = await edit(client, did, image=picture()) if operation == "edit" else await client.delete(
            f"/api/diaries/{did}", headers=delete_headers())
        assert_error(result, 503, "DIARY_EDIT_STORAGE_UNAVAILABLE")
