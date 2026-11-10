"""v0.4 HTTP 모형. DB 실행/권한/RLS/동시 트랜잭션 검증을 대신하지 않는다."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import uuid4

import httpx

from app.clients.edits import EDIT_ERRORS
from tests.conftest import MEMBER_A, MEMBER_B
from tests.fake_room import FakeRoomDatabase
from tests.test_drawings import DrawingDatabase
from tests.test_saved_diaries import FAKE_SECRET


class EditDatabase(DrawingDatabase):
    def __init__(self):
        super().__init__()
        self.edits, self.deletes = {}, {}
        self.edit_lock = asyncio.Lock()
        self.edit_failure = None
        self.room = FakeRoomDatabase()
        self.room.secret = FAKE_SECRET

    async def __call__(self, request):
        path = request.url.path
        if path == "/rest/v1/rpc/member_room_command":
            self.room.diaries = [{"id": row["id"], "member": row["user_id"],
                                 "date": row["diary_date"]} for row in self.diaries.values()]
            return await self.room(request)
        if path == "/rest/v1/diary_edit_requests":
            owner = {"Bearer fake-member-a": str(MEMBER_A),
                     "Bearer fake-member-b": str(MEMBER_B)}[request.headers["authorization"]]
            params = request.url.params
            rows = [r for r in self.edits.values() if r["user_id"] == owner
                    and r["diary_id"] == params["diary_id"][3:]
                    and r["request_id"] == params["request_id"][3:]
                    and r["state"] == "completed"]
            return httpx.Response(200, json=[{"drawing": r["drawing"]} for r in rows])
        if path != "/rest/v1/rpc/member_diary_edit":
            if path in {"/rest/v1/rpc/reserve_diary_drawing", "/rest/v1/rpc/complete_diary_drawing"}:
                data = json.loads(request.content)
                d = self.diaries.get(data["p_diary_id"])
                if d and d["edit_version"] != 1:
                    return httpx.Response(409, json={"code": "PT409", "details": "DIARY_EDIT_VERSION_CONFLICT"})
            return await super().__call__(request)
        self.requests.append(request)
        assert request.headers["apikey"] == FAKE_SECRET and "authorization" not in request.headers
        data = json.loads(request.content)
        async with self.edit_lock:
            before = deepcopy((self.diaries, self.edits, self.deletes))
            action = data["p_action"]
            try:
                result = self.command(**data)
                if self.edit_failure == action + "_rollback":
                    self.edit_failure = None
                    self.diaries, self.edits, self.deletes = before
                    return httpx.Response(500)
            except ValueError as error:
                code = str(error)
                status = EDIT_ERRORS[code]
                return httpx.Response(status, json={"code": f"PT{status}", "details": code})
            if self.edit_failure == action + "_lost":
                self.edit_failure = None
                raise httpx.ReadTimeout("synthetic result loss")
            return httpx.Response(200, json=deepcopy(result))

    def command(self, p_user_id, p_diary_id, p_action, p_payload):
        owner, did, action, body = p_user_id, p_diary_id, p_action, p_payload
        d = self.diaries.get(did)
        if not d or d["user_id"] != owner:
            raise ValueError("DIARY_NOT_FOUND")
        key = owner, body["client_request_id"]
        if action == "delete":
            if key in self.deletes:
                if self.deletes[key] != (did, body["expected_version"]):
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return {"deleted": True}
            if d["deleted_at"] is not None:
                raise ValueError("DIARY_NOT_FOUND")
            if d["edit_version"] != body["expected_version"]:
                raise ValueError("DIARY_EDIT_VERSION_CONFLICT")
            d["deleted_at"] = d["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.deletes[key] = did, body["expected_version"]
            return {"deleted": True}
        if d["deleted_at"] is not None:
            raise ValueError("DIARY_NOT_FOUND")
        r = self.edits.get(key)
        if r and (r["diary_id"] != did or r["fingerprint"] != body["fingerprint"]):
            raise ValueError("IDEMPOTENCY_CONFLICT")
        if r and r["state"] == "completed":
            return r
        if action == "reserve":
            if d["edit_version"] != body["expected_version"]:
                raise ValueError("DIARY_EDIT_VERSION_CONFLICT")
            if not r:
                rid = str(uuid4())
                a = body["drawing"]
                if a is not None:
                    a = {**a, "id": rid, "user_id": owner, "diary_id": did,
                         "asset_kind": "original_drawing", "scene_index": 0,
                         "bucket_id": "diary-private", "upload_status": "pending",
                         "storage_key": f"{owner}/{did}/original_drawing/{rid}.png"}
                r = {**body, "request_id": rid, "diary_id": did, "user_id": owner,
                     "drawing": a, "state": "pending", "result": None}
                self.edits[key] = r
            return r
        if not r:
            raise ValueError("DIARY_EDIT_INVALID")
        if d["edit_version"] != r["expected_version"]:
            raise ValueError("DIARY_EDIT_VERSION_CONFLICT")
        a = deepcopy(r["drawing"])
        if a is not None:
            a["upload_status"] = "ready"
            r["drawing"] = a
            d["active_drawing_edit_id"] = r["request_id"]
        elif d["active_drawing_edit_id"] is not None:
            a = next(x["drawing"] for x in self.edits.values() if x["request_id"] == d["active_drawing_edit_id"])
        else:
            a = self.assets.get(did)
        d["confirmed_text"] = r["confirmed_text"]
        d["edit_version"] += 1
        d["updated_at"] = datetime.now(timezone.utc).isoformat()
        r["state"] = "completed"
        r["result"] = {"diary_id": did, "confirmed_text": d["confirmed_text"],
                       "edit_version": d["edit_version"], "updated_at": d["updated_at"],
                       "original_drawing": deepcopy(a)}
        return r
