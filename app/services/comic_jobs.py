"""단일 BE 수명주기 실행기. 영속 claim/전송 전 기록으로 유료 생성 재전송을 막는다."""
import asyncio
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.clients.comic_ai import scene_key, validate_plan
from app.comic_job_schemas import ComicJobResponse
from app.comic_schemas import ComicAsset, ComicPanel, ComicPreviewRequest, VisualImage
from app.errors import AppError
from app.services.comic_images import decode_comic_image


def fingerprint(body):
    return sha256(json.dumps(body.model_dump(mode='json'), sort_keys=True,
                             ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def data_url(content):
    return 'data:image/png;base64,' + base64.b64encode(content).decode()


class ComicJobService:
    def __init__(self, comics, repository, ai, poll_seconds=1):
        self.comics, self.repository, self.ai = comics, repository, ai
        self.poll_seconds = poll_seconds
        self.task = None
        self.healthy = False
        self.pending = None
        # 단일 실행기에서 마지막 생성 이미지 하나(정규화 최대10MiB)만 보존한다.
        self.bytes = None

    @property
    def available(self):
        return self.task is not None and not self.task.done() and self.healthy

    async def start(self):
        if self.task is not None:
            raise RuntimeError('만화 실행기 중복 시작')
        if self.comics.repository.storage.writer is None:
            return
        try:
            await self.repository.command('probe')
        except AppError:
            return  # 일반 그림일기는 유지하고 생성 접수만503으로 막는다.
        self.healthy = True
        self.task = asyncio.create_task(self.run(), name='comic-worker')

    async def stop(self):
        self.healthy = False
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        # DB의 dispatched/checkpoint 기록은 남긴다. 종료를 생성 실패 확정으로 바꾸지 않는다.
        self.bytes = None

    async def submit(self, owner, session, body):
        if not self.available:
            raise AppError(503, 'COMIC_PREVIEW_NOT_ENABLED', '만화 실행 담당자 준비 필요')
        old = await self.repository.find(owner, session, body.client_request_id)
        digest = fingerprint(body)
        if old is not None:
            if old.fingerprint != digest:
                raise AppError(409, 'IDEMPOTENCY_CONFLICT', '원래 만화 요청 내용 불일치')
            return await self.response(old, None)  # POST는 서명 없이 원래 작업 상태만 반환.
        text = await self.comics.draft_text(owner, session, body)
        if not body.input_confirmed or not body.scene_confirmed or body.confirmed_plan is None:
            raise AppError(409, 'COMIC_CONFIRMATION_REQUIRED', '원문과 장면 확인 필요')
        try:
            validate_plan(body.confirmed_plan, text)
        except ValueError:
            raise AppError(409, 'COMIC_PLAN_MISMATCH', '확인한 장면과 원문 불일치') from None
        initial = dict(preview_id=str(uuid5(NAMESPACE_URL, f'comic/{owner}/{session}/{body.client_request_id}')),
                       user_id=str(owner), session_id=session, client_request_id=body.client_request_id,
                       history_revision=body.history_revision, draft_id=body.draft_id, draft_text=text,
                       confirmed_plan=body.confirmed_plan.model_dump(mode='json'), fingerprint=digest)
        job = await self.repository.command('admit', job=initial)
        if (job.user_id != owner or job.session_id != session or job.fingerprint != digest
                or str(job.preview_id) != initial['preview_id']):
            raise AppError(502, 'COMIC_JOB_INVALID_RESPONSE', '만화 접수 응답 불일치')
        if not self.available:
            raise AppError(503, 'COMIC_JOBS_UNAVAILABLE', '원래 요청 ID로 상태 확인 필요')
        return await self.response(job, None)

    async def get(self, owner, token, session, request_id):
        job = await self.repository.find(owner, session, request_id)
        if job is None:
            return ComicJobResponse(client_request_id=request_id, job_state='unknown',
                                    receipt_found=False, worker_available=self.available,
                                    status='none', error_code='COMIC_RESULT_UNKNOWN')
        return await self.response(job, token)

    async def response(self, job, token):
        expired = (job.state == 'running' and job.lease_until <= datetime.now(timezone.utc))
        state = 'unknown' if expired else job.state
        if job.result_saved and token:
            result = await self.comics.preview(job.user_id, token, job.session_id, job.preview_id)
        else:
            panels = []
            for scene in job.confirmed_plan.comic4.scenes:
                p = job.progress[scene.reuse_scene_id if scene.repeated else scene.scene_id]
                outcome = p.state if p.state in {'ready', 'failed', 'unknown', 'rejected', 'mock'} else 'pending'
                signed = (await self.comics.repository.sign(job, p.asset, token)
                          if token and p.state == 'ready' else None)
                panels.append(ComicPanel(position=scene.order, caption=scene.caption, status=outcome,
                                         imageUrl=signed.imageUrl if signed else None,
                                         expires_at=signed.expires_at if signed else None))
            from app.comic_schemas import ComicResponse
            ready = sum(p.status == 'ready' for p in panels)
            result = ComicResponse(preview_id=job.preview_id, panels=panels,
                                   status=state if state in {'queued', 'running'} else
                                   'ready' if ready == 4 else 'partial' if ready else 'failed',
                                   recovery_required=expired or any(p.state == 'checkpoint' for p in job.progress.values()),
                                   error_code='COMIC_RESULT_UNKNOWN' if state == 'unknown' else None)
        return ComicJobResponse(**result.model_dump(), client_request_id=job.client_request_id,
                                job_state=state, receipt_found=True, worker_available=self.available)

    async def run(self):
        while True:
            try:
                if self.pending is None:
                    self.pending = await self.repository.command('claim', claim_token=str(uuid4()))
                else:
                    old = self.pending
                    self.pending = await self.repository.find(old.user_id, old.session_id, old.client_request_id)
                    if (self.pending is None or self.pending.claim_token != old.claim_token
                            or self.pending.state != 'running'):
                        self.pending = None
                        self.bytes = None
                self.healthy = True
                if self.pending:
                    await self.execute(self.pending)
                    self.pending = None
                    self.bytes = None
            except AppError as error:
                self.healthy = False
                if error.code == 'COMIC_CLAIM_LOST':
                    self.pending = None
                    self.bytes = None
                # DB/업로드 재시도만 허용. 다음 순회는 영속 상태를 다시 읽고 dispatched를 재생성하지 않는다.
            except Exception:
                self.healthy = False
                # 예상외 오류도 다음 순회에서 전송 기록을 먼저 읽는다. 원문/이미지/키 로그 없음.
            await asyncio.sleep(self.poll_seconds)

    async def execute(self, job):
        for scene in job.confirmed_plan.comic4.scenes:
            if scene.repeated:
                continue
            p = job.progress[scene.scene_id]
            cache_key = (job.preview_id, scene.scene_id)
            image = self.bytes[1] if self.bytes and self.bytes[0] == cache_key else None
            if p.state == 'waiting':
                if any(x.state == 'unknown' for x in job.progress.values()):
                    job = await self.repository.transition(job, 'outcome', scene_id=scene.scene_id, outcome='unknown')
                    continue
                job = await self.repository.transition(job, 'dispatch', scene_id=scene.scene_id)
                try:
                    result = await self.ai.render(job.confirmed_plan, scene, scene_confirmed=True)
                    if result.status != 'generated':
                        job = await self.repository.transition(job, 'outcome', scene_id=scene.scene_id, outcome=result.status)
                        continue
                    async with self.comics.decode_slots:
                        image = await asyncio.to_thread(decode_comic_image, result.imageUrl)
                    self.bytes = (cache_key, image)
                except AppError:
                    job = await self.repository.transition(job, 'outcome', scene_id=scene.scene_id, outcome='unknown')
                    continue
                p = job.progress[scene.scene_id]
            if p.state == 'dispatched':
                if image is None:
                    job = await self.repository.transition(job, 'outcome', scene_id=scene.scene_id, outcome='unknown')
                    continue
                asset = ComicAsset(asset_id=uuid5(job.preview_id, scene.scene_id + image.metadata['sha256']), **image.metadata)
                job = await self.repository.transition(job, 'checkpoint', scene_id=scene.scene_id, asset=asset.model_dump(mode='json'))
                p = job.progress[scene.scene_id]
            if p.state == 'checkpoint':
                if image is None:
                    content = await self.comics.repository.checkpoint_bytes(job, p.asset)
                    if content is None:
                        job = await self.repository.transition(job, 'outcome', scene_id=scene.scene_id, outcome='unknown')
                        continue
                    image = await asyncio.to_thread(decode_comic_image, data_url(content))
                if image.metadata != p.asset.model_dump(exclude={'asset_id'}):
                    raise AppError(409, 'COMIC_RECOVERY_MISMATCH', '보관 이미지 불일치')
                await self.comics.repository.upload(job, p.asset, image)
                job = await self.repository.transition(job, 'ready', scene_id=scene.scene_id)
                self.bytes = None
        # 결과 테이블에는 최종 snapshot만 넣는다. 기존 fingerprint·첨부 계약은 유지한다.
        rendered, urls = {}, {}
        for scene in job.confirmed_plan.comic4.scenes:
            if scene.repeated:
                continue
            p = job.progress[scene.scene_id]
            url = None
            if p.state == 'ready':
                content = await self.comics.repository.checkpoint_bytes(job, p.asset)
                if content is None:
                    job = await self.repository.transition(job, 'outcome', scene_id=scene.scene_id, outcome='unknown')
                    p = job.progress[scene.scene_id]
                else:
                    url = data_url(content)
                    urls[p.asset.asset_id] = url
            rendered[scene.scene_id] = None if p.state == 'unknown' else VisualImage(
                diary_hash=job.confirmed_plan.diary_hash, scene_key=scene_key(job.confirmed_plan.diary_hash, scene.source_ids),
                caption=scene.caption, source_ids=scene.source_ids, format='comic4', imageUrl=url,
                status='generated' if url else p.state, requires_confirmation=True, mock=p.state == 'mock')
        body = ComicPreviewRequest(client_request_id=job.client_request_id, history_revision=job.history_revision, draft_id=job.draft_id)
        # lease 확인을 포함한 서버 전용 fence. 유료 호출 없이 결과/업로드만 복구한다.
        job = await self.repository.transition(job, 'renew')
        saved = await self.comics.persist_result(job.user_id, job.session_id, body, job.confirmed_plan,
                                                 rendered, draft_snapshot=job.draft_text, upload=False)
        if urls:
            await self.comics.recover_result_uploads(job.user_id, None, job.preview_id, urls, saved=saved)
        await self.repository.transition(job, 'complete')
