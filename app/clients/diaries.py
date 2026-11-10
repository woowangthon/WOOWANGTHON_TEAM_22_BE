import asyncio
from calendar import monthrange
from datetime import date
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.diary_schemas import DiaryDetail, DiaryMonth, DiarySummary, SavedMessage
from app.errors import AppError

DETAIL_COLUMNS = "id,diary_date,weather,confirmed_text,confirmed_at,ai_draft,edit_version,updated_at,deleted_at,active_drawing_edit_id"


def storage_unavailable() -> AppError:
    return AppError(503, "DIARY_STORAGE_UNAVAILABLE", "저장소 연결 실패", True)


def invalid_response() -> AppError:
    return AppError(502, "DIARY_STORAGE_INVALID_RESPONSE", "저장소 응답 오류")


class DiaryRepository:
    def __init__(self, reader: httpx.AsyncClient | None,
                 writer: httpx.AsyncClient | None, timeout_seconds: float):
        self.reader = reader
        self.writer = writer
        self.timeout_seconds = timeout_seconds
        self.drawings = None

    async def _request(self, method: str, path: str, token: str | None = None,
                       **kwargs):
        http = self.writer if method == "POST" else self.reader
        if http is None:
            raise AppError(503, "DIARY_STORAGE_NOT_CONFIGURED", "저장소 설정 누락")
        # 회원 JWT 읽기와 서버 전용 쓰기 분리
        headers = {"Authorization": token} if method == "GET" else {}
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await http.request(
                    method, path, headers=headers, timeout=self.timeout_seconds, **kwargs,
                )
        except (httpx.HTTPError, TimeoutError):
            raise storage_unavailable() from None
        if method == "GET" and response.status_code in {401, 403}:
            raise AppError(401, "AUTHENTICATION_FAILED", "인증 실패")
        if not 200 <= response.status_code < 300:
            code = None
            details = None
            try:
                data = response.json()
                if isinstance(data, dict):
                    code = data.get("code")
                    details = data.get("details")
            except ValueError:
                pass
            if method == "POST" and response.status_code == 409 and code == "PT409":
                if details == "DIARY_EDIT_VERSION_CONFLICT":
                    raise AppError(409, "DIARY_EDIT_VERSION_CONFLICT", "수정 API로 현재 버전을 저장해 주세요.")
                if details == "DRAWING_CONFLICT":
                    raise AppError(409, "DRAWING_ALREADY_EXISTS", "이미 저장된 그림")
                # 요청 충돌 처리
                if details == "IDEMPOTENCY_CONFLICT":
                    raise AppError(409, "IDEMPOTENCY_CONFLICT", "요청 내용 불일치")
                # 날짜 중복 확인
                if details == "DIARY_DATE_CONFLICT":
                    raise AppError(409, "DIARY_DATE_CONFLICT", "날짜 중복")
            if method == "POST" and response.status_code == 404 and code == "PT404" and details == "DIARY_NOT_FOUND":
                raise AppError(404, "DIARY_NOT_FOUND", "일기 없음")
            if response.status_code == 429 or response.status_code >= 500:
                raise storage_unavailable()
            raise AppError(503, "DIARY_STORAGE_FAILED", "저장소 처리 실패")
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError:
            raise invalid_response() from None

    async def _rows(self, path: str, token: str, params):
        data = await self._request("GET", path, token, params=params)
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise invalid_response()
        return data

    async def find_request(self, owner_id: UUID, token: str, key: str):
        rows = await self._rows("/rest/v1/diaries", token, {
            "select": "id,confirmation_fingerprint",
            "user_id": f"eq.{owner_id}", "source_client_request_id": f"eq.{key}",
            "confirmation_fingerprint": "not.is.null", "limit": "2",
        })
        if len(rows) > 1:
            raise invalid_response()
        if not rows:
            return None
        row = rows[0]
        try:
            return UUID(row["id"]), str(row["confirmation_fingerprint"])
        except (ValueError, TypeError, KeyError):
            raise invalid_response() from None

    async def save(self, payload: dict) -> UUID:
        data = await self._request("POST", "/rest/v1/rpc/confirm_member_diary", json=payload)
        try:
            return UUID(data)
        except (ValueError, TypeError, AttributeError):
            raise invalid_response() from None

    async def owned_diary(self, owner_id: UUID, token: str, diary_id: UUID):
        params = {"user_id": f"eq.{owner_id}", "id": f"eq.{diary_id}",
                  "select": DETAIL_COLUMNS, "limit": "2"}
        rows = await self._rows("/rest/v1/diaries", token, params)
        if not rows:
            raise AppError(404, "DIARY_NOT_FOUND", "일기 없음")
        if len(rows) != 1:
            raise invalid_response()
        if rows[0].get("id") != str(diary_id):
            raise invalid_response()
        return rows[0]

    async def detail(self, owner_id: UUID, token: str, diary_id: UUID) -> DiaryDetail:
        row = await self.owned_diary(owner_id, token, diary_id)
        messages = await self._rows("/rest/v1/diary_messages", token, {
            "user_id": f"eq.{owner_id}", "diary_id": f"eq.{diary_id}",
            "select": "position,role,text", "order": "position.asc", "limit": "60",
        })
        try:
            assets = (await self.drawings.edited_assets(owner_id, token, diary_id, row["active_drawing_edit_id"])
                      if self.drawings is not None and row.get("active_drawing_edit_id")
                      else await self.drawings.assets(owner_id, token, diary_id)
                      if self.drawings is not None else [])
            return DiaryDetail.model_validate({
                "diary_id": row["id"],
                **{key: row[key] for key in (
                    "diary_date", "weather", "confirmed_text", "confirmed_at", "ai_draft",
                )},
                "edit_version": row["edit_version"], "updated_at": row["updated_at"],
                "messages": [SavedMessage.model_validate(item) for item in messages],
                "assets": assets, "imageUrl": assets[0].imageUrl if assets else None,
            }, strict=False)
        except (ValidationError, KeyError, TypeError):
            raise invalid_response() from None

    async def month(self, owner_id: UUID, token: str, month: str) -> DiaryMonth:
        try:
            start = date.fromisoformat(month + "-01")
            end = date(start.year, start.month, monthrange(start.year, start.month)[1])
        except ValueError:
            raise AppError(422, "VALIDATION_ERROR", "날짜 형식 오류") from None
        # 월별 일기 조회
        rows = await self._rows("/rest/v1/diaries", token, [
            ("select", "id,diary_date,weather,confirmed_text"),
            ("user_id", f"eq.{owner_id}"), ("diary_date", f"gte.{start}"),
            ("diary_date", f"lte.{end}"), ("order", "diary_date.asc"), ("limit", "31"),
        ])
        try:
            return DiaryMonth(month=month, diaries=[DiarySummary.model_validate({
                "diary_id": row["id"], "diary_date": row["diary_date"],
                "weather": row["weather"], "preview": row["confirmed_text"][:120],
            }, strict=False) for row in rows])
        except (ValidationError, KeyError, TypeError):
            raise invalid_response() from None
