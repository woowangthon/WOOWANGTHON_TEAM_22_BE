import asyncio
import copy
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.errors import AppError
from tests.conftest import MEMBER_A, MEMBER_B
from tests.fake_comics import comic_api, image_for, prepared
from tests.test_drawings import picture, upload
from tests.test_saved_diaries import assert_error


def preview_url(session_id, preview_id=None):
    return f"/api/chat-sessions/{session_id}/comic-previews" + (f"/{preview_id}" if preview_id else "")


@pytest.mark.anyio
async def test_preview_is_gated_without_ai_queue_or_final_diary_save():
    async with comic_api() as (client, service, db, storage, ai):
        url, confirm, sid, body, plan, rendered = await prepared(client, service)
        before = len(ai.requests)
        for _ in range(2):
            result = await client.post(preview_url(sid), json=body.model_dump())
            assert_error(result, 503, "COMIC_PREVIEW_NOT_ENABLED")
            assert not result.json()["error"]["retryable"]
        assert not db.previews and not db.diaries and not storage.requests
        assert len(ai.requests) == before
        # 만화가 보류되어도 사용자 확인에 따른 기존 확정·원본 그림 저장은 성공한다.
        diary = await client.post(url, json=confirm)
        assert diary.status_code == 200
        did = diary.json()["diary_id"]
        original = await upload(client, did)
        assert original.status_code == 200
        absent = await client.get(f"/api/diaries/{did}/comics")
        assert absent.json() == {"preview_id": None, "status": "none", "panels": [],
                                 "recovery_required": False, "error_code": None,
                                 "current_edit_version": 1, "source_edit_version": None,
                                 "content_status": "none"}
        detail = (await client.get(f"/api/diaries/{did}")).json()
        assert detail["assets"][0]["asset_kind"] == "original_drawing"
        assert detail["imageUrl"] == original.json()["asset"]["imageUrl"]


@pytest.mark.anyio
@pytest.mark.parametrize("case,code,status", [
    ("foreign", "SESSION_NOT_FOUND", 404), ("missing", "SESSION_NOT_FOUND", 404),
    ("revision", "HISTORY_REVISION_CONFLICT", 409), ("draft", "DIARY_DRAFT_NOT_FOUND", 404),
    ("busy", "SESSION_BUSY", 409), ("stale_draft", "HISTORY_REVISION_CONFLICT", 409),
    ("too_long", "COMIC_TEXT_LIMIT", 422), ("too_many_sources", "COMIC_SOURCE_LIMIT", 422),
])
async def test_preview_checks_server_owned_draft_before_any_side_effect(case, code, status):
    async with comic_api() as (client, service, db, storage, ai):
        _, _, sid, body, _, _ = await prepared(client, service)
        data = body.model_dump()
        if case == "foreign": client.headers["Authorization"] = "Bearer fake-member-b"
        if case == "missing": sid = "ses_" + "0"*32
        if case == "revision": data["history_revision"] = 0
        if case == "draft": data["draft_id"] = "other"
        if case == "busy": service.store.sessions[sid].busy = object()
        if case == "stale_draft":
            service.store.sessions[sid].history_revision = 2
            data["history_revision"] = 2
        if case in {"too_long", "too_many_sources"}:
            record = next(iter(service.store.sessions[sid].diary_requests.values()))
            text = "가"*6001 if case == "too_long" else "\n".join(["장면"]*81)
            record.result = record.result.model_copy(update={
                "draft": record.result.draft.model_copy(update={"text": text})})
        before = len(ai.requests)
        assert_error(await client.post(preview_url(sid), json=data), status, code)
        assert len(ai.requests) == before and not db.requests and not storage.requests


@pytest.mark.anyio
@pytest.mark.parametrize("extra", [{"diary_text": "임의 본문"}, {"confirmed": True}, {"user_id": str(MEMBER_B)}])
async def test_preview_does_not_accept_client_text_confirmation_or_owner(extra):
    async with comic_api() as (client, service, db, storage, ai):
        _, _, sid, body, _, _ = await prepared(client, service)
        assert_error(await client.post(preview_url(sid), json={**body.model_dump(), **extra}),
                     422, "VALIDATION_ERROR")
        assert not db.requests and not storage.requests


