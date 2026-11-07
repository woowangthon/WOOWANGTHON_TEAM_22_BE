from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(code)


def error_response(request: Request, error: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", f"req_{uuid4().hex}")
    return JSONResponse(
        status_code=error.status,
        content={"error": {"code": error.code, "message": error.message,
                           "retryable": error.retryable}, "request_id": request_id},
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store",
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
