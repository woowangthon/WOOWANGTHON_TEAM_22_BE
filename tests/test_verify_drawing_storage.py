"""실제 계정·원격 요청 없는 검증 도구 안전 경계 모의 테스트."""

import copy
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.services.drawing_images import validate_image
from scripts import verify_drawing_storage as drawing
from tests.conftest import MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY
from tests.test_drawings import DrawingDatabase, DrawingStorage
from tests.test_verify_diary_storage import CREDENTIALS, SETTINGS, FakeSupabase as DiarySupabase


class FakeSupabase(DiarySupabase):
    def __init__(self, mode=None):
        super().__init__(mode)
        self.database = DrawingDatabase()
        self.storage = DrawingStorage()
        row = {"user_id": str(MEMBER_A), "diary_date": "2026-11-08", "weather": "sunny",
               "confirmed_text": "private fixture", "confirmed_at": "2026-11-08T00:00:00Z",
               "ai_draft": {"draft_id": "draft_fixture", "text": "private fixture",
                            "pages": [], "sources": [], "fallback": True, "mock": True}}
        for diary_id, owner in ((drawing.DIARY_C, MEMBER_A), (drawing.DIARY_A, MEMBER_B),
                                (drawing.DIARY_B, MEMBER_B)):
            self.database.diaries[diary_id] = {"id": diary_id, **copy.deepcopy(row), "user_id": str(owner)}
            self.database.messages[diary_id] = [{"position": 0, "role": "child", "text": "private fixture"}]
        if mode == "not_owner":
            self.database.diaries[drawing.DIARY_C]["user_id"] = str(MEMBER_B)
        if mode in {"ready", "pending"}:
            self.existing_asset(mode)
        if mode in {"reserve_lost", "complete_lost"}:
            self.database.failure = mode
        if mode == "upload_lost":
            self.storage.failure = mode

    def existing_asset(self, status):
        asset_id = str(uuid4())
        row = {"id": asset_id, "user_id": str(MEMBER_A), "diary_id": drawing.DIARY_C,
               "asset_kind": "original_drawing", "scene_index": 0, "bucket_id": "diary-private",
               "upload_status": status, **validate_image(drawing.pattern_png()).metadata,
               "storage_key": f"{MEMBER_A}/{drawing.DIARY_C}/original_drawing/{asset_id}.png"}
        self.database.assets[drawing.DIARY_C] = row

    async def __call__(self, request):
        path = request.url.path
        if not path.startswith("/storage/"):
            return await super().__call__(request)
        self.requests.append(request)
        if request.method == "GET" and "/object/sign/" in path:
            assert "apikey" not in request.headers and "authorization" not in request.headers
            key = path.split("/object/sign/diary-private/")[1]
            content = self.storage.objects[key]
            if self.mode == "corrupt_download":
                content = b"wrong image"
            return httpx.Response(200, content=content, headers={"content-type": "image/png"})
        return await self.storage(request)


async def run(fake, tmp_path):
    return await drawing.verify(SETTINGS, CREDENTIALS, transport=httpx.MockTransport(fake),
                                attempt_file=tmp_path / "c.attempt")


@pytest.mark.anyio
async def test_single_upload_persistent_read_download_and_no_diary_changes(tmp_path):
    fake = FakeSupabase()
    before = copy.deepcopy((fake.database.diaries, fake.database.messages))
    result = await run(fake, tmp_path)
    assert result.passed and result.attempted and result.asset_id
    assert (fake.database.diaries, fake.database.messages) == before
    assert (tmp_path / "c.attempt").read_text().strip() == drawing.DIARY_C
    writes = [r for r in fake.requests if r.method == "POST" and r.url.path != "/auth/v1/token"]
    assert len(writes) == 6  # 예약·객체·완료·서명 3회
    assert sum("/object/diary-private/" in r.url.path for r in writes) == 1
    second = await run(fake, tmp_path)
    assert not second.passed and not second.attempted
    assert "ready" in second.statuses[drawing.STAGES[3]]
    assert len(fake.storage.objects) == len(fake.database.assets) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["login_failure", "anonymous", "not_owner", "member_ab", "ready", "pending"])
async def test_preflight_failure_has_no_remote_write_or_attempt_record(tmp_path, mode):
    fake = FakeSupabase(mode)
    result = await run(fake, tmp_path)
    assert not result.passed and not result.attempted
    assert not (tmp_path / "c.attempt").exists()
    assert not any(r.method == "POST" and r.url.path != "/auth/v1/token" for r in fake.requests)
    if mode in {"ready", "pending"}:
        assert mode in result.statuses[drawing.STAGES[3]]
        assert result.asset_id == fake.database.assets[drawing.DIARY_C]["id"]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["reserve_lost", "upload_lost", "complete_lost", "corrupt_download"])