@pytest.mark.anyio
async def test_store_attach_read_and_restart_preserve_original_drawing_and_repeated_asset():
    async with comic_api() as (client, service, db, storage, ai):
        url, confirm, sid, body, plan, rendered = await prepared(client, service)
        saved = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        pid = str(saved.preview_id)
        assert not db.diaries and len(storage.objects) == 1
        assert len({p.asset.asset_id for p in saved.panels}) == 1
        assert all(p.outcome == "ready" for p in saved.panels)
        result = await client.get(preview_url(sid, pid))
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "ready"
        assert [p["position"] for p in result.json()["panels"]] == [1, 2, 3, 4]
        assert len({p["imageUrl"] for p in result.json()["panels"]}) == 1
        expiry = datetime.fromisoformat(result.json()["panels"][0]["expires_at"])
        assert 0 < (expiry - datetime.now(timezone.utc)).total_seconds() <= 300
        assert "imageUrl" not in str(db.previews) and "data:image" not in str(db.previews)
        diary = (await client.post(url, json=confirm)).json()
        did = diary["diary_id"]
        original = await upload(client, did)
        assert original.status_code == 200
        attach = {"client_request_id": "attach-1", "preview_id": pid}
        first, second = await asyncio.gather(*[
            client.post(f"/api/diaries/{did}/comics", json=attach) for _ in range(2)])
        assert first.status_code == second.status_code == 200, first.text
        assert first.json()["preview_id"] == second.json()["preview_id"] == pid
        assert len(db.links) == len(db.receipts) == 1
        detail = (await client.get(f"/api/diaries/{did}")).json()
        assert detail["imageUrl"] == original.json()["asset"]["imageUrl"]
        assert detail["confirmed_text"] == diary["confirmed_text"] and len(detail["assets"]) == 1
        expected = copy.deepcopy(db.diaries)
    async with comic_api(db, storage) as (client, service, _, _, ai):
        assert not service.store.sessions
        for target in (preview_url(sid, pid), f"/api/diaries/{did}/comics"):
            first = await client.get(target)
            second = await client.get(target)
            assert first.status_code == second.status_code == 200
            assert first.json()["status"] == "ready"
            assert datetime.fromisoformat(second.json()["panels"][0]["expires_at"]) >= datetime.fromisoformat(
                first.json()["panels"][0]["expires_at"])
        assert (await client.post(f"/api/diaries/{did}/comics", json=attach)).status_code == 200
        assert db.diaries == expected and not ai.requests


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["save_comic_preview_result", "save_comic_preview_result_lost",
                                    "complete_comic_preview_asset", "complete_comic_preview_asset_lost",
                                    "upload_lost", "offline"])
async def test_result_persistence_recovers_same_bytes_without_ai_regeneration(failure):
    async with comic_api() as (client, service, db, storage, ai):
        _, _, sid, body, plan, rendered = await prepared(client, service)
        before = len(ai.requests)
        if failure in {"upload_lost", "offline"}: storage.failure = failure
        else: db.comic_failure = failure
        with pytest.raises(AppError):
            await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        # GET은 실행·업로드 복구도 하지 않고 기존 체크포인트만 읽는다.
        objects_before = copy.deepcopy(storage.objects)
        if db.previews:
            pid = next(iter(db.previews))
            poll = await client.get(preview_url(sid, pid))
            assert poll.status_code == 200
            assert poll.json()["status"] in {"failed", "ready"}
            assert poll.json()["recovery_required"] == (poll.json()["status"] == "failed")
        assert storage.objects == objects_before and len(ai.requests) == before
        saved = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        assert all(p.outcome == "ready" for p in saved.panels)
        assert len(storage.objects) == len(db.previews) == 1
        assert len(ai.requests) == before
        assert all(r.method != "DELETE" for r in db.requests + storage.requests)


