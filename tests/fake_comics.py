"""HTTP 계약용 Fake. PostgreSQL 권한/RLS/트랜잭션 실행 증거가 아니다."""
import asyncio
import base64
import copy
from contextlib import asynccontextmanager
import json

import httpx

from app.clients.comic_ai import scene_key, sources_for, text_hash
from app.comic_schemas import VisualImage, VisualPlan
from app.config import Settings
from app.main import create_app
from tests.conftest import FakeAI, FakeAuth, MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY
from tests.test_drawings import DrawingDatabase, DrawingStorage, picture
from tests.test_saved_diaries import FAKE_SECRET, prepare


def plan_for(text):
    sources = sources_for(text)
    target = min(4, len(sources))
    scenes = []
    for i in range(target):
        ids = list(range(i * len(sources) // target, (i + 1) * len(sources) // target))
        scenes.append({"scene_id": f"comic4-{i+1}", "order": i+1,
                       "caption": "\n".join(sources[sid] for sid in ids), "source_ids": ids,
                       "imageUrl": None, "repeated": False, "reuse_scene_id": None})
    while len(scenes) < 4:
        scenes.append({**scenes[target-1], "scene_id": f"comic4-{len(scenes)+1}",
                       "order": len(scenes)+1, "repeated": True,
                       "reuse_scene_id": scenes[target-1]["scene_id"]})
    return VisualPlan.model_validate({
        "diary_text": text.strip(), "diary_hash": text_hash(text),
        "sources": [{"id": i, "text": t} for i, t in enumerate(sources)],
        "storybook": None, "comic4": {"format": "comic4", "layout": "grid_2x2",
                                      "planning_mode": "deterministic", "scenes": scenes, "notices": []},
        "requires_confirmation": True, "mock": False,
    })


def image_for(plan, scene, status="generated", content=None):
    return VisualImage.model_validate({
        "diary_hash": plan.diary_hash, "scene_key": scene_key(plan.diary_hash, scene.source_ids),
        "caption": scene.caption, "source_ids": scene.source_ids, "format": "comic4",
        "imageUrl": ("data:image/png;base64," + base64.b64encode(
            picture() if content is None else content).decode()) if status == "generated" else None,
        "status": status, "error": None, "requires_confirmation": True, "mock": status == "mock",
    })


class ComicDatabase(DrawingDatabase):
    def __init__(self):
        super().__init__()
        self.previews, self.fingerprints, self.links, self.receipts = {}, {}, {}, {}
        self.comic_failure = None
        self.comic_lock = asyncio.Lock()

    def _save(self, request):
        result = super()._save(request)
        if result.is_success:
            body = json.loads(request.content)
            self.diaries[result.json()].update({
                "source_session_id": body["p_session_id"],
                "source_history_revision": body["p_history_revision"],
            })
        return result

    async def __call__(self, request):
        name = request.url.path.rsplit("/", 1)[-1]
        if name not in {"comic_previews", "diary_comics", "save_comic_preview_result",
                        "complete_comic_preview_asset", "attach_diary_comic"}:
            return await super().__call__(request)
        self.requests.append(request)
        if request.method == "GET":
            assert request.headers["apikey"] == TEST_PUBLISHABLE_KEY
            owner = {"Bearer fake-member-a": str(MEMBER_A),
                     "Bearer fake-member-b": str(MEMBER_B)}[request.headers["authorization"]]
            params = request.url.params
            assert params["user_id"] == "eq." + owner
            if name == "diary_comics":
                diary_id = params["diary_id"].removeprefix("eq.")
                linked = self.links.get(diary_id)
                return httpx.Response(200, json=[{"preview_id": linked[1], "source_edit_version": 1}]
                                      if linked and linked[0] == owner else [])
            rows = [copy.deepcopy(p) for p in self.previews.values() if p["user_id"] == owner]
            for key in ("session_id", "preview_id"):
                if key in params:
                    rows = [r for r in rows if "eq." + r[key] == params[key]]
            return httpx.Response(200, json=rows)
        assert request.headers["apikey"] == FAKE_SECRET
        assert "authorization" not in request.headers
        data = json.loads(request.content)
        if self.comic_failure == name:
            self.comic_failure = None
            return httpx.Response(503, json={"message": "private failure"})
        async with self.comic_lock:
            result = self.command(name, data)
        if self.comic_failure == name + "_lost":
            self.comic_failure = None
            raise httpx.ReadTimeout("private lost response")
        return httpx.Response(200, json=copy.deepcopy(result))

    def command(self, name, data):
        if name == "save_comic_preview_result":
            p = data["p_preview"]
            key = (p["user_id"], p["session_id"], p["client_request_id"])
            if key in self.fingerprints:
                pid, fingerprint = self.fingerprints[key]
                if fingerprint != data["p_fingerprint"]:
                    return {"error": "IDEMPOTENCY_CONFLICT"}
                return self.previews[pid]
            self.fingerprints[key] = (p["preview_id"], data["p_fingerprint"])
            self.previews[p["preview_id"]] = copy.deepcopy(p)
            return p
        owner, pid = data["p_user_id"], data["p_preview_id"]
        p = self.previews.get(pid)
        if not p or p["user_id"] != owner:
            return {"error": "COMIC_PREVIEW_NOT_FOUND"}
        if name == "complete_comic_preview_asset":
            for panel in p["panels"]:
                if panel["asset"] and panel["asset"]["asset_id"] == data["p_asset_id"]:
                    panel["outcome"] = "ready"
            return p
        did, key = data["p_diary_id"], (owner, data["p_request_id"])
        receipt = self.receipts.get(key)
        if receipt and receipt != (did, pid):
            return {"error": "IDEMPOTENCY_CONFLICT"}
        d = self.diaries[did]
        if (d["user_id"] != owner or not d["confirmation_fingerprint"]
                or d.get("deleted_at") is not None or d["edit_version"] != 1
                or d["source_session_id"] != p["session_id"]
                or d["source_history_revision"] != p["history_revision"]
                or d["ai_draft"]["draft_id"] != p["draft_id"]
                or d["ai_draft"]["text"] != p["draft_text"]
                or d["confirmed_text"] != p["draft_text"]):
            return {"error": "COMIC_PREVIEW_MISMATCH"}
        if (any(x["outcome"] == "pending" for x in p["panels"])
                or not any(x["outcome"] == "ready" for x in p["panels"])):
            return {"error": "COMIC_NOT_READY"}
        if did in self.links and self.links[did] != (owner, pid):
            return {"error": "COMIC_ALREADY_ATTACHED"}
        if any(link[1] == pid and other != did for other, link in self.links.items()):
            return {"error": "COMIC_ALREADY_ATTACHED"}
        self.links[did] = (owner, pid)
        self.receipts[key] = (did, pid)
        return p


@asynccontextmanager
async def comic_api(db=None, storage=None):
    db, storage = db or ComicDatabase(), storage or DrawingStorage()
    ai = FakeAI()
    app = create_app(Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY,
                              supabase_secret_key=FAKE_SECRET),
                     httpx.MockTransport(ai), httpx.MockTransport(FakeAuth()),
                     httpx.MockTransport(db), httpx.MockTransport(storage))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        base_url="http://be.test", transport=httpx.ASGITransport(app),
        headers={"Authorization": "Bearer fake-member-a"},
    ) as client:
        yield client, app.state.comic_service, db, storage, ai


async def prepared(client, service, text=None):
    url, confirm, draft = await prepare(client)
    session_id = draft["session_id"]
    if text is not None:
        # 큰/다문장 AI 초안을 이미 검증해 저장한 서버 상태를 위한 fixture.
        session = service.store.sessions[session_id]
        record = next(iter(session.diary_requests.values()))
        record.result = record.result.model_copy(update={
            "draft": record.result.draft.model_copy(update={"text": text})})
    text = text if text is not None else draft["draft"]["text"]
    from app.comic_schemas import ComicPreviewRequest
    body = ComicPreviewRequest(client_request_id="preview-1", history_revision=1,
                               draft_id=confirm["draft_id"])
    plan = plan_for(text)
    rendered = {s.scene_id: image_for(plan, s) for s in plan.comic4.scenes if not s.repeated}
    return url, confirm, session_id, body, plan, rendered
