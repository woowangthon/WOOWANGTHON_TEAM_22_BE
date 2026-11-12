import base64
from io import BytesIO

import httpx
from PIL import Image
import pytest

from app.clients.comic_ai import ComicAIClient, sources_for, validate_plan
from app.errors import AppError
from app.services.comic_images import decode_comic_image
from tests.fake_comics import image_for, plan_for
from tests.test_drawings import picture


@pytest.mark.anyio
async def test_ai_requires_separate_explicit_input_and_scene_confirmation():
    requests = []
    plan = plan_for("공원에 갔다. 집에 왔다.")
    async def handler(request):
        requests.append(request)
        assert "authorization" not in request.headers and "apikey" not in request.headers
        return httpx.Response(200, json=(plan.model_dump() if request.url.path.endswith("visual-story")
                                         else image_for(plan, plan.comic4.scenes[0]).model_dump()))
    async with httpx.AsyncClient(base_url="http://ai.test", transport=httpx.MockTransport(handler)) as http:
        ai = ComicAIClient(http, 1)
        with pytest.raises(AppError, match="COMIC_CONFIRMATION_REQUIRED"):
            await ai.plan(plan.diary_text)
        assert not requests
        received = await ai.plan(plan.diary_text, input_confirmed=True)
        with pytest.raises(AppError, match="COMIC_CONFIRMATION_REQUIRED"):
            await ai.render(received, received.comic4.scenes[0])
        assert len(requests) == 1
        result = await ai.render(received, received.comic4.scenes[0], scene_confirmed=True)
        assert result.status == "generated"
        with pytest.raises(AppError, match="COMIC_SCENE_INVALID"):
            await ai.render(received, received.comic4.scenes[-1], scene_confirmed=True)
        assert len(requests) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["source_message_id", "text", "hash", "caption", "order",
                                    "missing", "reuse", "requires_confirmation", "image_url"])
async def test_rejects_mismatched_plan_without_rewriting_original(change):
    plan = plan_for("공원에 갔다. 집에 왔다.")
    data = plan.model_dump()
    if change == "source_message_id": data["sources"][0]["id"] = 1
    if change == "text": data["diary_text"] += " 새 사건."
    if change == "hash": data["diary_hash"] = "0" * 64
    if change == "caption": data["comic4"]["scenes"][0]["caption"] = "없는 사건"
    if change == "order": data["comic4"]["scenes"][0]["order"] = 2
    if change == "missing": data["comic4"]["scenes"][0]["source_ids"] = [1]
    if change == "reuse": data["comic4"]["scenes"][-1]["reuse_scene_id"] = "unknown"
    if change == "requires_confirmation": data["requires_confirmation"] = False
    if change == "image_url": data["comic4"]["scenes"][0]["imageUrl"] = "https://evil.invalid/x"
    async with httpx.AsyncClient(base_url="http://ai.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=data))) as http:
        with pytest.raises(AppError, match="COMIC_AI_BAD_RESPONSE"):
            await ComicAIClient(http, 1).plan(plan.diary_text, input_confirmed=True)


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 80])
def test_plan_coverage_and_repeated_scene_rules(count):
    text = "\n".join(f"{i}번 장면." for i in range(count))
    validate_plan(plan_for(text), text)


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["hash", "scene_key", "caption", "source_ids", "mock_generated",
                                    "failed_url", "missing_url", "confirmation"])
