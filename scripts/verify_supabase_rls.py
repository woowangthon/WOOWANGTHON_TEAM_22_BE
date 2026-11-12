"""가상 회원 A/B 전용 Auth·Data API 조회 검증. 실제 실행은 사용자가 담당합니다."""

import argparse
from contextlib import ExitStack
from dataclasses import dataclass, field
import getpass
import logging
import os
import re
import sys
from uuid import UUID
import warnings

import httpx


PROJECT_URL = "https://nnbmxtiouvsuszretahg.supabase.co"
TABLES = ("diaries", "diary_messages", "diary_assets")
SAMPLE_LIMIT = 100


class VerificationError(Exception):
    """외부 응답/입력 원문 없이 사용자에게 보여 줄 수 있는 오류입니다."""


@dataclass(frozen=True)
class Config:
    publishable_key: str = field(repr=False)
    url: str = PROJECT_URL

    def __post_init__(self):
        # 비밀번호를 다른 호스트에 보내거나 관리자 키로 RLS를 우회하지 않습니다.
        if self.url != PROJECT_URL:
            raise VerificationError("SUPABASE_URL은 지정된 today-and-near 주소여야 합니다.")
        if not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]+", self.publishable_key):
            raise VerificationError("SUPABASE_PUBLISHABLE_KEY에 Publishable key만 지정하세요.")


@dataclass(frozen=True)
class Member:
    user_id: str
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class TableResult:
    table: str
    rows_a: int
    rows_b: int
    isolation_confirmed: bool


@dataclass(frozen=True)
class Report:
    tables: tuple[TableResult, ...]
    fixture_confirmed: bool = False

    @property
    def complete(self):
        return all(item.isolation_confirmed for item in self.tables)


