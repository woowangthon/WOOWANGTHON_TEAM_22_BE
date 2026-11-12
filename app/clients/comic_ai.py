"""기존 AI 시각화 HTTP 경로만 사용. 확인 증거는 호출자가 명시해야 한다."""
import asyncio
import hashlib
import json
import re

import httpx
from pydantic import ValidationError

from app.comic_schemas import VisualImage, VisualPlan
from app.errors import AppError


def sources_for(text):
    if not text.strip() or len(text) > 6000:
        raise AppError(422, "COMIC_TEXT_LIMIT", "시각화 원문 길이 확인 필요")
    sources = [p.strip() for p in re.split(r"(?<=[.!?。])\s+|\n+", text.strip()) if p.strip()]
    if len(sources) > 80:
        raise AppError(422, "COMIC_SOURCE_LIMIT", "시각화 원문 조각 한도 초과")
    return sources


def text_hash(text):
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def scene_key(digest, source_ids):
    return hashlib.sha256(json.dumps(
        [digest, "comic4", source_ids], separators=(",", ":"),
    ).encode()).hexdigest()


def validate_plan(plan, text):
    expected = sources_for(text)
    if (plan.diary_text != text.strip() or plan.diary_hash != text_hash(text)
            or [(s.id, s.text) for s in plan.sources] != list(enumerate(expected))):
        raise ValueError("일기 원문 불일치")
    seen, flattened = {}, []
    for index, scene in enumerate(plan.comic4.scenes, 1):
        ids = scene.source_ids
        if (scene.order != index or scene.scene_id in seen
                or ids != list(range(ids[0], ids[-1] + 1))
                or any(sid >= len(expected) for sid in ids)
                or scene.caption != "\n".join(expected[sid] for sid in ids)):
            raise ValueError("장면 근거 불일치")
        if scene.repeated:
            original = seen.get(scene.reuse_scene_id)
            if (original is None or original.repeated or original.source_ids != ids
                    or original.caption != scene.caption):
                raise ValueError("반복 장면 불일치")
        else:
            if scene.reuse_scene_id is not None:
                raise ValueError("반복 표시 불일치")
            flattened.extend(ids)
        seen[scene.scene_id] = scene
    if (flattened != list(range(len(expected)))
            or sum(not s.repeated for s in seen.values()) != min(4, len(expected))):
        raise ValueError("원문 누락 또는 추가")


def validate_render(image, plan, scene):
    if (image.diary_hash != plan.diary_hash or image.format != "comic4"
            or image.scene_key != scene_key(plan.diary_hash, scene.source_ids)
            or image.source_ids != scene.source_ids or image.caption != scene.caption
            or (image.status == "generated" and (image.mock or image.imageUrl is None))
            or (image.status != "generated" and image.imageUrl is not None)
            or (image.status == "mock") != image.mock):
        raise ValueError("이미지 응답 근거 불일치")


class ComicAIClient:
    def __init__(self, http, timeout_seconds):
        self.http, self.timeout_seconds = http, timeout_seconds

    async def _post(self, path, payload, limit):
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self.http.stream("POST", path, json=payload) as response:
                    if response.status_code >= 500:
                        raise AppError(502, "COMIC_RESULT_UNKNOWN", "생성 결과 확인 필요")
                    if response.status_code != 200:
                        raise AppError(502, "COMIC_AI_REJECTED", "시각화 요청 처리 실패")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > limit:
                            raise AppError(502, "COMIC_AI_BAD_RESPONSE", "시각화 응답 한도 초과")
            return json.loads(content)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise AppError(503, "COMIC_AI_CONNECTION_FAILED", "시각화 서버 연결 실패") from None
        except (httpx.RequestError, TimeoutError):
            # 전송 후 결과 불명: 새 키/동일 키 모두 자동 재생성하지 않는다.
            raise AppError(504, "COMIC_RESULT_UNKNOWN", "생성 결과 확인 필요") from None
        except (ValueError, UnicodeError):
            raise AppError(502, "COMIC_AI_BAD_RESPONSE", "시각화 응답 형식 오류") from None

    async def plan(self, text, *, input_confirmed=False):
        sources_for(text)
        if input_confirmed is not True:
            raise AppError(409, "COMIC_CONFIRMATION_REQUIRED", "시각화 입력 확인 필요")
        data = await self._post("/api/visual-story", {
            "diary_text": text, "confirmed": True, "format": "comic4",
        }, 256 * 1024)
        try:
            result = VisualPlan.model_validate(data)
            validate_plan(result, text)
            return result
        except (ValidationError, ValueError):
            raise AppError(502, "COMIC_AI_BAD_RESPONSE", "시각화 장면 근거 오류") from None

    async def render(self, plan, scene, *, scene_confirmed=False):
        validate_plan(plan, plan.diary_text)
        if scene_confirmed is not True:
            raise AppError(409, "COMIC_CONFIRMATION_REQUIRED", "장면 글 확인 필요")
        if scene not in plan.comic4.scenes or scene.repeated:
            raise AppError(422, "COMIC_SCENE_INVALID", "원본 장면 확인 필요")
        data = await self._post("/api/visual-story/image", {
            "diary_text": plan.diary_text, "diary_hash": plan.diary_hash,
            "format": "comic4", "source_ids": scene.source_ids, "confirmed": True,
        }, 14 * 1024 * 1024 + 65536)
        try:
            result = VisualImage.model_validate(data)
            validate_render(result, plan, scene)
            return result
        except (ValidationError, ValueError):
            raise AppError(502, "COMIC_AI_BAD_RESPONSE", "이미지 응답 근거 오류") from None