async def test_remote_failure_never_retries_and_retains_marker(tmp_path, mode):
    fake = FakeSupabase(mode)
    result = await run(fake, tmp_path)
    assert not result.passed and result.attempted
    assert (tmp_path / "c.attempt").exists()
    if mode != "reserve_lost":
        assert result.asset_id == fake.database.assets[drawing.DIARY_C]["id"]
    writes = [r for r in fake.requests if r.method == "POST" and r.url.path != "/auth/v1/token"
              and "/object/sign/" not in r.url.path]
    assert len({str(r.url) for r in writes}) == len(writes)
    count = len(writes)
    second = await run(fake, tmp_path)
    assert not second.passed and not second.attempted
    assert len([r for r in fake.requests if r.method == "POST" and r.url.path != "/auth/v1/token"
                and "/object/sign/" not in r.url.path]) == count
    assert not any(r.method in {"DELETE", "PUT", "PATCH"} for r in fake.requests)


@pytest.mark.anyio
async def test_attempt_marker_alone_blocks_upload(tmp_path):
    (tmp_path / "c.attempt").touch()
    fake = FakeSupabase()
    result = await run(fake, tmp_path)
    assert not result.passed and not result.attempted and not fake.database.assets
    assert all(r.method == "GET" or r.url.path == "/auth/v1/token" for r in fake.requests)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["other_host", "wrong_owner", "other_rpc", "delete", "unarmed", "twice"])
async def test_transport_rejects_unapproved_or_repeated_write(mode):
    sent = []
    def handler(request):
        sent.append(request)
        raise httpx.ReadTimeout("private failure")
    guard = drawing.DrawingTransport(SETTINGS, inner=httpx.MockTransport(handler))
    guard.owner, guard.token, guard.armed = MEMBER_A, "Bearer fake-member-a", mode != "unarmed"
    path = "/rest/v1/rpc/reserve_diary_drawing"
    payload = {"p_user_id": str(MEMBER_B if mode == "wrong_owner" else MEMBER_A),
               "p_diary_id": drawing.DIARY_C,
               **{"p_" + key: value for key, value in guard.image.metadata.items()}}
    if mode == "other_host":
        path = "https://other.invalid" + path
    if mode == "other_rpc":
        path = "/rest/v1/rpc/confirm_member_diary"
    async with httpx.AsyncClient(base_url=drawing.PROJECT_URL, transport=guard,
                                 headers={"apikey": guard.secret}) as http:
        if mode == "twice":
            with pytest.raises(httpx.ReadTimeout):
                await http.post(path, json=payload)
        with pytest.raises(ValueError):
            await http.request("DELETE" if mode == "delete" else "POST", path, json=payload)
    assert len(sent) == (1 if mode == "twice" else 0)


def test_deterministic_png_and_exclusive_marker(tmp_path):
    png = drawing.pattern_png()
    assert png == drawing.pattern_png()
    assert sha256(png).hexdigest() == "129e47c486d5da00d28ab55ccdc61792891bf6b2f9fd667729c0a8fb4119a347"
    assert validate_image(png).width == validate_image(png).height == 8
    path = tmp_path / "c.attempt"
    drawing.claim_attempt(path)
    with pytest.raises(FileExistsError):
        drawing.claim_attempt(path)


@pytest.mark.parametrize("argv,secret,tty", [([], True, True), (["--upload-member-c-once"], False, True),
                                            (["--upload-member-c-once"], True, False)])
def test_cli_preflight_requires_opt_in_secret_and_hidden_terminal(monkeypatch, capsys, argv, secret, tty):
    monkeypatch.setattr(drawing.sys, "stdin", SimpleNamespace(isatty=lambda: tty))
    monkeypatch.setattr(drawing.Settings, "from_env", lambda: SETTINGS.model_copy(
        update={} if secret else {"supabase_secret_key": None}))
    monkeypatch.setattr(drawing.getpass, "getpass", lambda prompt: pytest.fail("unexpected input"))
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "private")
    assert drawing.main(argv) == 1
    assert "SUPABASE_SECRET_KEY" not in drawing.os.environ
    assert "private" not in capsys.readouterr().err


def test_cli_hides_secrets_and_response_content(monkeypatch, capsys):
    monkeypatch.setattr(drawing.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(drawing.Settings, "from_env", lambda: SETTINGS)
    answers = iter((CREDENTIALS.email, CREDENTIALS.password))
    monkeypatch.setattr(drawing.getpass, "getpass", lambda prompt: next(answers))
    sensitive = (CREDENTIALS.email, CREDENTIALS.password, "fake-member-a", TEST_PUBLISHABLE_KEY,
                 SETTINGS.supabase_secret_key.get_secret_value(), "?token=private", "private image")
    async def fail(*args, **kwargs):
        raise ValueError(json.dumps(sensitive))
    monkeypatch.setattr(drawing, "verify", fail)
    assert drawing.main(["--upload-member-c-once"]) == 1
    output = capsys.readouterr()
    assert all(value not in output.out + output.err for value in sensitive)
