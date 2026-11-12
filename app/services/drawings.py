import asyncio

from starlette.concurrency import run_in_threadpool

from app.drawing_schemas import DrawingResponse
from app.services.drawing_images import validate_image


class DrawingService:
    def __init__(self, repository):
        self.repository = repository
        self.image_slots = asyncio.Semaphore(2)

    async def save(self, owner_id, token, diary_id, content):
        async with self.image_slots:
            image = await run_in_threadpool(validate_image, content)
        # 자산 저장 복구
        row = await self.repository.reserve(owner_id, diary_id, image)
        if row["upload_status"] != "ready":
            await self.repository.upload(row, image)
            await self.repository.finish(owner_id, diary_id, row)
        asset = await self.repository.signed_asset(row, token)
        return DrawingResponse(diary_id=diary_id, asset=asset)
