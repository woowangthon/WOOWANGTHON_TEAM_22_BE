"""실제 계정/네트워크 없이 로컬 RLS 도구의 판정과 비밀정보 보호를 검사합니다."""

import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from scripts import verify_supabase_rls as rls


IDS = ("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
DIARIES = ("00000000-0000-4000-8000-000000000011", "00000000-0000-4000-8000-000000000012")
EMAILS = ("fake-a@example.invalid", "fake-b@example.invalid")
PASSWORDS = ("fake-password-a", "fake-password-b")
TOKENS = ("fake.access.a", "fake.access.b")
REFRESH = "fake-refresh-never-print"
CONFIG = rls.Config("sb_publishable_fake_test_only")
SENSITIVE = (*PASSWORDS, *TOKENS, REFRESH, *EMAILS, CONFIG.publishable_key)


class FakeSupabase:
    def __init__(self, counts=(1, 1), override=None):
        self.counts = counts
        self.override = override
        self.requests = []

    def user(self, index):
        return {"id": IDS[index], "is_anonymous": False}

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == "nnbmxtiouvsuszretahg.supabase.co"
        assert request.headers["apikey"] == CONFIG.publishable_key
        assert request.method == "GET" or (
            request.method == "POST" and request.url.path == "/auth/v1/token"
        )
        assert "/storage/" not in request.url.path
        if self.override:
            response = self.override(request)
            if response is not None:
                return response
        if request.url.path == "/auth/v1/token":
            assert dict(request.url.params) == {"grant_type": "password"}
            assert "Authorization" not in request.headers
            data = json.loads(request.content)
            index = EMAILS.index(data["email"])
            assert data["password"] == PASSWORDS[index]
            return httpx.Response(200, json={
                "access_token": TOKENS[index], "token_type": "bearer",
                "refresh_token": REFRESH, "user": self.user(index),
            })
        auth = request.headers.get("Authorization")
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json=self.user(TOKENS.index(auth.removeprefix("Bearer "))))
        assert request.url.path.removeprefix("/rest/v1/") in rls.TABLES
        assert request.url.params["select"] == "id,user_id"
        if auth is None:
            return httpx.Response(401, json={"code": "42501", "message": "fake permission denied"})
        index = TOKENS.index(auth.removeprefix("Bearer "))
        other_id = request.url.params.get("user_id")
        diary_id = request.url.params.get("id")
        rows = [{"id": DIARIES[index], "user_id": IDS[index]}] if self.counts[index] else []
        if other_id and other_id != f"eq.{IDS[index]}":
            rows = []
        if diary_id and diary_id != f"eq.{DIARIES[index]}":
            rows = []
        return httpx.Response(200, json=rows)


def run(fake, **kwargs):
    return rls.verify(CONFIG, EMAILS[0], PASSWORDS[0], EMAILS[1], PASSWORDS[1],
                      transport=httpx.MockTransport(fake), **kwargs)


def is_db(request):
    return request.url.path.startswith("/rest/v1/")


def test_login_tokens_and_read_only_requests_are_separate():
    fake = FakeSupabase()
    report = run(fake)
    assert report.complete and not report.fixture_confirmed
    assert len(report.tables) == 3
    assert len(fake.requests) == 19
    for table in rls.TABLES:
        requests = [request for request in fake.requests if request.url.path == f"/rest/v1/{table}"]
        assert [request.headers.get("Authorization") for request in requests] == [
            f"Bearer {TOKENS[0]}", f"Bearer {TOKENS[1]}",
            f"Bearer {TOKENS[0]}", f"Bearer {TOKENS[1]}", None,
        ]
        assert requests[2].url.params["user_id"] == f"eq.{IDS[1]}"
        assert requests[3].url.params["user_id"] == f"eq.{IDS[0]}"
    assert all("fake" not in repr(item) for item in report.tables)
    assert TOKENS[0] not in repr(rls.Member(IDS[0], TOKENS[0]))
    assert CONFIG.publishable_key not in repr(CONFIG)


