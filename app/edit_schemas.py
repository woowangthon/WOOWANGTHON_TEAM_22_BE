from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.drawing_schemas import DrawingAsset
from app.schemas import RequestKey, StrictModel


class EditRequest(StrictModel):
    client_request_id: RequestKey
    expected_version: int = Field(ge=1, le=2147483646)
    confirmed_text: str = Field(min_length=1, max_length=20000)

    @field_validator("confirmed_text")
    @classmethod
    def nonempty(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("일기 내용을 입력해 주세요.")
        return value


class EditResponse(StrictModel):
    diary_id: UUID
    edit_version: int = Field(ge=2)
    confirmed_text: str
    updated_at: datetime
    original_drawing: DrawingAsset | None
