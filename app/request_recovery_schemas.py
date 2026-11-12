from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas import RequestKey, StrictModel

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class EditLookupQuery(StrictModel):
    fingerprint: Fingerprint
    expected_version: int = Field(ge=1, le=2147483646, strict=False)


class EditLookupResponse(StrictModel):
    diary_id: UUID
    client_request_id: RequestKey
    fingerprint: Fingerprint
    expected_version: int
    status: Literal["completed", "unknown"]
    edit_version: int | None = Field(ge=2)


class WaterLookupResponse(StrictModel):
    client_request_id: RequestKey
    status: Literal["completed", "unknown"]
    watered_on: date | None


class EditReceiptResult(StrictModel):
    diary_id: UUID
    edit_version: int = Field(ge=2, le=2147483647)
    confirmed_text: str
    updated_at: AwareDatetime
    original_drawing: dict | None


class EditReceipt(StrictModel):
    request_id: UUID
    user_id: UUID
    diary_id: UUID
    client_request_id: RequestKey
    fingerprint: Fingerprint
    expected_version: int = Field(ge=1, le=2147483646)
    confirmed_text: str = Field(min_length=1, max_length=20000)
    state: Literal["pending", "completed"]
    result: EditReceiptResult | None
    drawing: dict | None


class WaterReceipt(StrictModel):
    member_id: UUID
    client_request_id: RequestKey
    watered_on: date