def uuid_value(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except ValueError:
        raise VerificationError("사용자 또는 가상 일기 UUID 형식이 올바르지 않습니다.") from None


def json_value(response):
    try:
        return response.json()
    except ValueError:
        raise VerificationError("서버 응답이 올바른 JSON이 아닙니다.") from None


class Probe:
    def __init__(self, config, transport=None):
        self.config = config
        self.client = httpx.Client(
            timeout=10, trust_env=False, follow_redirects=False, transport=transport,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def request(self, method, path, member=None, **kwargs):
        headers = {"apikey": self.config.publishable_key}
        if member is not None:
            headers["Authorization"] = f"Bearer {member.access_token}"
        try:
            return self.client.request(
                method, self.config.url + path, headers=headers, **kwargs,
            )
        except httpx.HTTPError:
            # 예외에는 요청 헤더/본문이 포함될 수 있어 원문을 출력하지 않습니다.
            raise VerificationError("네트워크 요청에 실패했습니다. 자동 재시도하지 않습니다.") from None

    def login(self, email, password):
        if not email.strip() or "@" not in email or not password:
            raise VerificationError("테스트 회원 이메일과 비밀번호를 모두 입력하세요.")
        response = self.request(
            "POST", "/auth/v1/token", params={"grant_type": "password"},
            json={"email": email.strip(), "password": password},
        )
        if response.status_code != 200:
            raise VerificationError("로그인 실패: 인증 정보·이메일 확인·Auth 제한을 확인하세요.")
        data = json_value(response)
        if not isinstance(data, dict):
            raise VerificationError("로그인 응답 형식이 올바르지 않습니다.")
        token = data.get("access_token")
        token_type = data.get("token_type")
        if (not isinstance(token_type, str) or token_type.lower() != "bearer"
                or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", token)):
            raise VerificationError("로그인 응답의 액세스 토큰 형식이 올바르지 않습니다.")
        user_id = self.member_id(data.get("user"))
        member = Member(user_id, token)
        # 단순 JWT 디코딩 대신 Auth 서버에 토큰의 실제 회원 정보를 다시 확인합니다.
        verified = self.request("GET", "/auth/v1/user", member)
        if verified.status_code != 200 or self.member_id(json_value(verified)) != user_id:
            raise VerificationError("Auth 서버의 회원 확인에 실패했습니다.")
        return member

    @staticmethod
    def member_id(user):
        if not isinstance(user, dict) or user.get("is_anonymous") is not False:
            raise VerificationError("비익명 테스트 회원만 사용할 수 있습니다.")
        return uuid_value(user.get("id"))

    def select(self, table, member, **filters):
        response = self.request(
            "GET", f"/rest/v1/{table}", member,
            params={"select": "id,user_id", "limit": str(SAMPLE_LIMIT), **filters},
        )
        if response.status_code != 200:
            raise VerificationError(f"{table}: 회원 Data API 조회에 실패했습니다.")
        rows = json_value(response)
        if not isinstance(rows, list):
            raise VerificationError(f"{table}: 조회 응답이 행 목록이 아닙니다.")
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"id", "user_id"}
                    or uuid_value(row["user_id"]) != member.user_id):
                raise VerificationError(f"{table}: 다른 회원 데이터 또는 잘못된 응답을 발견했습니다.")
            uuid_value(row["id"])
        return rows

    def check_unauthenticated(self, table):
        response = self.request(
            "GET", f"/rest/v1/{table}",
            params={"select": "id,user_id", "limit": "1"},
        )
        # 게이트웨이의 잘못된 키 401을 DB 권한 거절로 오인하지 않습니다.
        if response.status_code not in (401, 403):
            raise VerificationError(f"{table}: 미인증 DB 접근의 명시적 거절을 확인하지 못했습니다.")
        body = json_value(response)
        if not isinstance(body, dict) or body.get("code") != "42501":
            raise VerificationError(f"{table}: DB 권한 거절 근거를 확인하지 못했습니다.")


def check_fixture(probe_a, probe_b, member_a, member_b, fixture_ids):
    diary_a, diary_b = map(uuid_value, fixture_ids)
    if diary_a == diary_b:
        raise VerificationError("가상 일기 A/B UUID는 서로 달라야 합니다.")
    for own_probe, other_probe, own, other, diary_id in (
        (probe_a, probe_b, member_a, member_b, diary_a),
        (probe_b, probe_a, member_b, member_a, diary_b),
    ):
        own_rows = own_probe.select("diaries", own, id=f"eq.{diary_id}")
        if len(own_rows) != 1 or uuid_value(own_rows[0]["id"]) != diary_id:
            raise VerificationError("가상 일기 본인 조회를 확인하지 못해 교차 검증을 완료할 수 없습니다.")
        if other_probe.select("diaries", other, id=f"eq.{diary_id}"):
            raise VerificationError("다른 회원의 가상 일기가 조회되었습니다.")


def verify(config, email_a, password_a, email_b, password_b, *, transport=None, fixture_ids=None):
    if email_a.strip().casefold() == email_b.strip().casefold():
        raise VerificationError("테스트 회원 A/B 이메일은 서로 달라야 합니다.")
    with ExitStack() as stack:
        # 회원별 client/토큰을 분리하고 미인증 client에는 Authorization을 넣지 않습니다.
        probe_a, probe_b, anonymous = [
            stack.enter_context(Probe(config, transport)) for _ in range(3)
        ]
        member_a = probe_a.login(email_a, password_a)
        member_b = probe_b.login(email_b, password_b)
        if member_a.user_id == member_b.user_id or member_a.access_token == member_b.access_token:
            raise VerificationError("A/B 사용자 ID 또는 액세스 토큰이 동일합니다.")
        results = []
        for table in TABLES:
            # 소유자 필터 없는 표본으로 유출을 확인하고, 상대 회원 필터도 양방향 검사합니다.
            rows_a = probe_a.select(table, member_a)
            rows_b = probe_b.select(table, member_b)
            for probe, member, other in (
                (probe_a, member_a, member_b), (probe_b, member_b, member_a),
            ):
                if probe.select(table, member, user_id=f"eq.{other.user_id}"):
                    raise VerificationError(f"{table}: 다른 회원 조회 결과가 비어 있지 않습니다.")
            anonymous.check_unauthenticated(table)
            # 빈 결과만으로는 데이터 부재와 RLS 격리를 구분할 수 없습니다.
            results.append(TableResult(table, len(rows_a), len(rows_b), bool(rows_a and rows_b)))
        if fixture_ids is not None:
            check_fixture(probe_a, probe_b, member_a, member_b, fixture_ids)
        return Report(tuple(results), fixture_ids is not None)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise VerificationError("실행 옵션이 올바르지 않습니다. --help를 확인하세요.")


def main(argv=None):
    previous_logging = logging.root.manager.disable
    # HTTP 디버그 설정이 있어도 인증 요청/응답이 로그에 남지 않게 합니다.
    logging.disable(logging.CRITICAL)
    try:
        parser = SafeParser(description=__doc__)
        parser.add_argument("--diary-a-id", type=uuid_value, help="별도 준비한 가상 A 일기 UUID")
        parser.add_argument("--diary-b-id", type=uuid_value, help="별도 준비한 가상 B 일기 UUID")
        args = parser.parse_args(argv)
        if bool(args.diary_a_id) != bool(args.diary_b_id):
            raise VerificationError("가상 일기 A/B UUID 옵션을 함께 지정하세요.")
        config = Config(os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""),
                        os.environ.get("SUPABASE_URL", PROJECT_URL))
        if not sys.stdin.isatty():
            raise VerificationError("비밀번호를 숨길 수 있는 대화형 터미널에서 실행하세요.")
        print("가상 테스트 회원만 입력하세요. 비밀번호와 토큰은 저장하거나 출력하지 않습니다.")
        with warnings.catch_warnings():
            # getpass의 평문 입력 fallback을 금지합니다.
            warnings.simplefilter("error", getpass.GetPassWarning)
            email_a = input("회원 A 이메일: ").strip()
            password_a = getpass.getpass("회원 A 비밀번호(숨김): ")
            email_b = input("회원 B 이메일: ").strip()
            password_b = getpass.getpass("회원 B 비밀번호(숨김): ")
        fixture_ids = (args.diary_a_id, args.diary_b_id) if args.diary_a_id else None
        report = verify(config, email_a, password_a, email_b, password_b, fixture_ids=fixture_ids)
        print("A/B 비익명 로그인·서로 다른 사용자 ID·회원별 JWT 조회 확인.")
        for item in report.tables:
            status = "표본 교차 조회 확인" if item.isolation_confirmed else "격리 미완료(본인 데이터 부족)"
            print(f"{item.table}: A {item.rows_a}행 / B {item.rows_b}행, 미인증 DB 거절 확인, {status}")
        if report.fixture_confirmed:
            print("지정한 가상 일기 A/B: 본인 조회·상대 ID 교차 조회 확인.")
        print("각 조회는 최대 100행 표본입니다. 쓰기·Storage·Auth 익명 회원은 검증하지 않았습니다.")
        return 0 if report.complete else 2
    except VerificationError as error:
        print(f"검증 중단: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("안전한 입력을 완료하지 못해 검증을 중단했습니다.", file=sys.stderr)
        return 1
    except Exception:
        # 예상 밖 예외도 traceback/외부 응답/비밀번호를 노출하지 않습니다.
        print("예상하지 못한 오류로 검증을 중단했습니다. 민감정보는 출력하지 않습니다.", file=sys.stderr)
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    sys.exit(main())
