"""기존 가상 일기 A/B의 실제 Auth·BE GET 조회 검증."""

import asyncio
from dataclasses import dataclass, field
import getpass
import logging
import os
import re
import sys
import warnings
from uuid import UUID

import httpx

from app.config import Settings

PROJECT_URL = "https://nnbmxtiouvsuszretahg.supabase.co"
MONTH = "2026-11"
DIARY_DATE = "2026-11-07"
DIARY_A = "77e0ebce-127d-4bb4-84ef-8d89d6c06a90"
DIARY_B = "49df086e-a986-41ef-8e95-e8997ff270cc"


@dataclass(frozen=True)
class Credentials:
    email: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class Member:
    user_id: UUID = field(repr=False)
    token: str = field(repr=False)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, login=False, inner=None):
        self.login = login
        self.inner = inner if inner is not None else httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        url = request.url
        allowed_get = request.method == "GET" and url.path in {
            "/auth/v1/user", "/rest/v1/diaries", "/rest/v1/diary_messages",
        }
        allowed_login = (
            self.login and request.method == "POST" and url.path == "/auth/v1/token"
            and list(url.params.multi_items()) == [("grant_type", "password")]
        )
        # 대상 호스트와 조회 경로 제한
        if (url.scheme != "https" or url.host != httpx.URL(PROJECT_URL).host
                or url.port not in {None, 443} or url.userinfo
                or not (allowed_get or allowed_login)):
            raise RuntimeError("허용 외 요청 차단")
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


def load_settings():
    # 자식 프로세스의 쓰기 키 제외
    os.environ.pop("SUPABASE_SECRET_KEY", None)
    settings = Settings.from_env()
    if settings.supabase_url != PROJECT_URL or settings.supabase_publishable_key is None:
        raise ValueError("조회 설정 오류")
    return settings


async def login(http, auth_client, credentials):
    if not credentials.email.strip() or "@" not in credentials.email or not credentials.password:
        raise ValueError("회원 입력 오류")
    response = await http.post(
        "/auth/v1/token", params={"grant_type": "password"},
        json={"email": credentials.email.strip(), "password": credentials.password},
    )
    if response.status_code != 200:
        raise ValueError("로그인 실패")
    data = response.json()
    token = data.get("access_token")
    if (str(data.get("token_type", "")).lower() != "bearer"
            or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", token)):
        raise ValueError("로그인 응답 오류")
    # 기존 Auth 서버 검증 재사용
    user_id = await auth_client.verify(token)
    if UUID(data["user"]["id"]) != user_id:
        raise ValueError("회원 정보 불일치")
    return Member(user_id, token)


def own_detail(response, diary_id):
    if response.status_code != 200:
        return False
    data = response.json()
    return data["diary_id"] == diary_id and data["diary_date"] == DIARY_DATE


def own_month(response, diary_id):
    if response.status_code != 200:
        return False
    data = response.json()
    rows = data["diaries"]
    # 기존 가상 일기 한 건 기준
    return (data["month"] == MONTH and len(rows) == 1
            and rows[0]["diary_id"] == diary_id and rows[0]["diary_date"] == DIARY_DATE)


def denied(response):
    return (response.status_code == 404
            and response.json()["error"]["code"] == "DIARY_NOT_FOUND")


async def verify(settings, credentials_a, credentials_b, *, transport=None):
    # 앱 모듈의 환경 설정에서도 쓰기 키 제외
    os.environ.pop("SUPABASE_SECRET_KEY", None)
    from app.main import create_app

    if (settings.supabase_url != PROJECT_URL or settings.supabase_publishable_key is None
            or settings.supabase_secret_key is not None):
        raise ValueError("조회 설정 오류")
    checks = []
    app = create_app(
        settings,
        ai_transport=ReadOnlyTransport(inner=transport),
        auth_transport=ReadOnlyTransport(inner=transport),
        db_transport=ReadOnlyTransport(inner=transport),
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        base_url=PROJECT_URL,
        headers={"apikey": settings.supabase_publishable_key.get_secret_value()},
        timeout=settings.auth_request_timeout_seconds,
        transport=ReadOnlyTransport(login=True, inner=transport),
        follow_redirects=False, trust_env=False,
    ) as auth_http, httpx.AsyncClient(
        base_url="http://be.local", transport=httpx.ASGITransport(app=app),
        follow_redirects=False, trust_env=False,
    ) as be:
        async def check_get(name, path, predicate, member=None):
            try:
                headers = {"Authorization": f"Bearer {member.token}"} if member else {}
                response = await be.get(path, headers=headers)
                passed = bool(predicate(response))
            except Exception:
                passed = False
            checks.append(Check(name, passed))

        for name, path in (
            ("미인증 월별 조회 401", f"/api/diaries?month={MONTH}"),
            ("미인증 A 상세 조회 401", f"/api/diaries/{DIARY_A}"),
            ("미인증 B 상세 조회 401", f"/api/diaries/{DIARY_B}"),
        ):
            await check_get(name, path, lambda response: response.status_code == 401)

        members = []
        for label, credentials in (("A", credentials_a), ("B", credentials_b)):
            try:
                member = await login(auth_http, app.state.auth_client, credentials)
            except Exception:
                member = None
            members.append(member)
            checks.append(Check(f"{label} 실제 로그인·비익명 회원 확인", member is not None))
        member_a, member_b = members
        distinct = (member_a is not None and member_b is not None
                    and member_a.user_id != member_b.user_id and member_a.token != member_b.token
                    and credentials_a.email.strip().casefold() != credentials_b.email.strip().casefold())
        checks.append(Check("A/B 서로 다른 회원 확인", distinct))
        for label, member, own_id, other_id in (
            ("A", member_a, DIARY_A, DIARY_B), ("B", member_b, DIARY_B, DIARY_A),
        ):
            cases = (
                (f"{label} 본인 일기 상세 200", f"/api/diaries/{own_id}",
                 lambda response, diary_id=own_id: own_detail(response, diary_id)),
                (f"{label} 상대 일기 상세 404", f"/api/diaries/{other_id}", denied),
                (f"{label} 월별 본인 일기만 노출", f"/api/diaries?month={MONTH}",
                 lambda response, diary_id=own_id: own_month(response, diary_id)),
            )
            for name, path, predicate in cases:
                if distinct:
                    await check_get(name, path, predicate, member)
                else:
                    checks.append(Check(name, False))
    return checks


def main(argv=None):
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        if (sys.argv[1:] if argv is None else argv):
            raise ValueError("실행 옵션 오류")
        settings = load_settings()
        if not sys.stdin.isatty():
            raise ValueError("대화형 터미널 필요")
        with warnings.catch_warnings():
            # 평문 입력 대체 금지
            warnings.simplefilter("error", getpass.GetPassWarning)
            credentials_a = Credentials(
                getpass.getpass("회원 A 이메일(숨김): "),
                getpass.getpass("회원 A 비밀번호(숨김): "),
            )
            credentials_b = Credentials(
                getpass.getpass("회원 B 이메일(숨김): "),
                getpass.getpass("회원 B 비밀번호(숨김): "),
            )
        checks = asyncio.run(verify(settings, credentials_a, credentials_b))
        for check in checks:
            print(f"{check.name}: {'성공' if check.passed else '실패'}")
        return 0 if checks and all(check.passed for check in checks) else 1
    except (Exception, KeyboardInterrupt):
        # 예외·응답 원문 비노출
        print("검증 실행: 실패", file=sys.stderr)
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    sys.exit(main())
