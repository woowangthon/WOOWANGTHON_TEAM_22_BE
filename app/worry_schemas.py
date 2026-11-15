"""고민 대화의 신규 제안 계약. 일기 세션 DTO와 분리한다."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas import StrictModel, RequestKey, MessageRequest, validate_text

NOTICE_VERSION = "worry-v1-proposal"
NOTICE = ("대화는 시작한 때부터 7일간 보관한 뒤 삭제해요. 로그인한 본인만 볼 수 있어요. "
          "보호자에게 자동으로 알리거나 대화를 공유하지 않아요. "
          "답변을 위해 대화 내용이 AI 서비스에 전달돼요. 이름이나 연락처는 적지 않아도 돼요.")
AIStatus = Literal["opening", "reply", "needs_adult", "unavailable", "finished"]


class WorryStart(StrictModel):
    notice_version: Literal["worry-v1-proposal"]


class WorryMessageRequest(MessageRequest):
    @field_validator("text")
    @classmethod
    def postgres_text(cls, value):
        if "\x00" in value:
            raise ValueError("NUL 문자는 저장할 수 없습니다.")
        return value


class WorryAIReply(StrictModel):
    status: AIStatus
    message: str = Field(min_length=1, max_length=2000)
    mock: bool

    _text = field_validator("message")(validate_text)


class WorryMessage(StrictModel):
    session_id: UUID
    position: int = Field(ge=0)
    role: Literal["child", "assistant"]
    text: str = Field(min_length=1, max_length=2000)
    created_at: datetime


class WorryRequestState(StrictModel):
    id: UUID
    kind: Literal["opening", "message"]
    client_message_id: RequestKey | None
    status: Literal["accepted", "dispatched", "completed", "unavailable", "rejected", "unknown"]
    error_code: str | None
    mock: bool | None
    created_at: datetime


class WorrySummary(StrictModel):
    id: UUID
    state: Literal["active", "finished"]
    created_at: datetime
    expires_at: datetime
    last_ai_status: AIStatus | None


class WorrySession(WorrySummary):
    messages: list[WorryMessage]
    requests: list[WorryRequestState]


class WorryList(StrictModel):
    sessions: list[WorrySummary]
    limit: int
    offset: int
