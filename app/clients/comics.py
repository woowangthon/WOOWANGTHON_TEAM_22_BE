import json
from uuid import UUID

from pydantic import ValidationError

from app.comic_schemas import StoredPreview
from app.errors import AppError

PREVIEW_COLUMNS = ",".join(StoredPreview.model_fields)
CONFLICTS = {"IDEMPOTENCY_CONFLICT", "COMIC_PREVIEW_MISMATCH", "COMIC_NOT_READY",
             "COMIC_ALREADY_ATTACHED"}


def invalid_comic():
    return AppError(502, "COMIC_STORAGE_INVALID_RESPONSE", "만화 저장소 응답 오류")


class ComicRepository:
    """회원 JWT 읽기·서버 RPC 쓰기. 데이터 URL/서명 URL은 DB에 저장하지 않는다."""
    def __init__(self, diaries, storage):
        self.diaries, self.storage = diaries, storage

    @staticmethod
    def decode(data, owner_id, session_id=None, preview_id=None):
        try:
            result = StoredPreview.model_validate_json(json.dumps(data))
            if (result.user_id != owner_id
                    or session_id is not None and result.session_id != session_id
                    or preview_id is not None and result.preview_id != preview_id):
                raise ValueError
            return result
        except (ValidationError, ValueError, TypeError):
            raise invalid_comic() from None

    async def _rows(self, path, token, params):
        return await self.diaries._rows("/rest/v1/" + path, token, params)

    async def preview(self, owner_id, token, preview_id, session_id=None):
        rows = await self._rows("comic_previews", token, {
            "user_id": f"eq.{owner_id}", "preview_id": f"eq.{preview_id}",
            "select": PREVIEW_COLUMNS, "limit": "2",
            **({"session_id": f"eq.{session_id}"} if session_id is not None else {}),
        })
        if not rows:
            raise AppError(404, "COMIC_PREVIEW_NOT_FOUND", "미리보기 없음")
        if len(rows) != 1:
            raise invalid_comic()
        return self.decode(rows[0], owner_id, session_id, preview_id)

    async def diary_preview(self, owner_id, token, diary_id):
        rows = await self._rows("diary_comics", token, {
            "user_id": f"eq.{owner_id}", "diary_id": f"eq.{diary_id}",
            "select": "preview_id,source_edit_version", "limit": "2",
        })
        try:
            if len(rows) > 1:
                raise ValueError
            preview_id = UUID(rows[0]["preview_id"]) if rows else None
            version = rows[0]["source_edit_version"] if rows else None
            if rows and (type(version) is not int or version < 1):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise invalid_comic() from None
        return ((await self.preview(owner_id, token, preview_id)), version) if preview_id else (None, None)

    async def _rpc(self, name, payload):
        result = await self.diaries._request("POST", "/rest/v1/rpc/" + name, json=payload)
        if isinstance(result, dict) and "error" in result:
            code = result["error"]
            if isinstance(code, str) and code in CONFLICTS:
                raise AppError(409, code, "만화 연결 또는 요청 상태 충돌")
            if code == "COMIC_PREVIEW_NOT_FOUND":
                raise AppError(404, code, "미리보기 없음")
            raise invalid_comic()
        return result

    async def save_result(self, preview, fingerprint):
        data = await self._rpc("save_comic_preview_result", {
            "p_preview": preview.model_dump(mode="json"), "p_fingerprint": fingerprint,
        })
        result = self.decode(data, preview.user_id, preview.session_id, preview.preview_id)
        # 완료 재전송에서는 pending만 ready로 바뀔 수 있다.
        expected = preview.model_dump(mode="json")
        actual = result.model_dump(mode="json")
        for panel in actual["panels"]:
            if panel["outcome"] == "ready":
                panel["outcome"] = "pending"
        if actual != expected:
            raise invalid_comic()
        return result

    async def complete_asset(self, preview, asset_id):
        data = await self._rpc("complete_comic_preview_asset", {
            "p_user_id": str(preview.user_id), "p_preview_id": str(preview.preview_id),
            "p_asset_id": str(asset_id),
        })
        result = self.decode(data, preview.user_id, preview.session_id, preview.preview_id)
        expected = preview.model_dump(mode="json")
        for panel in expected["panels"]:
            if panel["asset"] is not None and panel["asset"]["asset_id"] == str(asset_id):
                panel["outcome"] = "ready"
        if result.model_dump(mode="json") != expected:
            raise invalid_comic()
        return result

    async def attach(self, owner_id, diary_id, body):
        data = await self._rpc("attach_diary_comic", {
            "p_user_id": str(owner_id), "p_diary_id": str(diary_id),
            "p_preview_id": str(body.preview_id), "p_request_id": body.client_request_id,
        })
        return self.decode(data, owner_id, preview_id=body.preview_id)

    @staticmethod
    def asset_row(preview, asset):
        # 기존 002 restrictive 정책에 맞는 UUID/UUID/comic_scene/UUID.png 경로.
        # 두 번째 UUID는 저장 전 preview_id이며 원본 그림 경로와 충돌하지 않는다.
        return {"id": str(asset.asset_id), **asset.model_dump(exclude={"asset_id"}),
                "storage_key": (f"{preview.user_id}/{preview.preview_id}/comic_scene/"
                                f"{asset.asset_id}.png")}

    async def upload(self, preview, asset, image):
        await self.storage.upload(self.asset_row(preview, asset), image)

    async def sign(self, preview, asset, token):
        return await self.storage.signed_asset(self.asset_row(preview, asset), token)
