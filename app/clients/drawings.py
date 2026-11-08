import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.clients.diaries import invalid_response
from app.drawing_schemas import DrawingAsset
from app.errors import AppError
from app.services.drawing_images import MAX_BYTES

ASSET_COLUMNS = ("id,diary_id,user_id,asset_kind,scene_index,bucket_id,storage_key,"
                 "mime_type,byte_size,width,height,sha256,upload_status")
SIGNED_URL_SECONDS = 300


def drawing_failed():
    return AppError(503, "DRAWING_STORAGE_UNAVAILABLE", "그림 저장 실패", True)


class DrawingRepository:
    def __init__(self, diaries, reader, writer, timeout_seconds):
        self.diaries = diaries
        self.reader = reader
        self.writer = writer
        self.timeout_seconds = timeout_seconds

    async def rows(self, owner_id, token, diary_id):
        return await self.diaries._rows("/rest/v1/diary_assets", token, {
            "user_id": f"eq.{owner_id}", "diary_id": f"eq.{diary_id}",
            "asset_kind": "eq.original_drawing", "scene_index": "eq.0",
            "select": ASSET_COLUMNS, "limit": "2",
        })

    def validate_row(self, row, owner_id, diary_id):
        try:
            asset_id = UUID(row["id"])
            extension = {"image/png": "png", "image/jpeg": "jpg"}[row["mime_type"]]
            expected = f"{owner_id}/{diary_id}/original_drawing/{asset_id}.{extension}"
            if (row["user_id"] != str(owner_id) or row["diary_id"] != str(diary_id)
                    or row["bucket_id"] != "diary-private" or row["storage_key"] != expected
                    or row["asset_kind"] != "original_drawing" or row["scene_index"] != 0
                    or row["upload_status"] not in {"pending", "ready"}):
                raise ValueError
            DrawingAsset.model_validate({
                "asset_id": str(asset_id), **{key: row[key] for key in (
                    "mime_type", "byte_size", "width", "height", "sha256",
                )}, "imageUrl": "", "expires_at": datetime.now(timezone.utc),
            }, strict=False)
            return row
        except (KeyError, TypeError, ValueError, ValidationError):
            raise invalid_response() from None

    async def reserve(self, owner_id, diary_id, image):
        row = await self.diaries._request("POST", "/rest/v1/rpc/reserve_diary_drawing", json={
            "p_user_id": str(owner_id), "p_diary_id": str(diary_id),
            **{"p_" + key: value for key, value in image.metadata.items()},
        })
        return self.validate_row(row, owner_id, diary_id)

    async def finish(self, owner_id, diary_id, row):
        await self.diaries._request("POST", "/rest/v1/rpc/complete_diary_drawing", json={
            "p_user_id": str(owner_id), "p_diary_id": str(diary_id), "p_asset_id": row["id"],
        })

    async def upload(self, row, image):
        if self.writer is None:
            raise drawing_failed()
        path = "/storage/v1/object/diary-private/" + row["storage_key"]
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.writer.post(path, content=image.content, headers={
                    "Content-Type": image.mime_type, "x-upsert": "false",
                    "Cache-Control": "private, no-store",
                })
                if response.is_success:
                    return
                # 동일 객체 충돌 복구
                duplicate = response.status_code == 409
                if response.status_code == 400:
                    try:
                        duplicate = response.json().get("error") == "Duplicate"
                    except (ValueError, AttributeError):
                        pass
                if duplicate:
                    async with self.writer.stream("GET", path) as existing:
                        if existing.is_success:
                            digest, size = sha256(), 0
                            async for chunk in existing.aiter_bytes():
                                size += len(chunk)
                                if size > MAX_BYTES:
                                    raise drawing_failed()
                                digest.update(chunk)
                            if size == row["byte_size"] and digest.hexdigest() == row["sha256"]:
                                return
        except (httpx.HTTPError, TimeoutError):
            pass
        raise drawing_failed()

    async def signed_asset(self, row, token):
        # 회원 JWT 서명 발급
        issued_at = datetime.now(timezone.utc)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.reader.post(
                    "/storage/v1/object/sign/diary-private/" + row["storage_key"],
                    headers={"Authorization": token}, json={"expiresIn": SIGNED_URL_SECONDS},
                )
            response.raise_for_status()
            signed = response.json()["signedURL"]
            expected = "/object/sign/diary-private/" + row["storage_key"]
            parts = urlsplit(signed)
            if (parts.scheme or parts.netloc or parts.path != expected
                    or not parts.query or parts.fragment):
                raise ValueError
            url = str(self.reader.base_url).rstrip("/") + "/storage/v1" + signed
            return DrawingAsset.model_validate({
                "asset_id": row["id"], **{key: row[key] for key in (
                    "mime_type", "byte_size", "width", "height", "sha256",
                )}, "imageUrl": url,
                "expires_at": issued_at + timedelta(seconds=SIGNED_URL_SECONDS),
            }, strict=False)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError):
            raise AppError(503, "DRAWING_READ_UNAVAILABLE", "이미지 조회 실패", True) from None

    async def assets(self, owner_id, token, diary_id):
        rows = await self.rows(owner_id, token, diary_id)
        if len(rows) > 1:
            raise invalid_response()
        result = []
        for row in rows:
            row = self.validate_row(row, owner_id, diary_id)
            if row["upload_status"] == "ready":
                result.append(await self.signed_asset(row, token))
        return result
