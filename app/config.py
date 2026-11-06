import os
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    ai_base_url: str = "http://127.0.0.1:8002"
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5500", "http://localhost:5500",
    )
    ai_connect_timeout_seconds: float = Field(default=3, gt=0, allow_inf_nan=False)
    ai_read_timeout_seconds: float = Field(default=130, gt=0, allow_inf_nan=False)
    ai_write_timeout_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
    ai_pool_timeout_seconds: float = Field(default=3, gt=0, allow_inf_nan=False)
    ai_request_timeout_seconds: float = Field(default=135, gt=0, allow_inf_nan=False)

    @field_validator("ai_base_url")
    @classmethod
    def validate_ai_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("AI_BASE_URL에는 인증정보 없는 HTTP(S) origin을 지정하세요.")
        # 잘못된 포트도 서버 시작 시 발견합니다.
        _ = url.port
        return value.rstrip("/")

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            cls.validate_ai_url(value)
            if value.endswith("/"):
                raise ValueError("CORS origin 끝에는 /를 붙이지 마세요.")
        return values

    @classmethod
    def from_env(cls) -> "Settings":
        values = {}
        for name in cls.model_fields:
            raw = os.environ.get(name.upper())
            if raw is not None:
                values[name] = (
                    tuple(item.strip() for item in raw.split(",") if item.strip())
                    if name == "allowed_origins" else raw
                )
        return cls(**values)