@pytest.mark.anyio
async def test_changed_results_same_key_conflict_and_existing_corrupt_blob_cannot_finish():
    async with comic_api() as (client, service, db, storage, ai):
        _, _, sid, body, plan, rendered = await prepared(client, service)
        db.comic_failure = "complete_comic_preview_asset"
        with pytest.raises(AppError): await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        key = next(iter(storage.objects))
        original = storage.objects[key]
        storage.objects[key] = b"different bytes"
        with pytest.raises(AppError, match="DRAWING_STORAGE_UNAVAILABLE"):
            await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        assert db.previews[next(iter(db.previews))]["panels"][0]["outcome"] == "pending"
        changed = {s.scene_id: image_for(plan, s, content=picture(color="blue"))
                   for s in plan.comic4.scenes if not s.repeated}
        with pytest.raises(AppError, match="IDEMPOTENCY_CONFLICT"):
            await service.persist_result(MEMBER_A, sid, body, plan, changed)
        storage.objects[key] = original
        assert (await service.persist_result(MEMBER_A, sid, body, plan, rendered)).panels[0].outcome == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize("outcomes,expected,error", [
    (["generated", "failed", "rejected", "mock"], "partial", None),
    (["generated", None, "failed", "failed"], "partial", "COMIC_RESULT_UNKNOWN"),
    ([None, "failed", "rejected", "mock"], "failed", "COMIC_RESULT_UNKNOWN"),
    (["failed"]*4, "failed", None),
    (["mock"]*4, "failed", None),
])
async def test_partial_unknown_rejected_and_mock_are_not_reported_ready(outcomes, expected, error):
    async with comic_api() as (client, service, db, storage, ai):
        url, confirm, sid, body, plan, _ = await prepared(client, service, "하나. 둘. 셋. 넷.")
        rendered = {s.scene_id: image_for(plan, s, status) if status else None
                    for s, status in zip(plan.comic4.scenes, outcomes)}
        saved = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        result = (await client.get(preview_url(sid, saved.preview_id))).json()
        assert result["status"] == expected and result["error_code"] == error
        assert sum(p["imageUrl"] is not None for p in result["panels"]) == outcomes.count("generated")
        # 만화 실패도 기존 사용자 확정 저장을 막지 않는다.
        diary = await client.post(url, json=confirm)
        assert diary.status_code == 200
        linked = await client.post(f"/api/diaries/{diary.json()['diary_id']}/comics", json={
            "client_request_id": "attach-1", "preview_id": str(saved.preview_id)})
        assert linked.status_code == (200 if expected == "partial" else 409)


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["confirmed_text", "draft_id", "draft_text", "session_id", "revision", "unconfirmed"])
async def test_attach_rejects_wrong_confirmed_snapshot(change):
    async with comic_api() as (client, service, db, storage, ai):
        url, confirm, sid, body, plan, rendered = await prepared(client, service)
        saved = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        did = (await client.post(url, json=confirm)).json()["diary_id"]
        row = db.diaries[did]
        if change == "confirmed_text": row["confirmed_text"] = "다른 본문"
        if change == "draft_id": row["ai_draft"]["draft_id"] = "draft_" + "0"*32
        if change == "draft_text": row["ai_draft"]["text"] = "다른 본문"
        if change == "session_id": row["source_session_id"] = "ses_" + "0"*32
        if change == "revision": row["source_history_revision"] = 2
        if change == "unconfirmed": row["confirmation_fingerprint"] = None
        assert_error(await client.post(f"/api/diaries/{did}/comics", json={
            "client_request_id": "attach-1", "preview_id": str(saved.preview_id)}),
            409, "COMIC_PREVIEW_MISMATCH")
        assert not db.links and not db.receipts


@pytest.mark.anyio
async def test_attachment_response_loss_request_conflict_and_new_preview_cannot_replace_link():
    async with comic_api() as (client, service, db, storage, ai):
        url, confirm, sid, body, plan, rendered = await prepared(client, service)
        saved = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        did = (await client.post(url, json=confirm)).json()["diary_id"]
        endpoint = f"/api/diaries/{did}/comics"
        attach = {"client_request_id": "attach-1", "preview_id": str(saved.preview_id)}
        db.comic_failure = "attach_diary_comic_lost"
        assert (await client.post(endpoint, json=attach)).status_code == 503
        assert (await client.post(endpoint, json=attach)).status_code == 200
        other = await service.persist_result(MEMBER_A, sid, body.model_copy(update={
            "client_request_id": "preview-2"}), plan, rendered)
        assert_error(await client.post(endpoint, json={**attach, "preview_id": str(other.preview_id)}),
                     409, "IDEMPOTENCY_CONFLICT")
        assert_error(await client.post(endpoint, json={
            "client_request_id": "attach-2", "preview_id": str(other.preview_id)}),
            409, "COMIC_ALREADY_ATTACHED")
        assert len(db.links) == len(db.receipts) == 1


