"""생성 작업 기록. 이미지 바이트와 회원 JWT는 DB 작업 행에 보관하지 않는다."""
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5
from hashlib import sha256
import json

from pydantic import Field, model_validator

from app.comic_schemas import ComicAsset, ComicResponse, Digest, VisualPlan
from app.schemas import RequestKey, StrictModel


class ComicCheckpoint(StrictModel):
    state: Literal['waiting', 'dispatched', 'checkpoint', 'ready', 'failed', 'rejected', 'mock', 'unknown']
    asset: ComicAsset | None = None

    @model_validator(mode='after')
    def asset_state(self):
        if ((self.state in {'checkpoint', 'ready'} and self.asset is None)
                or (self.state not in {'checkpoint', 'ready', 'unknown'} and self.asset is not None)):
            raise ValueError('체크포인트 자산 상태 불일치')
        return self


class ComicJob(StrictModel):
    preview_id: UUID
    user_id: UUID
    session_id: str
    client_request_id: RequestKey
    history_revision: int = Field(ge=0)
    draft_id: str
    draft_text: str
    confirmed_plan: VisualPlan
    fingerprint: Digest
    progress: dict[str, ComicCheckpoint]
    state: Literal['queued', 'running', 'completed', 'unknown']
    claim_token: UUID | None
    lease_until: datetime | None
    result_saved: bool
    created_at: datetime

    @model_validator(mode='after')
    def plan_and_progress(self):
        from app.clients.comic_ai import validate_plan
        validate_plan(self.confirmed_plan, self.draft_text)
        body = dict(client_request_id=self.client_request_id, history_revision=self.history_revision,
                    draft_id=self.draft_id, input_confirmed=True, scene_confirmed=True,
                    confirmed_plan=self.confirmed_plan.model_dump(mode='json'))
        digest = sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if self.fingerprint != digest or self.preview_id != uuid5(
                NAMESPACE_URL, f'comic/{self.user_id}/{self.session_id}/{self.client_request_id}'):
            raise ValueError('작업 원문/확인 지문 불일치')
        if set(self.progress) != {s.scene_id for s in self.confirmed_plan.comic4.scenes if not s.repeated}:
            raise ValueError('작업 컷 불일치')
        if self.state == 'queued' and (self.claim_token is not None or self.lease_until is not None):
            raise ValueError('접수 상태 오류')
        if self.state == 'running' and (self.claim_token is None or self.lease_until is None):
            raise ValueError('실행 소유권 누락')
        if self.result_saved and any(p.state in {'waiting', 'dispatched', 'checkpoint'} for p in self.progress.values()):
            raise ValueError('미완료 컷')
        for scene, checkpoint in self.progress.items():
            if checkpoint.asset and checkpoint.asset.asset_id != uuid5(self.preview_id, scene + checkpoint.asset.sha256):
                raise ValueError('체크포인트 식별자 불일치')
        return self


class ComicJobResponse(ComicResponse):
    client_request_id: RequestKey
    job_state: Literal['queued', 'running', 'completed', 'unknown']
    receipt_found: bool
    worker_available: bool
    # 미발견/리스 만료도 새 생성 허가가 아니다. 항상 원래 키로 GET한다.
    retry_generation_allowed: Literal[False] = False
