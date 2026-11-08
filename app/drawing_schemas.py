from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas import StrictModel


class DrawingAsset(StrictModel):
    asset_id: UUID
    asset_kind: Literal["original_drawing"] = "original_drawing"
    mime_type: Literal["image/jpeg", "image/png"]
    byte_size: int = Field(ge=1, le=10485760)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    imageUrl: str
    expires_at: datetime


class DrawingResponse(StrictModel):
    diary_id: UUID
    asset: DrawingAsset
