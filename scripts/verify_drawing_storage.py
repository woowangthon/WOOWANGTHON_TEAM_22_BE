"""기존 회원 C 일기의 가상 그림 1회 업로드·실제 Storage 검증 준비 도구."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import getpass
from hashlib import sha256
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import struct
import sys
import warnings
from uuid import UUID
import zlib

import httpx
from PIL import Image

from app.config import Settings
from app.diary_schemas import DiaryDetail
from app.drawing_schemas import DrawingResponse
from app.services.drawing_images import validate_image
from scripts.verify_diary_reads import (
    Credentials, DIARY_A, DIARY_B, PROJECT_URL, ReadOnlyTransport, denied, login,
)
from scripts.verify_diary_storage import require

DIARY_C = "a73ce25b-581c-47fe-bc9b-ba6b9741a460"
ATTEMPT_FILE = Path(__file__).resolve().parents[1] / ".drawing-verification" / (DIARY_C + ".attempt")
STAGES = (
    "실제 Auth 로그인", "C 일기 소유권", "A/B 일기 접근 차단", "그림 중복 확인",
    "기존 C 상세 조회", "가상 PNG 생성", "그림 업로드·실제 RPC·Storage",
    "ready 자산 소유권·메타데이터", "업로드 후 상세 조회·기존 내용 보존",
    "새 BE 앱 상세 재조회", "비공개 임시 URL·다운로드·이미지 내용",
)


def pattern_png():
    """8×8 고정 체크 패턴. 무압축 DEFLATE로 입력 PNG 바이트 고정."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\0" + b"".join(
        bytes((32, 96, 192) if (x + y) % 2 else (240, 192, 64)) for x in range(8)
    ) for y in range(8))
    compressed = (b"\x78\x01\x01" + struct.pack("<HH", len(raw), 65535 - len(raw))
                  + raw + struct.pack(">I", zlib.adler32(raw)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


class DrawingTransport(ReadOnlyTransport):
    """실제 요청 허용 목록·단일 예약/객체 업로드/완료 제한."""

    def __init__(self, settings, *, inner=None):
        super().__init__(inner=inner)
        self.public = settings.supabase_publishable_key.get_secret_value()
        self.secret = (settings.supabase_secret_key.get_secret_value()
                       if settings.supabase_secret_key else None)
        self.owner = self.token = self.asset_id = self.signed_url = None
        self.armed = False
        self.sent = set()
        self.image = validate_image(pattern_png())

    @property
    def object_path(self):
        require(self.owner is not None and self.asset_id is not None)
        return f"/storage/v1/object/diary-private/{self.owner}/{DIARY_C}/original_drawing/{self.asset_id}.png"

    async def handle_async_request(self, request):
        url, path, method = request.url, request.url.path, request.method
        require(url.scheme == "https" and url.host == httpx.URL(PROJECT_URL).host
                and url.port in {None, 443} and not url.userinfo and not url.fragment)
        if method == "GET" and self.signed_url is not None and str(url) == self.signed_url:
            require(not any(name in request.headers for name in ("apikey", "authorization", "cookie")))
            return await self.inner.handle_async_request(request)
        require(not url.query or (method == "GET" and path.startswith("/rest/v1/")))
        public = request.headers.get("apikey") == self.public
        member = public and request.headers.get("authorization") == self.token and self.owner is not None
        server = (self.secret is not None and request.headers.get("apikey") == self.secret
                  and "authorization" not in request.headers)
        if method == "GET" and path == "/auth/v1/user":
            require(public and request.headers.get("authorization", "").startswith("Bearer "))
        elif method == "GET" and path in {"/rest/v1/diaries", "/rest/v1/diary_messages", "/rest/v1/diary_assets"}:
            require(member and request.url.params.get("user_id") == f"eq.{self.owner}")
            key = "id" if path == "/rest/v1/diaries" else "diary_id"
            allowed = (DIARY_C, DIARY_A, DIARY_B) if key == "id" else (DIARY_C,)
            require(request.url.params.get(key) in {"eq." + value for value in allowed})
        elif method == "POST" and path.startswith("/storage/v1/object/sign/diary-private/"):
            prefix = f"/storage/v1/object/sign/diary-private/{self.owner}/{DIARY_C}/original_drawing/"
            require(member and path.startswith(prefix) and path.endswith(".png")
                    and str(UUID(path.removeprefix(prefix).removesuffix(".png")))
                    == path.removeprefix(prefix).removesuffix(".png")
                    and json.loads(request.content) == {"expiresIn": 300})
        else:
            require(self.armed and server and method == "POST" and path not in self.sent)
            payload = None if path.startswith("/storage/") else json.loads(request.content)
            if path == "/rest/v1/rpc/reserve_diary_drawing":
                require(not self.sent and payload == {
                    "p_user_id": str(self.owner), "p_diary_id": DIARY_C,
                    **{"p_" + key: value for key, value in self.image.metadata.items()},
                })
            elif path == "/rest/v1/rpc/complete_diary_drawing":
                require(self.object_path in self.sent and payload == {
                    "p_user_id": str(self.owner), "p_diary_id": DIARY_C, "p_asset_id": self.asset_id,
                })
            else:
                require(path == self.object_path and request.headers.get("x-upsert") == "false"
                        and request.headers.get("content-type") == "image/png"
                        and request.content == self.image.content)
            # 전송 직전 소진. 실패·응답 유실·충돌 후 자동 복구 GET도 차단.
            self.sent.add(path)
        response = await self.inner.handle_async_request(request)
        if path == "/rest/v1/rpc/reserve_diary_drawing" and response.is_success:
            await response.aread()
            row = response.json()
            asset_id = str(UUID(row["id"]))
            require(row["user_id"] == str(self.owner) and row["diary_id"] == DIARY_C
                    and row["upload_status"] == "pending")
            self.asset_id = asset_id
        return response


def no_ai(request):
    raise ValueError("AI 호출 차단")


@asynccontextmanager
async def local_be(settings, guard):
    from app.main import create_app

    app = create_app(settings, ai_transport=httpx.MockTransport(no_ai),
                     auth_transport=guard, db_transport=guard, storage_transport=guard)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        base_url="http://be.local", transport=httpx.ASGITransport(app=app),
        follow_redirects=False, trust_env=False,
    ) as be:
        yield app, be


@dataclass
class Result:
    statuses: dict = field(default_factory=lambda: dict.fromkeys(STAGES, "미실행"))
    asset_id: str | None = None
    attempted: bool = False

    @property
    def passed(self):
        return all(value == "성공" for value in self.statuses.values())


def claim_attempt(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # 프로세스 간 배타 생성·디스크 반영. 성공/실패 모두 기록 보존.
    with path.open("x", encoding="ascii") as marker:
        marker.write(DIARY_C + "\n")
        marker.flush()
        os.fsync(marker.fileno())


def unchanged(detail):
    return detail.model_dump(mode="json", exclude={"assets", "imageUrl"})


def check_asset(asset, row, image):
    require(str(asset.asset_id) == row["id"] and asset.asset_kind == "original_drawing")
    require(all(getattr(asset, key) == value and row[key] == value
                for key, value in image.metadata.items()))
    require(asset.expires_at > datetime.now(timezone.utc))


async def verify(settings, credentials, *, transport=None, attempt_file=ATTEMPT_FILE):
    """transport 주입은 원격 네트워크 없는 모의 테스트 전용."""
    result = Result()
    stage = STAGES[0]
    guard = None
    try:
        require(settings.supabase_url == PROJECT_URL and settings.supabase_publishable_key is not None
                and settings.supabase_secret_key is not None)
        guard = DrawingTransport(settings, inner=transport)
        async with local_be(settings, guard) as (app, be):
            async with httpx.AsyncClient(
                base_url=PROJECT_URL, headers={"apikey": guard.public},
                transport=ReadOnlyTransport(login=True, inner=transport),
                timeout=settings.auth_request_timeout_seconds, follow_redirects=False, trust_env=False,
            ) as auth:
                member = await login(auth, app.state.auth_client, credentials)
            guard.owner, guard.token = member.user_id, f"Bearer {member.token}"
            be.headers["Authorization"] = guard.token
            repository = app.state.drawing_service.repository
            result.statuses[stage] = "성공"

            stage = STAGES[1]
            await repository.diaries.owned_diary(member.user_id, guard.token, UUID(DIARY_C))
            result.statuses[stage] = "성공"
            stage = STAGES[2]
            for diary_id in (DIARY_A, DIARY_B):
                require(denied(await be.get(f"/api/diaries/{diary_id}")))
            result.statuses[stage] = "성공"

            stage = STAGES[3]
            rows = await repository.rows(member.user_id, guard.token, UUID(DIARY_C))
            require(len(rows) <= 1)
            if rows:
                row = repository.validate_row(rows[0], member.user_id, UUID(DIARY_C))
                result.asset_id = row["id"]
                result.statuses[stage] = "실패 (기존 " + row["upload_status"] + "·새 업로드 중단)"
                return result
            if attempt_file.exists():
                result.statuses[stage] = "실패 (시도 기록·재실행 금지)"
                return result
            result.statuses[stage] = "성공"

            stage = STAGES[4]
            detail_path = f"/api/diaries/{DIARY_C}"
            response = await be.get(detail_path)
            require(response.status_code == 200)
            before = DiaryDetail.model_validate(response.json(), strict=False)
            require(str(before.diary_id) == DIARY_C and before.assets == [] and before.imageUrl is None)
            result.statuses[stage] = "성공"
            stage = STAGES[5]
            png = pattern_png()
            image = validate_image(png)
            require(image.width == image.height == 8 and image == guard.image)
            result.statuses[stage] = "성공"

            stage = STAGES[6]
            claim_attempt(attempt_file)
            result.attempted = True
            guard.armed = True
            response = await be.post(detail_path + "/drawing", files={"file": ("test-pattern.png", png, "image/png")})
            require(response.status_code == 200)
            saved = DrawingResponse.model_validate(response.json(), strict=False)
            result.asset_id = str(saved.asset.asset_id)
            require(str(saved.diary_id) == DIARY_C)
            result.statuses[stage] = "성공"

            stage = STAGES[7]
            rows = await repository.rows(member.user_id, guard.token, UUID(DIARY_C))
            require(len(rows) == 1)
            row = repository.validate_row(rows[0], member.user_id, UUID(DIARY_C))
            require(row["upload_status"] == "ready")
            check_asset(saved.asset, row, image)
            result.statuses[stage] = "성공"
            stage = STAGES[8]
            response = await be.get(detail_path)
            require(response.status_code == 200)
            detail = DiaryDetail.model_validate(response.json(), strict=False)
            require(unchanged(detail) == unchanged(before) and len(detail.assets) == 1
                    and detail.imageUrl == detail.assets[0].imageUrl)
            check_asset(detail.assets[0], row, image)
            result.statuses[stage] = "성공"

        stage = STAGES[9]
        read_settings = settings.model_copy(update={"supabase_secret_key": None})
        reader = DrawingTransport(read_settings, inner=transport)
        reader.owner, reader.token = member.user_id, f"Bearer {member.token}"
        async with local_be(read_settings, reader) as (app, be):
            require(not app.state.chat_service.store.sessions)
            response = await be.get(detail_path, headers={"Authorization": reader.token})
            require(response.status_code == 200)
            detail = DiaryDetail.model_validate(response.json(), strict=False)
            require(unchanged(detail) == unchanged(before) and len(detail.assets) == 1
                    and detail.imageUrl == detail.assets[0].imageUrl)
            check_asset(detail.assets[0], row, image)
            result.statuses[stage] = "성공"
            stage = STAGES[10]
            reader.asset_id = result.asset_id
            expected_path = reader.object_path.replace("/object/", "/object/sign/", 1)
            url = httpx.URL(detail.assets[0].imageUrl)
            require(url.path == expected_path and bool(url.query))
            reader.signed_url = str(url)
            async with httpx.AsyncClient(transport=reader, timeout=settings.db_request_timeout_seconds,
                                         follow_redirects=False, trust_env=False) as http:
                async with http.stream("GET", reader.signed_url) as download:
                    require(download.status_code == 200
                            and download.headers.get("content-type", "").split(";")[0] == "image/png")
                    data = bytearray()
                    async for part in download.aiter_bytes():
                        data.extend(part)
                        require(len(data) <= row["byte_size"])
            require(bytes(data) == image.content and len(data) == row["byte_size"]
                    and sha256(data).hexdigest() == row["sha256"])
            with Image.open(BytesIO(data)) as actual, Image.open(BytesIO(png)) as original:
                actual.load()
                require(actual.format == "PNG" and actual.size == (8, 8)
                        and actual.convert("RGB").tobytes() == original.convert("RGB").tobytes())
            result.statuses[stage] = "성공"
    except (Exception, KeyboardInterrupt):
        result.statuses[stage] = "실패"
        if guard is not None and guard.asset_id is not None:
            result.asset_id = guard.asset_id
    return result


def main(argv=None):
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        require((sys.argv[1:] if argv is None else argv) == ["--upload-member-c-once"])
        require(sys.stdin.isatty())
        settings = Settings.from_env()
        require(settings.supabase_url == PROJECT_URL and settings.supabase_publishable_key is not None
                and settings.supabase_secret_key is not None)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            credentials = Credentials(getpass.getpass("회원 C 이메일(숨김): "),
                                      getpass.getpass("회원 C 비밀번호(숨김): "))
        print("BE: 로컬 ASGI / AI: 호출 차단 MockTransport / Supabase Auth·RPC·Storage: 실제 원격")
        result = asyncio.run(verify(settings, credentials))
        for name, status in result.statuses.items():
            print(f"{name}: {status}")
        print(f"일기 ID: {DIARY_C}")
        if result.asset_id:
            print(f"자산 ID: {result.asset_id}")
        if not result.passed:
            print("재실행: 금지 (기존 자산·시도 기록 확인, 동일 Blob 복구 별도 절차)")
        return 0 if result.passed else 1
    except (Exception, KeyboardInterrupt):
        print("실행 준비: 실패 (명시 옵션·설정·숨김 입력 확인)", file=sys.stderr)
        return 1
    finally:
        for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY"):
            os.environ.pop(name, None)
        logging.disable(previous_logging)


if __name__ == "__main__":
    sys.exit(main())