@pytest.mark.parametrize("counts", [(0, 0), (1, 0), (0, 1)])
def test_empty_rows_never_confirm_isolation(counts):
    report = run(FakeSupabase(counts))
    assert not report.complete
    assert all(not item.isolation_confirmed for item in report.tables)


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("status", [400, 401, 403, 422, 429, 500, 302])
def test_failed_login_stops_without_data_api(index, status):
    def override(request):
        if request.url.path == "/auth/v1/token" and json.loads(request.content)["email"] == EMAILS[index]:
            return httpx.Response(status, text=" ".join(SENSITIVE))
    fake = FakeSupabase(override=override)
    with pytest.raises(rls.VerificationError, match="로그인 실패") as caught:
        run(fake)
    assert not any(is_db(request) for request in fake.requests)
    assert all(secret not in str(caught.value) for secret in SENSITIVE)


@pytest.mark.parametrize("key", ["", "sb_secret_fake", "service_role", "eyJhbGci.fake.jwt", "sb_publishable_bad\nvalue"])
def test_reject_non_publishable_keys(key):
    with pytest.raises(rls.VerificationError):
        rls.Config(key)


@pytest.mark.parametrize("url", ["http://nnbmxtiouvsuszretahg.supabase.co", "https://other.supabase.co",
                                rls.PROJECT_URL + "/", rls.PROJECT_URL + "@evil.invalid"])
def test_reject_other_auth_destinations(url):
    with pytest.raises(rls.VerificationError):
        rls.Config(CONFIG.publishable_key, url)


@pytest.mark.parametrize("email,password", [("", "fake"), ("bad", "fake"), (EMAILS[0], "")])
def test_empty_or_invalid_credentials_fail_before_network(email, password):
    fake = FakeSupabase()
    with rls.Probe(CONFIG, httpx.MockTransport(fake)) as probe:
        with pytest.raises(rls.VerificationError):
            probe.login(email, password)
    assert fake.requests == []


def test_same_email_rejected_before_network():
    fake = FakeSupabase()
    with pytest.raises(rls.VerificationError, match="이메일"):
        rls.verify(CONFIG, EMAILS[0], PASSWORDS[0], EMAILS[0].upper(), PASSWORDS[1],
                   transport=httpx.MockTransport(fake))
    assert not fake.requests


@pytest.mark.parametrize("body", [None, [], {},
    {"access_token": "bad\nheader", "token_type": "bearer"},
    {"access_token": 12, "token_type": "bearer"},
    {"access_token": TOKENS[0], "token_type": None},
    {"access_token": TOKENS[0], "token_type": "wrong"},
    {"access_token": TOKENS[0], "token_type": "bearer", "user": {"id": IDS[0]}},
    {"access_token": TOKENS[0], "token_type": "bearer", "user": {"id": IDS[0], "is_anonymous": True}},
    {"access_token": TOKENS[0], "token_type": "bearer", "user": {"id": "bad", "is_anonymous": False}},
])
def test_malformed_auth_success_rejected(body):
    fake = FakeSupabase(override=lambda request: httpx.Response(200, content=json.dumps(body))
                        if request.url.path == "/auth/v1/token" else None)
    with pytest.raises(rls.VerificationError):
        run(fake)


@pytest.mark.parametrize("status,body", [(401, {}), (200, {"id": IDS[1], "is_anonymous": False}),
                                       (200, {"id": IDS[0], "is_anonymous": True})])
def test_auth_server_must_confirm_login_identity(status, body):
    fake = FakeSupabase(override=lambda request: httpx.Response(status, json=body)
                        if request.url.path == "/auth/v1/user" else None)
    with pytest.raises(rls.VerificationError):
        run(fake)
    assert not any(is_db(request) for request in fake.requests)


@pytest.mark.parametrize("same_field", ["id", "token"])
def test_same_user_id_or_token_rejected(same_field):
    fake = FakeSupabase()
    if same_field == "id":
        fake.user = lambda index: {"id": IDS[0], "is_anonymous": False}
    else:
        def override(request):
            if request.url.path == "/auth/v1/token":
                index = EMAILS.index(json.loads(request.content)["email"])
                return httpx.Response(200, json={"access_token": TOKENS[0], "token_type": "bearer",
                                                "user": fake.user(index)})
            if request.url.path == "/auth/v1/user":
                index = sum(item.url.path == "/auth/v1/token" for item in fake.requests) - 1
                return httpx.Response(200, json=fake.user(index))
        fake.override = override
    with pytest.raises(rls.VerificationError, match="동일"):
        run(fake)
    assert not any(is_db(request) for request in fake.requests)


