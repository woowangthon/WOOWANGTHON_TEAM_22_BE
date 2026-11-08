from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from app.schemas import DiaryDraft, RequestKey, StrictModel
from app.drawing_schemas import DrawingAsset

Weather = Literal["sunny", "cloudy", "snowy", "rainy"]


class ConfirmDiaryRequest(StrictModel):
    client_request_id: RequestKey
    history_revision: int = Field(ge=1)
    draft_id: Annotated[str, StringConstraints(pattern=r"^draft_[0-9a-f]{32}$")]
    weather: Weather | None = None


class SavedMessage(StrictModel):
    position: int
    role: Literal["child", "assistant"]
    text: str


class DiaryDetail(StrictModel):
    diary_id: UUID
    diary_date: date
    weather: Weather | None
    confirmed_text: str
    confirmed_at: datetime
    ai_draft: DiaryDraft
    messages: list[SavedMessage]
    imageUrl: str | None = None
    assets: list[DrawingAsset] = Field(default_factory=list)


class DiarySummary(StrictModel):
    diary_id: UUID
    diary_date: date
    weather: Weather | None
    preview: str


class DiaryMonth(StrictModel):
    month: str
    diaries: list[DiarySummary]
