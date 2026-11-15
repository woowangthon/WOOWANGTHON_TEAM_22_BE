from uuid import uuid4
from datetime import datetime
from math import ceil
import re
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False,
                 *, retry_after: str | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self._retry_deadline = None
        self._retry_date = None
        # 공급자 본문/임의 헤더는 전달하지 않고 검증된 대기 정보만 허용합니다.
        if status == 503 and code == "AI_UNAVAILABLE" and isinstance(retry_after, str):
            if re.fullmatch(r"[0-9]{1,6}", retry_after):
                self._retry_deadline = monotonic() + int(retry_after)
            elif re.fullmatch(r"[A-Z][a-z]{2}, [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT", retry_after):
                try:
                    datetime.strptime(retry_after, "%a, %d %b %Y %H:%M:%S GMT")
                except ValueError:
                    pass
                else:
                    self._retry_date = retry_after
        super().__init__(code)

    @property
    def retry_after(self) -> str | None:
        # 같은 실패의 메모리 재응답이 대기 시간을 새로 시작하지 않게 합니다.
        if self._retry_deadline is not None:
            return str(max(0, ceil(self._retry_deadline - monotonic())))
        return self._retry_date


def error_response(request: Request, error: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", f"req_{uuid4().hex}")
    return JSONResponse(
        status_code=error.status,
        content={"error": {"code": error.code, "message": error.message,
                           "retryable": error.retryable}, "request_id": request_id},
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store",
                 **({"Retry-After": error.retry_after} if error.retry_after is not None else {}),
                 **({"WWW-Authenticate": "Bearer"} if error.status == 401 else {})},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, error: AppError):
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # 입력 전문과 Pydantic 내부 오류를 FE에 반환하지 않습니다.
        return error_response(request, AppError(
            422, "VALIDATION_ERROR", (
                "요청 형식 오류" if (request.url.path.startswith("/api/diaries")
                                    or request.url.path.endswith("/diaries"))
                else "요청 형식과 입력 한도를 확인해 주세요."
            ),
        ))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return error_response(request, AppError(
            error.status_code, "HTTP_ERROR", "요청 경로와 방법을 확인해 주세요.",
        ))

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = f"req_{uuid4().hex}"
        try:
            response = await call_next(request)
        except Exception:
            # 공급자 오류/본문/비밀값이 기본 오류 로그에 섞이지 않게 공통화합니다.
            response = error_response(request, AppError(
                500, "INTERNAL_ERROR", "서버에서 요청을 처리하지 못했어요.",
            ))
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response