@pytest.mark.parametrize("body", [{}, None, [{"id": DIARIES[0]}],
                                  [{"id": "bad", "user_id": IDS[0]}],
                                  [{"id": DIARIES[1], "user_id": IDS[1]}],
                                  [{"id": DIARIES[0], "user_id": IDS[0], "text": "unexpected"}]])
def test_bad_rows_or_owner_leak_rejected(body):
    fake = FakeSupabase(override=lambda request: httpx.Response(200, content=json.dumps(body))
                        if is_db(request) and "Authorization" in request.headers else None)
    with pytest.raises(rls.VerificationError):
        run(fake)


def test_cross_query_must_be_empty_even_if_server_ignores_filter():
    def override(request):
        if is_db(request) and "user_id" in request.url.params:
            return httpx.Response(200, json=[{"id": DIARIES[0], "user_id": IDS[0]}])
    with pytest.raises(rls.VerificationError):
        run(FakeSupabase(override=override))


@pytest.mark.parametrize("status,body", [(200, []), (200, [{"id": DIARIES[0], "user_id": IDS[0]}]),
    (401, {"message": "invalid key"}), (403, {"code": "bad_gateway"}),
    (500, {"code": "42501"}), (302, {"code": "42501"})])
def test_anon_empty_or_gateway_failure_not_counted_as_db_denial(status, body):
    fake = FakeSupabase(override=lambda request: httpx.Response(status, json=body)
                        if is_db(request) and "Authorization" not in request.headers else None)
    with pytest.raises(rls.VerificationError):
        run(fake)


@pytest.mark.parametrize("status", [401, 403])
def test_unauthenticated_database_permission_denied(status):
    fake = FakeSupabase(override=lambda request: httpx.Response(status, json={"code": "42501"})
                        if is_db(request) and "Authorization" not in request.headers else None)
    assert run(fake).complete


@pytest.mark.parametrize("status", [401, 403, 500, 302])
def test_member_data_api_errors_are_failures(status):
    fake = FakeSupabase(override=lambda request: httpx.Response(status, text=" ".join(SENSITIVE))
                        if is_db(request) and "Authorization" in request.headers else None)
    with pytest.raises(rls.VerificationError):
        run(fake)


@pytest.mark.parametrize("path", ["/auth/v1/token", "/auth/v1/user", "/rest/v1/diaries"])
def test_invalid_json_is_sanitized(path):
    fake = FakeSupabase(override=lambda request: httpx.Response(200, text=" ".join(SENSITIVE))
                        if request.url.path == path else None)
    with pytest.raises(rls.VerificationError, match="JSON") as caught:
        run(fake)
    assert all(secret not in str(caught.value) for secret in SENSITIVE)