@pytest.mark.anyio
async def test_owner_session_diary_isolation_and_no_signing_on_foreign_get():
    async with comic_api() as (client, service, db, storage, ai):
        url, confirm, sid, body, plan, rendered = await prepared(client, service)
        saved = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        did = (await client.post(url, json=confirm)).json()["diary_id"]
        count = len(storage.requests)
        assert_error(await client.get(preview_url("ses_"+"0"*32, saved.preview_id)),
                     404, "COMIC_PREVIEW_NOT_FOUND")
        client.headers["Authorization"] = "Bearer fake-member-b"
        assert_error(await client.get(preview_url(sid, saved.preview_id)), 404, "COMIC_PREVIEW_NOT_FOUND")
        assert_error(await client.get(f"/api/diaries/{did}/comics"), 404, "DIARY_NOT_FOUND")
        assert_error(await client.post(f"/api/diaries/{did}/comics", json={
            "client_request_id": "attach-1", "preview_id": str(saved.preview_id)}), 404, "DIARY_NOT_FOUND")
        assert len(storage.requests) == count and not db.links


@pytest.mark.anyio
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/chat-sessions/ses_x/comic-previews", {"client_request_id": "p", "history_revision": 1, "draft_id": "d"}),
    ("GET", f"/api/chat-sessions/ses_x/comic-previews/{uuid4()}", None),
    ("POST", f"/api/diaries/{uuid4()}/comics", {"client_request_id": "a", "preview_id": str(uuid4())}),
    ("GET", f"/api/diaries/{uuid4()}/comics", None),
])
@pytest.mark.parametrize("auth", [None, "Bearer invalid"])
async def test_all_new_routes_require_member_auth(method, path, body, auth):
    async with comic_api() as (client, service, db, storage, ai):
        client.headers.clear()
        if auth: client.headers["Authorization"] = auth
        assert_error(await client.request(method, path, json=body), 401, "AUTHENTICATION_FAILED")
        assert not db.requests and not storage.requests and not ai.requests


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["sign", "foreign_url"])
async def test_signing_failure_keeps_result_and_get_reissues_without_generation(failure):
    async with comic_api() as (client, service, db, storage, ai):
        _, _, sid, body, plan, rendered = await prepared(client, service)
        saved = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        before = copy.deepcopy(db.previews)
        storage.failure = failure
        assert_error(await client.get(preview_url(sid, saved.preview_id)), 503, "DRAWING_READ_UNAVAILABLE")
        storage.failure = None
        assert (await client.get(preview_url(sid, saved.preview_id))).json()["status"] == "ready"
        assert db.previews == before


@pytest.mark.anyio
async def test_pending_preview_cannot_attach_and_refresh_never_recovers_upload():
    async with comic_api() as (client, service, db, storage, ai):
        url, confirm, sid, body, plan, rendered = await prepared(client, service)
        storage.failure = "offline"
        with pytest.raises(AppError): await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        pid = next(iter(db.previews))
        did = (await client.post(url, json=confirm)).json()["diary_id"]
        before = len(storage.requests)
        for _ in range(2):
            result = (await client.get(preview_url(sid, pid))).json()
            assert result["status"] == "failed" and result["recovery_required"]
            assert result["error_code"] == "COMIC_STORAGE_RECOVERY_REQUIRED"
        assert len(storage.requests) == before
        assert_error(await client.post(f"/api/diaries/{did}/comics", json={
            "client_request_id": "attach-1", "preview_id": pid}), 409, "COMIC_NOT_READY")


@pytest.mark.anyio
async def test_corrupt_stored_ownership_or_metadata_is_never_signed():
    async with comic_api() as (client, service, db, storage, ai):
        _, _, sid, body, plan, rendered = await prepared(client, service)
        saved = await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        pid = str(saved.preview_id)
        db.previews[pid]["panels"][0]["asset"]["width"] = 99999
        before = len(storage.requests)
        assert_error(await client.get(preview_url(sid, pid)), 502, "COMIC_STORAGE_INVALID_RESPONSE")
        assert len(storage.requests) == before


