"""잠정 FE 계약과 기존 AI 시각화 계약. 기존 일기 DTO와 출처 번호를 공유하지 않는다."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas import RequestKey, StrictModel

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SourceId = Annotated[int, Field(ge=0, le=79)]
PanelOutcome = Literal["pending", "ready", "failed", "unknown", "rejected", "mock"]


class ComicPreviewRequest(StrictModel):
    client_request_id: RequestKey
    history_revision: int = Field(ge=0)
    draft_id: str = Field(min_length=1, max_length=128)
    input_confirmed: bool = False
    scene_confirmed: bool = False
    confirmed_plan: "VisualPlan | None" = None


class ComicPlanRequest(StrictModel):
    history_revision: int = Field(ge=0)
    draft_id: str = Field(min_length=1, max_length=128)
    input_confirmed: bool = False


class ComicAttachRequest(StrictModel):
    client_request_id: RequestKey
    preview_id: UUID = Field(strict=False)


class VisualSource(StrictModel):
    id: SourceId
    text: str = Field(min_length=1, max_length=6000)


class VisualScene(StrictModel):
    scene_id: str = Field(min_length=1, max_length=128)
    order: int = Field(ge=1, le=4)
    caption: str = Field(min_length=1, max_length=6000)
    source_ids: list[SourceId] = Field(min_length=1, max_length=80)
    imageUrl: None = None
    repeated: bool = False
    reuse_scene_id: str | None = None


class VisualDocument(StrictModel):
    format: Literal["comic4"]
    layout: Literal["grid_2x2"]
    planning_mode: Literal["model", "deterministic", "fallback", "mock"]
    scenes: list[VisualScene] = Field(min_length=4, max_length=4)
    notices: list[str] = Field(max_length=20)


class VisualPlan(StrictModel):
    diary_text: str = Field(min_length=1, max_length=6000)
    diary_hash: Digest
    sources: list[VisualSource] = Field(min_length=1, max_length=80)
    storybook: None = None
    comic4: VisualDocument
    requires_confirmation: Literal[True]
    mock: bool


class VisualImage(StrictModel):
    diary_hash: Digest
    scene_key: Digest
    caption: str = Field(min_length=1, max_length=6000)
    source_ids: list[SourceId] = Field(min_length=1, max_length=80)
    format: Literal["comic4"]
    imageUrl: str | None
    status: Literal["generated", "rejected", "failed", "mock"]
    error: str | None = Field(default=None, max_length=6000)
    requires_confirmation: Literal[True]
    mock: bool


class ComicAsset(StrictModel):
    asset_id: UUID
    mime_type: Literal["image/png"]
    byte_size: int = Field(ge=1, le=10485760)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    sha256: Digest

    @model_validator(mode="after")
    def pixels(self):
        if self.width * self.height > 16_000_000:
            raise ValueError("이미지 픽셀 제한 초과")
        return self


class StoredPanel(StrictModel):
    position: int = Field(ge=1, le=4)
    caption: str = Field(min_length=1, max_length=6000)
    source_ids: list[SourceId] = Field(min_length=1, max_length=80)
    outcome: PanelOutcome
    asset: ComicAsset | None = None

    @model_validator(mode="after")
    def asset_state(self):
        if (self.outcome in {"pending", "ready"}) != (self.asset is not None):
            raise ValueError("자산과 처리 상태 불일치")
        return self


class StoredPreview(StrictModel):
    preview_id: UUID
    user_id: UUID
    session_id: str
    client_request_id: RequestKey
    history_revision: int = Field(ge=0)
    draft_id: str
    draft_text: str = Field(min_length=1, max_length=6000)
    panels: list[StoredPanel] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def ordered(self):
        if [p.position for p in self.panels] != [1, 2, 3, 4]:
            raise ValueError("컷 순서 오류")
        assets = {}
        for p in self.panels:
            if p.asset is not None:
                previous = assets.setdefault(p.asset.asset_id, (p.asset, p.outcome))
                if previous != (p.asset, p.outcome):
                    raise ValueError("반복 자산 불일치")
        return self


class ComicPanel(StrictModel):
    position: int
    caption: str
    status: PanelOutcome
    imageUrl: str | None = None
    expires_at: datetime | None = None


class ComicResponse(StrictModel):
    preview_id: UUID | None = None
    status: Literal["none", "queued", "running", "ready", "partial", "failed"]
    panels: list[ComicPanel] = Field(default_factory=list)
    recovery_required: bool = False
    error_code: Literal["COMIC_RESULT_UNKNOWN", "COMIC_STORAGE_RECOVERY_REQUIRED"] | None = None


class DiaryComicResponse(ComicResponse):
    current_edit_version: int = Field(ge=1)
    source_edit_version: int | None = Field(ge=1)
    content_status: Literal["none", "current", "stale"]
