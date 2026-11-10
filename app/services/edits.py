import asyncio
import json
from hashlib import sha256

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.clients.edits import edit_unavailable
from app.edit_schemas import EditResponse
from app.errors import AppError
from app.services.drawing_images import validate_image


class EditService:
    def __init__(self, repository, drawings):
        self.repository, self.drawings = repository, drawings
        self.image_slots = asyncio.Semaphore(2)

    def validated_row(self, row, owner_id, diary_id):
        try:
            return self.drawings.validate_row(row, owner_id, diary_id)
        except AppError:
            raise edit_unavailable() from None

    async def edit(self, owner_id, token, diary_id, body, content):
        # 업로드 원본 바이트도 식별한다. 같은 ID에 다른 파일을 보내면 충돌이다.
        fingerprint = sha256(json.dumps({
            **body.model_dump(),
            "file_sha256": sha256(content).hexdigest() if content is not None else None,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        image = None
        if content is not None:
            async with self.image_slots:
                image = await run_in_threadpool(validate_image, content)
        payload = {**body.model_dump(), "fingerprint": fingerprint,
                   "drawing": image.metadata if image is not None else None}
        receipt = await self.repository.command(owner_id, diary_id, "reserve", payload)
        if receipt.get("state") == "pending":
            row = receipt.get("drawing")
            if image is not None:
                row = self.validated_row(row, owner_id, diary_id)
                if any(row[k] != v for k, v in image.metadata.items()):
                    raise edit_unavailable()
                try:
                    await self.drawings.upload(row, image)
                except AppError:
                    raise edit_unavailable() from None
            elif row is not None:
                raise edit_unavailable()
            receipt = await self.repository.command(owner_id, diary_id, "finish", {
                "client_request_id": body.client_request_id, "fingerprint": fingerprint,
            })
        if receipt.get("state") != "completed" or not isinstance(receipt.get("result"), dict):
            raise edit_unavailable()
        result = dict(receipt["result"])
        try:
            row = result.pop("original_drawing")
            if image is not None and (row is None or any(row[k] != v for k, v in image.metadata.items())):
                raise edit_unavailable()
            if row is not None:
                row = self.validated_row(row, owner_id, diary_id)
                if row["upload_status"] != "ready":
                    raise edit_unavailable()
                try:
                    asset = await self.drawings.signed_asset(row, token)
                except AppError:
                    raise edit_unavailable() from None
            else:
                asset = None
            result["original_drawing"] = asset
            response = EditResponse.model_validate(result, strict=False)
            if response.diary_id != diary_id or response.edit_version != body.expected_version + 1:
                raise edit_unavailable()
            if response.confirmed_text != body.confirmed_text:
                raise edit_unavailable()
            return response
        except (ValidationError, KeyError, TypeError, ValueError):
            raise edit_unavailable() from None
