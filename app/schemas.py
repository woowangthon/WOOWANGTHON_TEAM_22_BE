from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

MAX_MESSAGE_LENGTH = 2000
MAX_AI_REPLY_LENGTH = 150
MAX_HISTORY_MESSAGES = 60
MAX_HISTORY_LENGTH = 20000
RequestKey = Annotated[str, StringConstraints(
    strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$",
)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def validate_text(value: str) -> str:
    if not value.strip() or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("공백뿐인 입력과 잘못된 Unicode는 허용하지 않습니다.")
    return value


class StartSessionRequest(StrictModel):
    pass


class MessageRequest(StrictModel):
    client_message_id: RequestKey
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)

    _validate_text = field_validator("text")(validate_text)


class AIMessage(StrictModel):
    role: Literal["child", "assistant"]
    text: str


class AIReply(StrictModel):
    question: str = Field(min_length=1, max_length=MAX_AI_REPLY_LENGTH)
    finished: bool
    mock: bool
    fallback: bool

    _validate_question = field_validator("question")(validate_text)


class Reply(StrictModel):
    message_id: str
    role: Literal["assistant"] = "assistant"
    text: str


class SessionResponse(StrictModel):
    session_id: str
    reply: Reply
    finished: bool
    mock: bool
    fallback: bool


class MessageResponse(SessionResponse):
    client_message_id: str