async def test_image_http200_is_not_enough(change):
    plan = plan_for("공원에 갔다.")
    scene = plan.comic4.scenes[0]
    data = image_for(plan, scene).model_dump()
    if change in {"hash", "scene_key"}: data["diary_hash" if change == "hash" else change] = "0"*64
    if change == "caption": data["caption"] = "다른 사건"
    if change == "source_ids": data["source_ids"] = [1]
    if change == "mock_generated": data["mock"] = True
    if change == "failed_url": data["status"] = "failed"
    if change == "missing_url": data["imageUrl"] = None
    if change == "confirmation": data["requires_confirmation"] = False
    async with httpx.AsyncClient(base_url="http://ai.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=data))) as http:
        with pytest.raises(AppError, match="COMIC_AI_BAD_RESPONSE"):
            await ComicAIClient(http, 1).render(plan, scene, scene_confirmed=True)


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["failed", "rejected", "mock"])
async def test_image_non_generated_is_preserved(status):
    plan = plan_for("공원에 갔다.")
    scene = plan.comic4.scenes[0]
    async with httpx.AsyncClient(base_url="http://ai.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=image_for(plan, scene, status).model_dump()))) as http:
        result = await ComicAIClient(http, 1).render(plan, scene, scene_confirmed=True)
        assert result.status == status and result.imageUrl is None


@pytest.mark.anyio
@pytest.mark.parametrize("outcome,code", [
    (httpx.ReadTimeout("sensitive"), "COMIC_RESULT_UNKNOWN"),
    (httpx.WriteTimeout("sensitive"), "COMIC_RESULT_UNKNOWN"),
    (httpx.ConnectError("sensitive"), "COMIC_AI_CONNECTION_FAILED"),
    (httpx.RemoteProtocolError("sensitive"), "COMIC_RESULT_UNKNOWN"),
    (httpx.Response(503, text="sensitive"), "COMIC_RESULT_UNKNOWN"),
    (httpx.Response(429, text="sensitive"), "COMIC_AI_REJECTED"),
    (httpx.Response(302, headers={"location": "https://evil.invalid"}), "COMIC_AI_REJECTED"),
    (httpx.Response(200, text="not json"), "COMIC_AI_BAD_RESPONSE"),
])
async def test_no_automatic_retry_after_ai_error(outcome, code):
    calls = []
    def handler(request):
        calls.append(request)
        if isinstance(outcome, Exception): raise outcome
        return outcome
    plan = plan_for("공원에 갔다.")
    async with httpx.AsyncClient(base_url="http://ai.test", transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(AppError, match=code) as caught:
            await ComicAIClient(http, 1).render(plan, plan.comic4.scenes[0], scene_confirmed=True)
        assert not caught.value.retryable and len(calls) == 1
        assert "sensitive" not in caught.value.message


@pytest.mark.parametrize("text,code", [("가"*6001, "COMIC_TEXT_LIMIT"),
                                       ("   ", "COMIC_TEXT_LIMIT"),
                                       ("\n".join(["장면"]*81), "COMIC_SOURCE_LIMIT")])
def test_visual_limit_does_not_truncate_or_invent(text, code):
    with pytest.raises(AppError, match=code): sources_for(text)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_data_url_uses_real_format_and_normalized_pixels(fmt):
    raw = picture(fmt)
    data_url = f"data:image/{'png' if fmt == 'PNG' else 'jpeg'};base64," + base64.b64encode(raw).decode()
    image = decode_comic_image(data_url)
    assert image.mime_type == "image/png" and (image.width, image.height) == (16, 12)
    with Image.open(BytesIO(image.content)) as result:
        assert result.format == "PNG" and not result.getexif()


@pytest.mark.parametrize("value", ["https://evil.invalid/x.png", "data:image/svg+xml;base64,PHN2Zz4=",
                                  "data:image/png;base64,@@@@", "data:image/png;base64,",
                                  "data:image/png;base64,"+base64.b64encode(b"bad").decode(),
                                  "data:image/jpeg;base64,"+base64.b64encode(picture()).decode(),
                                  "data:image/png;base64,AAAA\n"])
def test_invalid_images_never_fetch_remote_url(value):
    with pytest.raises(AppError): decode_comic_image(value)


def test_pixel_and_encoded_size_limits():
    from app.services.drawing_images import MAX_BYTES
    with pytest.raises(AppError): decode_comic_image("x" * (4*((MAX_BYTES+2)//3)+65))
    large = picture(size=(8193, 1))
    with pytest.raises(AppError):
        decode_comic_image("data:image/png;base64,"+base64.b64encode(large).decode())


@pytest.mark.anyio
async def test_streamed_ai_response_is_bounded_before_json_decode():
    class Oversized(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(10): yield b"x" * 65536
    async with httpx.AsyncClient(base_url="http://ai.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, stream=Oversized()))) as http:
        with pytest.raises(AppError, match="COMIC_AI_BAD_RESPONSE"):
            await ComicAIClient(http, 1).plan("공원에 갔다.", input_confirmed=True)
