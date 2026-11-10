import asyncio
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from app.clients.comic_ai import sources_for, validate_plan, validate_render
from app.comic_schemas import ComicAsset, ComicPanel, ComicResponse, DiaryComicResponse, StoredPanel, StoredPreview
from app.errors import AppError
from app.persistent_session_store import PersistentSessionStore
from app.schemas import DiaryDraftResponse
from app.services.comic_images import decode_comic_image


class ComicService:
    def __init__(self, store, repository):
        self.store, self.repository = store, repository
        self.decode_slots = asyncio.Semaphore(2)

    async def draft_text(self, owner_id, session_id, body):
        if isinstance(self.store, PersistentSessionStore):
            # 005가 선택된 경우에만 기존 서버 전용 저장소를 읽는다. 예약/확정은 하지 않는다.
            repo = self.store.repository
            rows = await repo._request("GET", "/rest/v1/chat_sessions", params={
                "owner_id": f"eq.{owner_id}", "session_id": f"eq.{session_id}",
                "select": "owner_id,history_revision,active_request_id", "limit": "2",
            })
            if (not isinstance(rows, list) or len(rows) > 1
                    or rows and not isinstance(rows[0], dict)):
                raise AppError(502, "COMIC_STORAGE_INVALID_RESPONSE", "세션 저장소 응답 오류")
            if not rows or rows[0].get("owner_id") != str(owner_id):
                raise AppError(404, "SESSION_NOT_FOUND", "세션 없음")
            revision, busy = rows[0].get("history_revision"), rows[0].get("active_request_id")
            if type(revision) is not int or revision < 0 or "active_request_id" not in rows[0]:
                raise AppError(502, "COMIC_STORAGE_INVALID_RESPONSE", "세션 저장소 응답 오류")
            drafts = await repo._request("GET", "/rest/v1/chat_requests", params={
                "owner_id": f"eq.{owner_id}", "session_id": f"eq.{session_id}",
                "kind": "eq.draft", "state": "eq.succeeded",
                "result->draft->>draft_id": f"eq.{body.draft_id}",
                "select": "result", "limit": "2",
            })
            try:
                draft = (DiaryDraftResponse.model_validate(drafts[0]["result"])
                         if isinstance(drafts, list) and len(drafts) == 1 else None)
            except (ValidationError, TypeError, KeyError):
                raise AppError(502, "COMIC_STORAGE_INVALID_RESPONSE", "초안 저장소 응답 오류") from None
        else:
            session = self.store.owned_session(owner_id, session_id)
            revision, busy = session.history_revision, session.busy
            draft = next((r.result for r in session.diary_requests.values()
                          if not r.pending and isinstance(r.result, DiaryDraftResponse)
                          and r.result.draft.draft_id == body.draft_id), None)
        if busy is not None:
            raise AppError(409, "SESSION_BUSY", "세션 처리 중", True)
        if revision != body.history_revision:
            raise AppError(409, "HISTORY_REVISION_CONFLICT", "대화 버전 충돌")
        if draft is None:
            raise AppError(404, "DIARY_DRAFT_NOT_FOUND", "초안 없음")
        if draft.session_id != session_id or draft.history_revision != body.history_revision:
            raise AppError(409, "HISTORY_REVISION_CONFLICT", "대화 버전 충돌")
        sources_for(draft.draft.text)  # 절단·재작성하지 않는다.
        return draft.draft.text

    async def create_preview(self, owner_id, session_id, body):
        await self.draft_text(owner_id, session_id, body)
        # 확인 증거·지속성 실행기가 없는 상태에서 성공 예약/거짓 confirmed를 만들지 않는다.
        raise AppError(503, "COMIC_PREVIEW_NOT_ENABLED", "시각화 확인 계약과 작업 실행 방식 합의 필요")

    async def response(self, preview, token):
        signed, panels = {}, []
        for p in preview.panels:
            asset = None
            if p.outcome == "ready":
                if p.asset.asset_id not in signed:
                    signed[p.asset.asset_id] = await self.repository.sign(preview, p.asset, token)
                asset = signed[p.asset.asset_id]
            panels.append(ComicPanel(position=p.position, caption=p.caption, status=p.outcome,
                                     imageUrl=asset.imageUrl if asset else None,
                                     expires_at=asset.expires_at if asset else None))
        ready = sum(p.outcome == "ready" for p in preview.panels)
        pending = any(p.outcome == "pending" for p in preview.panels)
        unknown = any(p.outcome == "unknown" for p in preview.panels)
        return ComicResponse(
            preview_id=preview.preview_id,
            # 지속성 실행기가 없으므로 pending 업로드를 running/완료 예정으로 표시하지 않는다.
            status="ready" if ready == 4 else "partial" if ready else "failed",
            panels=panels, recovery_required=pending,
            error_code=("COMIC_RESULT_UNKNOWN" if unknown else
                        "COMIC_STORAGE_RECOVERY_REQUIRED" if pending else None),
        )

    async def preview(self, owner_id, token, session_id, preview_id):
        # 영속 결과 자체의 소유권을 확인하므로 메모리 세션 소실 후에도 결과 GET은 가능하다.
        saved = await self.repository.preview(owner_id, token, preview_id, session_id)
        return await self.response(saved, token)

    async def diary(self, owner_id, token, diary_id):
        diary = await self.repository.diaries.owned_diary(owner_id, token, diary_id)
        saved, source_version = await self.repository.diary_preview(owner_id, token, diary_id)
        result = await self.response(saved, token) if saved else ComicResponse(status="none")
        return DiaryComicResponse(**result.model_dump(), current_edit_version=diary["edit_version"],
                                  source_edit_version=source_version,
                                  content_status="none" if saved is None else
                                  "current" if source_version == diary["edit_version"] else "stale")

    async def attach(self, owner_id, token, diary_id, body):
        await self.repository.diaries.owned_diary(owner_id, token, diary_id)
        # DB가 확정 내용/세션/revision/초안/소유권과 멱등성을 한 트랜잭션에서 재검사한다.
        saved = await self.repository.attach(owner_id, diary_id, body)
        return await self.response(saved, token)

    async def persist_result(self, owner_id, session_id, body, plan, rendered):
        """미노출 복구 어댑터: 이미 받은 AI 결과만 저장한다. AI 호출·접수·스케줄링 없음.

        rendered의 None은 결과 불명이다. 동일 바이트로 재호출하면 업로드/DB 완료만 복구한다.
        생성 직후 프로세스 유실 시 바이트 복구를 보장하지 않으며 durable runner의 대체가 아니다.
        """
        text = await self.draft_text(owner_id, session_id, body)
        try:
            validate_plan(plan, text)
            originals = {s.scene_id: s for s in plan.comic4.scenes if not s.repeated}
            if set(rendered) != set(originals):
                raise ValueError
            for key, image in rendered.items():
                if image is not None:
                    validate_render(image, plan, originals[key])
        except ValueError:
            raise AppError(502, "COMIC_AI_BAD_RESPONSE", "만화 결과와 원문 불일치") from None
        preview_id = uuid5(NAMESPACE_URL, f"comic/{owner_id}/{session_id}/{body.client_request_id}")
        normalized, original_panels, panels = {}, {}, []
        for scene in plan.comic4.scenes:
            if scene.repeated:
                original = original_panels[scene.reuse_scene_id]
                panel = original.model_copy(update={"position": scene.order})
            else:
                result, asset = rendered[scene.scene_id], None
                outcome = result.status if result is not None else "unknown"
                if outcome == "generated":
                    async with self.decode_slots:
                        image = await asyncio.to_thread(decode_comic_image, result.imageUrl)
                    asset_id = uuid5(preview_id, scene.scene_id + image.metadata["sha256"])
                    asset = ComicAsset(asset_id=asset_id, **image.metadata)
                    normalized[asset_id] = image
                    outcome = "pending"
                panel = StoredPanel(position=scene.order, caption=scene.caption,
                                    source_ids=scene.source_ids, outcome=outcome, asset=asset)
                original_panels[scene.scene_id] = panel
            panels.append(panel)
        initial = StoredPreview(preview_id=preview_id, user_id=owner_id, session_id=session_id,
                                client_request_id=body.client_request_id,
                                history_revision=body.history_revision, draft_id=body.draft_id,
                                draft_text=text, panels=panels)
        fingerprint = sha256(json.dumps(initial.model_dump(mode="json"), sort_keys=True,
                                        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        saved = await self.repository.save_result(initial, fingerprint)
        return await self._upload_saved(saved, normalized)

    async def recover_result_uploads(self, owner_id, token, preview_id, data_urls):
        """미노출 동일 바이트 복구 도구. 세션 메모리/AI에 의존하지 않는다.

        호출자가 기존 생성 바이트를 실제로 보관한 경우에만 사용할 수 있다.
        DB에는 이미지 바이트가 없으므로 유실한 바이트를 되살리거나 재생성하지 않는다.
        """
        saved = await self.repository.preview(owner_id, token, preview_id)
        if not 1 <= len(data_urls) <= 4:
            raise AppError(422, "COMIC_RECOVERY_INVALID", "복구 자산 개수 오류")
        assets = {p.asset.asset_id: p.asset for p in saved.panels if p.asset is not None}
        if not set(data_urls) <= set(assets):
            raise AppError(422, "COMIC_RECOVERY_INVALID", "복구 자산 불일치")
        normalized = {}
        for asset_id, data_url in data_urls.items():
            async with self.decode_slots:
                image = await asyncio.to_thread(decode_comic_image, data_url)
            if image.metadata != assets[asset_id].model_dump(exclude={"asset_id"}):
                raise AppError(409, "COMIC_RECOVERY_MISMATCH", "기존 이미지와 복구 바이트 불일치")
            normalized[asset_id] = image
        return await self._upload_saved(saved, normalized)

    async def _upload_saved(self, saved, images):
        for asset_id, image in images.items():
            panel = next(p for p in saved.panels if p.asset is not None and p.asset.asset_id == asset_id)
            if panel.outcome == "pending":
                await self.repository.upload(saved, panel.asset, image)
                saved = await self.repository.complete_asset(saved, asset_id)
        return saved
