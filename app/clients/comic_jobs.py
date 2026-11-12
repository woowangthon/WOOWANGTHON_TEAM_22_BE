import asyncio
import json

import httpx
from pydantic import ValidationError

from app.comic_job_schemas import ComicJob
from app.errors import AppError


class ComicJobRepository:
    """서버 전용 RPC + 읽기 전용 SELECT. claim token은 회원 응답에 노출하지 않는다."""
    def __init__(self, writer, timeout_seconds):
        self.http, self.timeout = writer, timeout_seconds

    async def request(self, method, path, **kwargs):
        if self.http is None:
            raise AppError(503, 'COMIC_JOBS_UNAVAILABLE', '만화 작업 저장소 설정 필요')
        try:
            async with asyncio.timeout(self.timeout):
                r = await self.http.request(method, path, **kwargs)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise AppError(503, 'COMIC_JOBS_UNAVAILABLE', '만화 작업 상태 확인 필요') from None

    @staticmethod
    def decode(data):
        try:
            return ComicJob.model_validate_json(json.dumps(data))
        except (ValidationError, ValueError, TypeError):
            raise AppError(502, 'COMIC_JOB_INVALID_RESPONSE', '만화 작업 기록 오류') from None

    async def command(self, action, **args):
        data = await self.request('POST', '/rest/v1/rpc/comic_job_command',
                                  json={'p_action': action, 'p_args': args})
        if isinstance(data, dict) and 'error' in data:
            code = data['error']
            if code in {'IDEMPOTENCY_CONFLICT', 'COMIC_DRAFT_ALREADY_REQUESTED', 'COMIC_CLAIM_LOST'}:
                raise AppError(409, code, '기존 만화 요청 또는 실행 소유권 확인 필요')
            raise AppError(502, 'COMIC_JOB_INVALID_RESPONSE', '만화 작업 기록 오류')
        if action == 'probe':
            if data != {'version': 1}:
                raise AppError(503, 'COMIC_JOBS_UNAVAILABLE', '작업 스키마 버전 확인 필요')
            return None
        return None if data is None and action == 'claim' else self.decode(data)

    async def find(self, owner, session, request_id):
        rows = await self.request('GET', '/rest/v1/comic_generation_jobs', params={
            'user_id': f'eq.{owner}', 'session_id': f'eq.{session}',
            'client_request_id': f'eq.{request_id}', 'select': '*', 'limit': '2',
        })
        if not isinstance(rows, list) or len(rows) > 1:
            raise AppError(502, 'COMIC_JOB_INVALID_RESPONSE', '만화 작업 조회 오류')
        if not rows:
            return None
        job = self.decode(rows[0])
        if job.user_id != owner or job.session_id != session or job.client_request_id != request_id:
            raise AppError(502, 'COMIC_JOB_INVALID_RESPONSE', '만화 작업 소유권 오류')
        return job

    async def transition(self, job, action, **args):
        result = await self.command(action, preview_id=str(job.preview_id),
                                    claim_token=str(job.claim_token), **args)
        if (result.preview_id != job.preview_id or result.user_id != job.user_id
                or result.fingerprint != job.fingerprint or result.claim_token != job.claim_token):
            raise AppError(502, 'COMIC_JOB_INVALID_RESPONSE', '작업 변경 응답 오류')
        return result
