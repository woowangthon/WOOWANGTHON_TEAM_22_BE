"""사용자가 명시적으로 실행하는 회원 C 가상 일기 1건 영구 저장 검증."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import getpass
import json
import logging
import os
import sys
import warnings
from uuid import UUID, uuid4

import httpx

from app.config import Settings
from app.diary_schemas import DiaryDetail, DiaryMonth
from scripts.verify_diary_reads import (
    Credentials, DIARY_A, DIARY_B, PROJECT_URL, ReadOnlyTransport, denied, login,
)

STAGES = (
    "실제 Auth 로그인·비익명 authenticated 회원 확인",
    "기존 A/B 계정 제외",
    "실제 GET 오늘 일기 없음 확인",
    "BE 세션 생성·AI HTTP mock",
    "BE 가상 발언·AI HTTP mock",
    "BE 초안 생성·AI HTTP mock",
    "BE 확정·실제 RPC 저장·원본 대화 확인",
    "실제 GET 달력 월별 목록",
    "실제 GET 상세",
    "새 BE 앱 인스턴스 실제 GET 상세",
)


def seoul_today():
    # 현재 Asia/Seoul은 UTC+09:00. Windows의 별도 tzdata 설치 없이 계산합니다.
    return datetime.now(timezone(timedelta(hours=9))).date()


def ai_mock(request):
    """기존 테스트와 같은 HTTP 계약. AI 서버/모델/네트워크를 사용하지 않습니다."""
    if request.method != "POST" or request.url.path not in {"/api/chat", "/api/diary"}:
        raise ValueError("AI 경로 오류")
    if "apikey" in request.headers or "authorization" in request.headers:
        raise ValueError("AI 인증정보 차단")
    if request.url.path == "/api/chat":
        return httpx.Response(200, json={
            "question": "가상 인물의 하루는 어땠나요?", "finished": False,
            "mock": True, "fallback": False,
        })
    sources = [{"id": i, "text": item["text"]}
               for i, item in enumerate(json.loads(request.content)["messages"])
               if item["role"] == "child"]
    text = "\n".join(source["text"] for source in sources)
    return httpx.Response(200, json={
        "text": text, "sources": sources,
        "pages": [{"text": text, "source_ids": [s["id"] for s in sources], "imageUrl": None}],
        "fallback": True, "requires_confirmation": True, "mock": True,
    })


class SingleWriteTransport(ReadOnlyTransport):
    def __init__(self, today, *, inner=None):
        super().__init__(inner=inner)
        self.today = today
        self.owner = None
        self.attempted = False

    async def handle_async_request(self, request):
        if request.method != "POST":
            return await super().handle_async_request(request)
        if (str(request.url) != PROJECT_URL + "/rest/v1/rpc/confirm_member_diary"
                or self.owner is None or self.attempted or seoul_today() != self.today
                or "authorization" in request.headers
                or not request.headers.get("apikey", "").startswith("sb_secret_")):
            raise ValueError("허용 외 쓰기 차단")
        payload = json.loads(request.content)
        confirmed_date = datetime.fromisoformat(payload["p_confirmed_at"]).astimezone(
            timezone(timedelta(hours=9)),
        ).date()
        if payload["p_user_id"] != str(self.owner) or confirmed_date != self.today:
            raise ValueError("저장 대상 오류")
        # 전송 직전 소진: timeout/오류/응답 유실에도 두 번째 쓰기를 허용하지 않습니다.
        self.attempted = True
        return await self.inner.handle_async_request(request)


@dataclass
class Result:
    statuses: dict = field(default_factory=lambda: dict.fromkeys(STAGES, "미실행"))
    diary_id: str | None = None
    rpc_attempted: bool = False
    failed: bool = False

    @property
    def passed(self):
        return not self.failed and all(value == "성공" for value in self.statuses.values())


def require(condition):
    if not condition:
        raise ValueError("검증 조건 불일치")


def month_rows(response, month):
    require(response.status_code == 200)
    data = DiaryMonth.model_validate(response.json(), strict=False)
    require(data.month == month)
    dates = [row.diary_date.isoformat() for row in data.diaries]
    require(all(value.startswith(month + "-") for value in dates)
            and dates == sorted(set(dates))
            and len({row.diary_id for row in data.diaries}) == len(dates))
    return data.diaries


@asynccontextmanager
async def local_be(settings, *, auth_transport=None, db_transport=None):
    from app.main import create_app

    app = create_app(
        settings, ai_transport=httpx.MockTransport(ai_mock),
        auth_transport=ReadOnlyTransport(inner=auth_transport),
        db_transport=db_transport,
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        base_url="http://be.local", transport=httpx.ASGITransport(app=app),
        follow_redirects=False, trust_env=False,
    ) as be:
        yield app, be


async def verify(settings, credentials, *, transport=None):
    """transport 주입은 원격 요청 없는 자동 테스트용입니다."""
    result = Result()
    stage = STAGES[0]
    writer = SingleWriteTransport(seoul_today(), inner=transport)
    try:
        require(settings.supabase_url == PROJECT_URL
                and settings.supabase_publishable_key is not None
                and settings.supabase_secret_key is not None)
        async with local_be(settings, auth_transport=transport, db_transport=writer) as (app, be):
            async with httpx.AsyncClient(
                base_url=PROJECT_URL,
                headers={"apikey": settings.supabase_publishable_key.get_secret_value()},
                transport=ReadOnlyTransport(login=True, inner=transport),
                timeout=settings.auth_request_timeout_seconds,
                follow_redirects=False, trust_env=False,
            ) as auth_http:
                member = await login(auth_http, app.state.auth_client, credentials)
            be.headers["Authorization"] = f"Bearer {member.token}"
            result.statuses[stage] = "성공"

            stage = STAGES[1]
            # 알려진 A/B fixture를 본인으로 읽을 수 있는 계정이면 날짜와 무관하게 차단합니다.
            for diary_id in (DIARY_A, DIARY_B):
                require(denied(await be.get(f"/api/diaries/{diary_id}")))
            result.statuses[stage] = "성공"

            stage = STAGES[2]
            today = writer.today
            month = today.strftime("%Y-%m")
            before = month_rows(await be.get(f"/api/diaries?month={month}"), month)
            require(not any(row.diary_date == today for row in before))
            result.statuses[stage] = "성공"

            stage = STAGES[3]
            start = await be.post("/api/chat-sessions", json={}, headers={
                "Idempotency-Key": f"verify-c-{uuid4().hex}",
            })
            require(start.status_code == 201)
            first = start.json()
            require(first["mock"] is True and first["history_revision"] == 0)
            session_id = first["session_id"]
            path = f"/api/chat-sessions/{session_id}"
            history = [{"position": 0, "role": "assistant", "text": first["reply"]["text"]}]
            result.statuses[stage] = "성공"

            stage = STAGES[4]
            text = "가상 인물은 종이 공으로 놀고 즐거웠다고 했어. 실제 아이 이야기가 아니야."
            reply = await be.post(path + "/messages", json={
                "client_message_id": "verify-turn-1", "text": text,
            })
            require(reply.status_code == 200)
            require(reply.json()["mock"] is True and reply.json()["history_revision"] == 1)
            history.extend([{"position": 1, "role": "child", "text": text},
                            {"position": 2, "role": "assistant", "text": reply.json()["reply"]["text"]}])
            result.statuses[stage] = "성공"

            stage = STAGES[5]
            response = await be.post(path + "/diary-draft", json={
                "client_request_id": "verify-draft-1", "history_revision": 1,
            })
            require(response.status_code == 200)
            draft_response = response.json()
            draft = draft_response["draft"]
            require(draft_response["history_revision"] == 1 and draft["mock"] is True)
            result.statuses[stage] = "성공"

            stage = STAGES[6]
            require(seoul_today() == today)
            writer.owner = member.user_id
            response = await be.post(path + "/diaries", json={
                "client_request_id": f"verify-confirm-{uuid4().hex}",
                "history_revision": 1, "draft_id": draft["draft_id"], "weather": "sunny",
            })
            require(response.status_code == 200 and writer.attempted)
            saved = DiaryDetail.model_validate(response.json(), strict=False).model_dump(mode="json")
            require(saved["diary_date"] == today.isoformat()
                    and saved["confirmed_text"] == draft["text"] and saved["ai_draft"] == draft
                    and saved["messages"] == history and saved["weather"] == "sunny")
            result.diary_id = str(UUID(saved["diary_id"]))
            result.statuses[stage] = "성공"

            stage = STAGES[7]
            after = month_rows(await be.get(f"/api/diaries?month={month}"), month)
            expected = {str(row.diary_id): row.model_dump(mode="json") for row in before}
            expected[result.diary_id] = {
                "diary_id": result.diary_id, "diary_date": today.isoformat(), "weather": "sunny",
                "preview": draft["text"][:120],
            }
            require({str(row.diary_id): row.model_dump(mode="json") for row in after} == expected)
            result.statuses[stage] = "성공"

            stage = STAGES[8]
            detail_path = f"/api/diaries/{result.diary_id}"
            response = await be.get(detail_path)
            require(response.status_code == 200 and response.json() == saved)
            result.statuses[stage] = "성공"

        stage = STAGES[9]
        # 이전 lifespan 종료 후 새 앱/빈 SessionStore/새 HTTP 클라이언트. 쓰기 키도 제거합니다.
        read_settings = settings.model_copy(update={"supabase_secret_key": None})
        async with local_be(read_settings, auth_transport=transport,
                            db_transport=ReadOnlyTransport(inner=transport)) as (restarted, be):
            require(not restarted.state.chat_service.store.sessions)
            response = await be.get(detail_path, headers={"Authorization": f"Bearer {member.token}"})
            require(response.status_code == 200 and response.json() == saved)
            result.statuses[stage] = "성공"
    except (Exception, KeyboardInterrupt):
        result.statuses[stage] = "실패"
        result.failed = True
    finally:
        result.rpc_attempted = writer.attempted
    return result


def main(argv=None):
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        require((sys.argv[1:] if argv is None else argv) == ["--save-member-c"])
        require(sys.stdin.isatty())
        settings = Settings.from_env()
        require(settings.supabase_url == PROJECT_URL
                and settings.supabase_publishable_key is not None
                and settings.supabase_secret_key is not None)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            credentials = Credentials(getpass.getpass("회원 C 이메일(숨김): "),
                                      getpass.getpass("회원 C 비밀번호(숨김): "))
        print("BE: 로컬 ASGI / AI: HTTP mock / Supabase Auth·RPC·GET: 실제 원격")
        result = asyncio.run(verify(settings, credentials))
        for name, status in result.statuses.items():
            print(f"{name}: {status}")
        if result.diary_id:
            print(f"저장 확인 일기 ID: {result.diary_id}")
        if not result.passed and result.rpc_attempted:
            print("RPC 전송 후 실패: 저장됐을 수 있습니다. 추가 저장 없이 조회로 확인하세요.")
        return 0 if result.passed else 1
    except (Exception, KeyboardInterrupt):
        print("실행 준비: 실패 (명시 실행 옵션·설정·숨김 입력 확인)", file=sys.stderr)
        return 1
    finally:
        for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY"):
            os.environ.pop(name, None)
        logging.disable(previous_logging)


if __name__ == "__main__":
    sys.exit(main())