@pytest.mark.parametrize("error_class", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_network_exception_is_sanitized_and_not_retried(error_class):
    def override(request):
        raise error_class(" ".join(SENSITIVE), request=request)
    fake = FakeSupabase(override=override)
    with pytest.raises(rls.VerificationError, match="네트워크") as caught:
        run(fake)
    assert len(fake.requests) == 1
    assert all(secret not in str(caught.value) for secret in SENSITIVE)


def test_known_fixture_ids_checked_in_both_directions():
    fake = FakeSupabase()
    report = run(fake, fixture_ids=DIARIES)
    assert report.fixture_confirmed
    requests = [request for request in fake.requests if "id" in request.url.params]
    assert [request.url.params["id"] for request in requests] == [
        f"eq.{DIARIES[0]}", f"eq.{DIARIES[0]}", f"eq.{DIARIES[1]}", f"eq.{DIARIES[1]}",
    ]


def test_only_one_table_without_rows_stays_incomplete():
    fake = FakeSupabase(override=lambda request: httpx.Response(200, json=[])
                        if request.url.path == "/rest/v1/diary_assets"
                        and "Authorization" in request.headers else None)
    report = run(fake)
    assert [item.isolation_confirmed for item in report.tables] == [True, True, False]
    assert not report.complete


def test_members_do_not_share_auth_cookies():
    def override(request):
        if request.url.path == "/auth/v1/token":
            assert "cookie" not in request.headers
            index = EMAILS.index(json.loads(request.content)["email"])
            return httpx.Response(200, json={
                "access_token": TOKENS[index], "token_type": "bearer", "user": fake.user(index),
            }, headers={"Set-Cookie": f"test_member={index}; Path=/; Secure"})
        if "Authorization" not in request.headers:
            assert "cookie" not in request.headers
        else:
            index = TOKENS.index(request.headers["Authorization"].removeprefix("Bearer "))
            assert request.headers["cookie"] == f"test_member={index}"
    fake = FakeSupabase(override=override)
    assert run(fake).complete


@pytest.mark.parametrize("fixtures", [(DIARIES[1], DIARIES[0]), (DIARIES[0], DIARIES[0])])
def test_wrong_fixture_owner_or_duplicate_ids_never_pass(fixtures):
    with pytest.raises(rls.VerificationError):
        run(FakeSupabase(), fixture_ids=fixtures)


def setup_cli(monkeypatch, fake):
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", CONFIG.publishable_key)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setattr(rls.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    emails, passwords = iter(EMAILS), iter(PASSWORDS)
    monkeypatch.setattr("builtins.input", lambda prompt: next(emails))
    monkeypatch.setattr(rls.getpass, "getpass", lambda prompt: next(passwords))
    original = rls.verify
    monkeypatch.setattr(rls, "verify", lambda *args, **kwargs: original(
        *args, **kwargs, transport=httpx.MockTransport(fake),
    ))


@pytest.mark.parametrize("counts,exit_code", [((1, 1), 0), ((0, 0), 2), ((1, 0), 2)])
def test_cli_exit_status_and_no_secrets(monkeypatch, capsys, caplog, counts, exit_code):
    caplog.set_level(logging.DEBUG)
    setup_cli(monkeypatch, FakeSupabase(counts))
    assert rls.main([]) == exit_code
    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    assert all(secret not in combined for secret in SENSITIVE)
    assert ("격리 미완료" in combined) is (exit_code == 2)
    assert logging.root.manager.disable == 0


@pytest.mark.parametrize("error_kind", ["http", "unexpected", "login"])
def test_cli_failure_does_not_log_secrets_or_traceback(monkeypatch, capsys, caplog, error_kind):
    caplog.set_level(logging.DEBUG)
    def override(request):
        logging.getLogger("httpcore").critical(" ".join(SENSITIVE))
        if error_kind == "login":
            return httpx.Response(400, text=" ".join(SENSITIVE))
        error_class = httpx.ConnectError if error_kind == "http" else RuntimeError
        raise error_class(" ".join(SENSITIVE))
    setup_cli(monkeypatch, FakeSupabase(override=override))
    assert rls.main([]) == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    assert "Traceback" not in combined
    assert all(secret not in combined for secret in SENSITIVE)


@pytest.mark.parametrize("args", [["--diary-a-id", DIARIES[0]], ["--diary-a-id", PASSWORDS[0]],
                                  ["--password", PASSWORDS[0]]])
def test_invalid_cli_options_do_not_echo_values(monkeypatch, capsys, args):
    setup_cli(monkeypatch, FakeSupabase())
    assert rls.main(args) == 1
    captured = capsys.readouterr()
    assert all(secret not in captured.out + captured.err for secret in SENSITIVE)


def test_noninteractive_input_rejected(monkeypatch, capsys):
    setup_cli(monkeypatch, FakeSupabase())
    monkeypatch.setattr(rls.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert rls.main([]) == 1
    assert "대화형" in capsys.readouterr().err


def test_getpass_echo_fallback_rejected(monkeypatch, capsys):
    setup_cli(monkeypatch, FakeSupabase())
    def unsafe_getpass(prompt):
        import warnings
        warnings.warn("echo unavailable", getpass_warning)
        pytest.fail("평문 입력에 도달하면 안 됩니다.")
    getpass_warning = rls.getpass.GetPassWarning
    monkeypatch.setattr(rls.getpass, "getpass", unsafe_getpass)
    assert rls.main([]) == 1
    assert "안전한 입력" in capsys.readouterr().err


def test_cli_known_fixture_options(monkeypatch, capsys):
    setup_cli(monkeypatch, FakeSupabase())
    assert rls.main(["--diary-a-id", DIARIES[0], "--diary-b-id", DIARIES[1]]) == 0
    assert "지정한 가상 일기 A/B" in capsys.readouterr().out
