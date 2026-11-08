import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from uuid import UUID, uuid4

import httpx
from PIL import Image, ImageCms
import pytest

from app.config import Settings
from app.main import create_app
from app.services.drawing_images import MAX_BYTES, validate_image
from app.errors import AppError
from tests.conftest import FakeAI, FakeAuth, MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY
from tests.test_saved_diaries import FAKE_SECRET, FakeDatabase, assert_error, prepare


def picture(fmt="PNG", size=(16, 12), color="red", exif=None):
    out = BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt, **({"exif": exif} if exif else {}))
    return out.getvalue()


class DrawingDatabase(FakeDatabase):
    def __init__(self):
        super().__init__()
        self.assets = {}
        self.failure = None

    async def __call__(self, request):
        path = request.url.path
        if path not in {"/rest/v1/diary_assets", "/rest/v1/rpc/reserve_diary_drawing",
                        "/rest/v1/rpc/complete_diary_drawing"}:
            return await super().__call__(request)
        self.requests.append(request)
        if request.method == "GET":
            assert request.headers["apikey"] == TEST_PUBLISHABLE_KEY
            owner = {"Bearer fake-member-a": str(MEMBER_A),
                     "Bearer fake-member-b": str(MEMBER_B)}[request.headers["authorization"]]
            assert request.url.params["user_id"] == "eq." + owner
            diary_id = request.url.params["diary_id"].removeprefix("eq.")
            return httpx.Response(200, json=[copy.deepcopy(row) for row in self.assets.values()
                                           if row["user_id"] == owner and row["diary_id"] == diary_id])
        assert request.headers["apikey"] == FAKE_SECRET
        assert "authorization" not in request.headers
        data = json.loads(request.content)
        owner, diary_id = data["p_user_id"], data["p_diary_id"]
        assert self.diaries[diary_id]["user_id"] == owner
        stage = "reserve" if path.endswith("reserve_diary_drawing") else "complete"
        if self.failure == stage:
            self.failure = None
            return httpx.Response(503, json={"message": "private database failure"})
        row = self.assets.get(diary_id)
        if stage == "reserve":
            metadata = {key.removeprefix("p_"): value for key, value in data.items()
                        if key not in {"p_user_id", "p_diary_id"}}
            if row and any(row[key] != value for key, value in metadata.items()):
                return httpx.Response(409, json={"code": "PT409", "details": "DRAWING_CONFLICT"})
            if row is None:
                asset_id = str(uuid4())
                row = {"id": asset_id, "user_id": owner, "diary_id": diary_id,
                       "asset_kind": "original_drawing", "scene_index": 0,
                       "bucket_id": "diary-private", "upload_status": "pending",
                       "storage_key": f"{owner}/{diary_id}/original_drawing/{asset_id}.png",
                       **metadata}
                self.assets[diary_id] = row
            if self.failure == "reserve_lost":
                self.failure = None
                raise httpx.ReadTimeout("private reserve response lost")
            return httpx.Response(200, json=copy.deepcopy(row))
        assert row["id"] == data["p_asset_id"]
        row["upload_status"] = "ready"
        if self.failure == "complete_lost":
            self.failure = None
            raise httpx.ReadTimeout("private complete response lost")
        return httpx.Response(204)


class DrawingStorage:
    def __init__(self):
        self.objects = {}
        self.requests = []
        self.failure = None

    async def __call__(self, request):
        self.requests.append(request)
        if "/object/sign/" in request.url.path:
            assert request.headers["apikey"] == TEST_PUBLISHABLE_KEY
            assert request.headers["authorization"] == "Bearer fake-member-a"
            assert json.loads(request.content) == {"expiresIn": 300}
            key = request.url.path.split("/object/sign/diary-private/")[1]
            assert key.startswith(str(MEMBER_A) + "/")
            assert key in self.objects
            if self.failure == "sign":
                return httpx.Response(503, json={"message": "private signed link"})
            if self.failure == "foreign_url":
                return httpx.Response(200, json={"signedURL": "https://evil.invalid/?token=private"})
            return httpx.Response(200, json={
                "signedURL": "/object/sign/diary-private/" + key + "?token=fake-private",
            })
        assert request.headers["apikey"] == FAKE_SECRET
        assert "authorization" not in request.headers
        key = request.url.path.split("/object/diary-private/")[1]
        if request.method == "GET":
            return httpx.Response(200, content=self.objects[key])
        assert request.method == "POST" and request.headers["x-upsert"] == "false"
        assert request.headers["content-type"] == "image/png"
        if self.failure == "offline":
            self.failure = None
            raise httpx.ConnectError("private network failure")
        if key in self.objects:
            return httpx.Response(400, json={"error": "Duplicate", "statusCode": "409"})
        self.objects[key] = request.content
        if self.failure == "upload_lost":
            self.failure = None
            raise httpx.ReadTimeout("private upload response lost")
        return httpx.Response(200, json={"Key": key})