@pytest.mark.anyio
async def test_same_bytes_recovery_after_restart_needs_no_memory_session_or_new_ai_call():
    async with comic_api() as (client, service, db, storage, ai):
        _, _, sid, body, plan, rendered = await prepared(client, service)
        storage.failure = "upload_lost"
        with pytest.raises(AppError): await service.persist_result(MEMBER_A, sid, body, plan, rendered)
        pid = UUID(next(iter(db.previews)))
        asset_id = UUID(db.previews[str(pid)]["panels"][0]["asset"]["asset_id"])
        original_url = rendered["comic4-1"].imageUrl
    async with comic_api(db, storage) as (client, service, _, _, ai):
        assert not service.store.sessions
        before = len(storage.requests)
        with pytest.raises(AppError, match="COMIC_RECOVERY_MISMATCH"):
            await service.recover_result_uploads(MEMBER_A, "Bearer fake-member-a", pid, {
                asset_id: image_for(plan, plan.comic4.scenes[0], content=picture(color="blue")).imageUrl})
        assert len(storage.requests) == before
        with pytest.raises(AppError, match="COMIC_PREVIEW_NOT_FOUND"):
            await service.recover_result_uploads(MEMBER_B, "Bearer fake-member-b", pid, {asset_id: original_url})
        saved = await service.recover_result_uploads(MEMBER_A, "Bearer fake-member-a", pid, {asset_id: original_url})
        assert all(p.outcome == "ready" for p in saved.panels)
        assert not ai.requests and len(storage.objects) == 1
        assert (await client.get(preview_url(sid, pid))).json()["status"] == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize("case,code", [("valid", "COMIC_PREVIEW_NOT_ENABLED"),
                                       ("foreign", "SESSION_NOT_FOUND"),
                                       ("revision", "HISTORY_REVISION_CONFLICT"),
                                       ("missing_draft", "DIARY_DRAFT_NOT_FOUND"),
                                       ("busy", "SESSION_BUSY"),
                                       ("malformed", "COMIC_STORAGE_INVALID_RESPONSE")])
async def test_optional_persistent_session_adapter_only_reads_existing_005_state(case, code):
    from app.persistent_session_store import PersistentSessionStore
    async with comic_api() as (client, service, db, storage, ai):
        _, _, sid, body, _, _ = await prepared(client, service)
        draft = next(iter(service.store.sessions[sid].diary_requests.values())).result.model_dump()
        class Repository:
            def __init__(self): self.calls = []
            async def _request(self, method, path, **kwargs):
                self.calls.append((method, path, kwargs))
                assert method == "GET"
                assert kwargs["params"]["owner_id"] == f"eq.{MEMBER_A}"
                if path.endswith("chat_sessions"):
                    if case == "malformed": return ["private invalid row"]
                    return [{"owner_id": str(MEMBER_B if case == "foreign" else MEMBER_A),
                             "history_revision": 2 if case == "revision" else 1,
                             "active_request_id": str(uuid4()) if case == "busy" else None}]
                return [] if case == "missing_draft" else [{"result": draft}]
        repo = Repository()
        service.store = PersistentSessionStore(repo)
        response = await client.post(preview_url(sid), json=body.model_dump())
        assert response.json()["error"]["code"] == code, response.text
        assert not db.requests and not storage.requests
        assert all(method == "GET" for method, _, _ in repo.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("error", [[], {}, "unexpected-private-code"])
async def test_malformed_rpc_error_is_sanitized_as_invalid_storage_response(error):
    async with comic_api() as (client, service, db, storage, ai):
        url, confirm, sid, body, _, _ = await prepared(client, service)
        did = (await client.post(url, json=confirm)).json()["diary_id"]
        original = service.repository.diaries._request
        async def request(method, path, *args, **kwargs):
            if path.endswith("attach_diary_comic"): return {"error": error}
            return await original(method, path, *args, **kwargs)
        service.repository.diaries._request = request
        assert_error(await client.post(f"/api/diaries/{did}/comics", json={
            "client_request_id": "attach-1", "preview_id": str(uuid4())}),
            502, "COMIC_STORAGE_INVALID_RESPONSE")
