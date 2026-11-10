"""오프라인 트랜잭션 모형. PostgreSQL 잠금/RLS/SQL 실행 검증을 대신하지 않습니다."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import httpx

from app.clients.sessions import SessionRepository, unavailable
from app.errors import AppError
from tests.test_drawings import DrawingDatabase


UNKNOWN = {"status": 502, "code": "AI_OUTCOME_UNKNOWN",
           "message": "응답을 확인하지 못했어요. 자동으로 다시 보내지 않습니다.", "retryable": False}


class FakeSessionRepository(SessionRepository):
    def __init__(self, db=None):
        super().__init__(None, 10, 135)
        self.db = db or DrawingDatabase()
        self.sessions = {}
        self.requests = {}
        self.calls = []
        self.clock = 0
        self.lock = asyncio.Lock()
        self.failure = None

    def envelope(self, session, request, acquired=False):
        return deepcopy({"session": session, "request": request, "acquired": acquired})

    async def check_existing_owner(self, owner_id, session_id):
        row = self.sessions.get(session_id)
        if row is not None and row["owner_id"] != str(owner_id):
            raise AppError(404, "SESSION_NOT_FOUND", "세션 없음")

    def expire(self, session):
        active = self.requests.get(session["active_request_id"])
        if active and active["state"] == "dispatched" and active["deadline"] <= self.clock:
            active.update(state="unknown", error=deepcopy(UNKNOWN))
            session.update(active_request_id=None, version=session["version"] + 1)

    async def rpc(self, name, payload):
        async with self.lock:
            self.calls.append((name, deepcopy(payload)))
            phase = payload.get("p_action", "confirm" if name == "confirm_chat_session_diary" else "reserve")
            if self.failure == (phase, "before"):
                self.failure = None
                raise unavailable()
            backup = deepcopy((self.sessions, self.requests, self.db.diaries, self.db.messages))
            try:
                if name == "reserve_chat_request":
                    result = self._reserve(payload)
                elif name == "transition_chat_request":
                    result = self._transition(payload)
                else:
                    assert name == "confirm_chat_session_diary"
                    result = self._confirm(payload)
                if self.failure == (phase, "rollback"):
                    self.failure = None
                    raise unavailable()
            except Exception:
                self.sessions, self.requests, self.db.diaries, self.db.messages = backup
                raise
            if self.failure == (phase, "after"):
                self.failure = None
                raise unavailable()
            return self.check_error(result)

    def _reserve(self, p):
        owner, sid, kind, key = (p[k] for k in ("p_owner_id", "p_session_id", "p_kind", "p_client_key"))
        previous = next((r for r in self.requests.values() if r["owner_id"] == owner
                         and r["kind"] == kind and r["client_key"] == key
                         and (kind in {"start", "confirm"} or r["session_id"] == sid)), None)
        if kind == "start":
            if previous:
                sid = previous["session_id"]
            else:
                if sum(s["owner_id"] == owner and s["lifecycle"] in {"starting", "active"}
                       for s in self.sessions.values()) >= 3:
                    return {"error": "SESSION_LIMIT_REACHED"}
                self.sessions[sid] = {"session_id": sid, "owner_id": owner, "schema_version": 1,
                                      "messages": [], "history_revision": 0, "version": 0,
                                      "lifecycle": "starting", "active_request_id": None}
        session = self.sessions.get(sid)
        if session is None or session["owner_id"] != owner:
            return {"error": "SESSION_NOT_FOUND"}
        if previous and (previous["session_id"] != sid or previous["fingerprint"] != p["p_fingerprint"]
                         or previous["input"] != p["p_input"]):
            return {"error": "IDEMPOTENCY_CONFLICT"}
        self.expire(session)
        if previous and previous["state"] in {"succeeded", "failed", "unknown"}:
            return self.envelope(session, previous)
        if any(r["session_id"] == sid and r["state"] == "unknown" for r in self.requests.values()):
            return {"error": "AI_OUTCOME_UNKNOWN"}
        if session["lifecycle"] == "confirmed":
            return {"error": "SESSION_ALREADY_CONFIRMED"}
        if previous and (previous["state"] == "dispatched" or
                         (previous["state"] == "reserved" and previous["lease"] > self.clock and kind != "confirm")):
            return self.envelope(session, previous)
        if session["active_request_id"] and (not previous or session["active_request_id"] != previous["id"]):
            return {"error": "SESSION_BUSY"}
        if previous and previous["base_revision"] != session["history_revision"]:
            return {"error": "HISTORY_REVISION_CONFLICT"}
        if kind in {"draft", "confirm"}:
            if p["p_input"]["history_revision"] != session["history_revision"]:
                return {"error": "HISTORY_REVISION_CONFLICT"}
            if not session["history_revision"]:
                return {"error": "NO_CHILD_MESSAGES"}
        if kind == "confirm" and self.draft(session, p["p_input"]["draft_id"]) is None:
            return {"error": "DIARY_DRAFT_NOT_FOUND"}
        if not previous:
            if sum(r["session_id"] == sid for r in self.requests.values()) >= 128:
                return {"error": "SESSION_LIMIT_REACHED"}
            previous = {"id": str(uuid4()), "owner_id": owner, "session_id": sid, "kind": kind,
                        "client_key": key, "fingerprint": p["p_fingerprint"], "input": deepcopy(p["p_input"]),
                        "base_revision": session["history_revision"], "result": None,
                        "confirmed_at": (datetime(2026, 11, 9, tzinfo=timezone.utc)
                                         + timedelta(seconds=self.clock)).isoformat() if kind == "confirm" else None}
            self.requests[previous["id"]] = previous
        previous.update(state="reserved", attempt_token=p["p_attempt_token"], lease=self.clock + 30, error=None)
        session.update(active_request_id=previous["id"], version=session["version"] + 1)
        return self.envelope(session, previous, True)

    def identity(self, p):
        session = self.sessions.get(p["p_session_id"])
        request = self.requests.get(p["p_request_id"])
        if session is None or session["owner_id"] != p["p_owner_id"]:
            return None, None
        if request is None or request["owner_id"] != p["p_owner_id"] or request["session_id"] != p["p_session_id"]:
            return session, None
        return session, request

    @staticmethod
    def current(session, request, p):
        return (request and request["attempt_token"] == p["p_attempt_token"]
                and session["version"] == p["p_version"] and session["active_request_id"] == request["id"])

    def _transition(self, p):
        session, request = self.identity(p)
        if session is None:
            return {"error": "SESSION_NOT_FOUND"}
        if not self.current(session, request, p):
            return {"error": "REQUEST_IN_PROGRESS"}
        self.expire(session)
        if request["state"] == "unknown":
            return {"error": "AI_OUTCOME_UNKNOWN"}
        action = p["p_action"]
        if action == "dispatched":
            if request["state"] != "reserved" or request["lease"] <= self.clock:
                return {"error": "REQUEST_IN_PROGRESS"}
            request.update(state="dispatched", deadline=self.clock + p["p_dispatch_seconds"])
        else:
            assert request["state"] == "dispatched"
            if action == "succeeded":
                result = p["p_result"]
                if request["kind"] == "start":
                    session["messages"] = [{"role": "assistant", "text": result["reply"]["text"]}]
                    session["lifecycle"] = "active"
                elif request["kind"] == "message":
                    session["messages"].extend([{"role": "child", "text": request["input"]["text"]},
                                                 {"role": "assistant", "text": result["reply"]["text"]}])
                    session["history_revision"] += 1
                request.update(state=action, result=deepcopy(result), error=None)
            else:
                request.update(state=action, error=deepcopy(p["p_error"]))
            session.update(active_request_id=None, version=session["version"] + 1)
        return self.envelope(session, request)

    def draft(self, session, draft_id):
        return next((r["result"]["draft"] for r in self.requests.values()
                     if r["session_id"] == session["session_id"] and r["kind"] == "draft"
                     and r["state"] == "succeeded" and r["base_revision"] == session["history_revision"]
                     and r["result"]["draft"]["draft_id"] == draft_id), None)

    def _confirm(self, p):
        session, request = self.identity(p)
        if session is None:
            return {"error": "SESSION_NOT_FOUND"}
        if request and request["state"] == "succeeded":
            return self.envelope(session, request)
        if not self.current(session, request, p):
            return {"error": "REQUEST_IN_PROGRESS"}
        assert request["kind"] == "confirm" and request["state"] == "reserved"
        # 기존 확정 저장 모형을 같은 lock/rollback 경계 안에서 재사용합니다.
        data = {"p_user_id": p["p_owner_id"], "p_session_id": session["session_id"],
                "p_client_request_id": request["client_key"], "p_fingerprint": request["fingerprint"],
                "p_history_revision": request["base_revision"], "p_weather": request["input"]["weather"],
                "p_confirmed_at": request["confirmed_at"], "p_ai_draft": self.draft(session, request["input"]["draft_id"]),
                "p_messages": [{"position": i, **m} for i, m in enumerate(session["messages"])]}
        response = self.db._save(httpx.Request("POST", "https://offline.invalid/rpc", json=data))
        if response.status_code == 409:
            code = response.json()["details"]
            request.update(state="failed", error={"status":409,"code":code,"message":
                "날짜 중복" if code == "DIARY_DATE_CONFLICT" else "요청 ID 충돌", "retryable":False})
            session.update(active_request_id=None, version=session["version"] + 1)
            return {"error": code}
        if response.status_code != 200:
            raise unavailable()
        diary_id = response.json()
        request.update(state="succeeded", result={"diary_id": diary_id})
        session.update(lifecycle="confirmed", confirmed_diary_id=diary_id,
                       active_request_id=None, version=session["version"] + 1)
        return self.envelope(session, request)