@asynccontextmanager
async def drawing_api(db=None, storage=None):
    db, storage = db or DrawingDatabase(), storage or DrawingStorage()
    app = create_app(Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY,
                              supabase_secret_key=FAKE_SECRET),
                     httpx.MockTransport(FakeAI()), httpx.MockTransport(FakeAuth()),
                     httpx.MockTransport(db), httpx.MockTransport(storage))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        base_url="http://be.test", transport=httpx.ASGITransport(app),
        headers={"Authorization": "Bearer fake-member-a"},
    ) as client:
        yield client, db, storage


async def saved_diary(client):
    url, body, _ = await prepare(client)
    result = await client.post(url, json=body)
    assert result.status_code == 200, result.text
    return result.json()


async def upload(client, diary_id, data=None):
    return await client.post(f"/api/diaries/{diary_id}/drawing", files={
        "file": ("../../other-user.svg", picture() if data is None else data, "text/plain"),
    }, data={"user_id": str(MEMBER_B), "storage_key": "evil/path"})


@pytest.mark.anyio
@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
async def test_upload_detail_retry_and_restart(fmt):
    async with drawing_api() as (client, db, storage):
        diary = await saved_diary(client)
        assert diary["imageUrl"] is None and diary["assets"] == []
        diary_id = diary["diary_id"]
        original = picture(fmt)
        first = await upload(client, diary_id, original)
        assert first.status_code == 200, first.text
        asset = first.json()["asset"]
        assert 0 < (datetime.fromisoformat(asset["expires_at"]) - datetime.now(timezone.utc)).total_seconds() <= 300
        assert asset["asset_kind"] == "original_drawing" and asset["mime_type"] == "image/png"
        key, content = next(iter(storage.objects.items()))
        assert key == f"{MEMBER_A}/{diary_id}/original_drawing/{asset['asset_id']}.png"
        UUID(asset["asset_id"])
        assert asset["sha256"] == sha256(content).hexdigest()
        assert asset["byte_size"] == len(content) and (asset["width"], asset["height"]) == (16, 12)
        for _ in range(2):
            retry = await upload(client, diary_id, original)
            assert retry.status_code == 200 and retry.json()["asset"]["asset_id"] == asset["asset_id"]
        different = await upload(client, diary_id, picture(color="blue"))
        assert_error(different, 409, "DRAWING_ALREADY_EXISTS")
        detail = (await client.get(f"/api/diaries/{diary_id}")).json()
        assert detail["confirmed_text"] == diary["confirmed_text"] and detail["ai_draft"] == diary["ai_draft"]
        assert detail["imageUrl"] == asset["imageUrl"] and detail["assets"][0]["asset_id"] == asset["asset_id"]
        assert len(storage.objects) == len(db.assets) == 1
        assert "imageUrl" not in db.assets[diary_id]
        assert len([r for r in storage.requests if "/sign/" not in r.url.path and r.method == "POST"]) == 1
    async with drawing_api(db, storage) as (client, _, _):
        retry = await upload(client, diary_id, original)
        assert retry.status_code == 200 and retry.json()["asset"]["asset_id"] == asset["asset_id"]


@pytest.mark.anyio
async def test_authorization_before_file_or_storage():
    async with drawing_api() as (client, db, storage):
        diary_id = (await saved_diary(client))["diary_id"]
        client.headers["Authorization"] = "Bearer fake-member-b"
        for target in (diary_id, str(uuid4())):
            assert_error(await upload(client, target, b"broken"), 404, "DIARY_NOT_FOUND")
            assert_error(await client.get(f"/api/diaries/{target}"), 404, "DIARY_NOT_FOUND")
        client.headers.clear()
        assert_error(await upload(client, diary_id), 401, "AUTHENTICATION_FAILED")
        client.headers["Authorization"] = "Bearer fake-member-a"
        assert_error(await upload(client, "invalid"), 422, "VALIDATION_ERROR")
        assert not storage.requests and not db.assets


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["reserve", "reserve_lost", "offline", "upload_lost",
                                         "complete", "complete_lost"])
async def test_partial_failure_retries_same_reservation(failure):
    async with drawing_api() as (client, db, storage):
        diary_id = (await saved_diary(client))["diary_id"]
        if failure in {"offline", "upload_lost"}:
            storage.failure = failure
        else:
            db.failure = failure
        response = await upload(client, diary_id)
        assert response.status_code in {502, 503}, response.text
        assert "private" not in response.text and FAKE_SECRET not in response.text
        if failure not in {"reserve", "complete_lost"}:
            detail = (await client.get(f"/api/diaries/{diary_id}")).json()
            assert detail["imageUrl"] is None and detail["assets"] == []
            previous = copy.deepcopy(storage.objects)
            assert_error(await upload(client, diary_id, picture(color="blue")),
                         409, "DRAWING_ALREADY_EXISTS")
            assert storage.objects == previous
        retry = await upload(client, diary_id)
        assert retry.status_code == 200, retry.text
        assert len(db.assets) == len(storage.objects) == 1
        assert db.assets[diary_id]["upload_status"] == "ready"
        assert all(r.method != "DELETE" for r in db.requests + storage.requests)


@pytest.mark.anyio
async def test_concurrent_same_upload_and_collision_integrity():
    async with drawing_api() as (client, db, storage):
        diary_id = (await saved_diary(client))["diary_id"]
        db.failure = "complete"
        assert (await upload(client, diary_id)).status_code == 503
        key = next(iter(storage.objects))
        storage.objects[key] = b"unexpected object"
        assert_error(await upload(client, diary_id), 503, "DRAWING_STORAGE_UNAVAILABLE")
        assert db.assets[diary_id]["upload_status"] == "pending"
        storage.objects[key] = validate_image(picture()).content
        responses = await asyncio.gather(upload(client, diary_id), upload(client, diary_id))
        assert all(r.status_code == 200 for r in responses)
        assert len({r.json()["asset"]["asset_id"] for r in responses}) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["sign", "foreign_url"])
async def test_sign_failure_preserves_ready_and_reissues(failure):
    async with drawing_api() as (client, db, storage):
        diary_id = (await saved_diary(client))["diary_id"]
        storage.failure = failure
        assert_error(await upload(client, diary_id), 503, "DRAWING_READ_UNAVAILABLE")
        assert db.assets[diary_id]["upload_status"] == "ready"
        client.headers["Authorization"] = "Bearer fake-member-b"
        count = len(storage.requests)
        assert (await client.get(f"/api/diaries/{diary_id}")).status_code == 404
        assert len(storage.requests) == count
        client.headers["Authorization"] = "Bearer fake-member-a"
        storage.failure = None
        assert (await client.get(f"/api/diaries/{diary_id}")).json()["imageUrl"]


@pytest.mark.anyio
@pytest.mark.parametrize("data,status", [(b"", 422), (b"not a picture", 422),
                                         (picture()[:-16], 422),
                                         (picture("GIF"), 422),
                                         (b"a" * (MAX_BYTES + 1), 413),
                                         (picture(size=(8193, 1)), 413),
                                         (picture(size=(4001, 4000)), 413)])
async def test_invalid_input_never_reserves_or_uploads(data, status):
    async with drawing_api() as (client, db, storage):
        diary_id = (await saved_diary(client))["diary_id"]
        response = await upload(client, diary_id, data)
        assert response.status_code == status, response.text
        assert not db.assets and not storage.requests


def test_pixel_limits_exact_byte_limits_and_metadata():
    assert validate_image(picture(size=(8192, 1))).width == 8192
    assert validate_image(picture(size=(4000, 4000))).height == 4000
    source = picture()
    assert validate_image(source + b"\0" * (MAX_BYTES - len(source))).metadata["sha256"]
    with pytest.raises(AppError) as error:
        validate_image(source + b"\0" * (MAX_BYTES + 1 - len(source)))
    assert error.value.status == 413
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private author"
    image = validate_image(picture("JPEG", exif=exif))
    assert (image.width, image.height) == (12, 16)
    with Image.open(BytesIO(image.content)) as clean:
        assert not clean.getexif() and not clean.info


def test_transparency_and_decoded_pixels_preserved():
    out = BytesIO()
    source = Image.new("RGBA", (7, 5), (20, 30, 40, 123))
    source.save(out, format="PNG")
    result = validate_image(out.getvalue())
    with Image.open(BytesIO(result.content)) as clean:
        assert clean.mode == "RGBA" and clean.tobytes() == source.tobytes()


def test_color_profile_conversion_is_repeatable_without_metadata():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for mode in ("RGB", "RGBA", "P"):
        source = Image.new("RGB", (8, 4), (20, 40, 60)).convert(mode)
        if mode == "P":
            source.info["transparency"] = 0
        out = BytesIO()
        source.save(out, format="PNG", icc_profile=profile)
        first = validate_image(out.getvalue())
        assert first.content == validate_image(out.getvalue()).content
        with Image.open(BytesIO(first.content)) as clean:
            assert clean.convert("RGBA").tobytes() == source.convert("RGBA").tobytes()
            assert not clean.info


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [("user_id", str(MEMBER_B)),
                                         ("diary_id", str(uuid4())),
                                         ("storage_key", "other/member/private.png"),
                                         ("bucket_id", "public")])
async def test_untrusted_asset_row_never_signs_foreign_path(field, value):
    async with drawing_api() as (client, db, storage):
        diary_id = (await saved_diary(client))["diary_id"]
        assert (await upload(client, diary_id)).status_code == 200
        db.assets[diary_id][field] = value
        count = len(storage.requests)
        assert_error(await upload(client, diary_id), 502, "DIARY_STORAGE_INVALID_RESPONSE")
        assert len(storage.requests) == count


@pytest.mark.anyio
async def test_multipart_stream_limit_and_missing_field():
    async with drawing_api() as (client, db, storage):
        diary_id = (await saved_diary(client))["diary_id"]
        path = f"/api/diaries/{diary_id}/drawing"
        assert (await client.post(path, files={"other": ("a.png", picture())})).status_code == 422
        async def chunks():
            yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="a"\r\n\r\n'
            for _ in range(12):
                yield b"x" * 1024 * 1024
            yield b"\r\n--test--\r\n"
        response = await client.post(path, content=chunks(), headers={
            "Content-Type": "multipart/form-data; boundary=test",
        })
        assert response.status_code == 413, response.text
        assert not db.assets and not storage.requests
