"""DB/Storage의 모의 상태 전이. 실제 PostgreSQL 잠금/RLS 실행 증거가 아니다."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import httpx

from tests.fake_comics import ComicDatabase, image_for, plan_for
from tests.conftest import FakeAI
from tests.test_drawings import DrawingStorage
from tests.test_saved_diaries import FAKE_SECRET


class JobDatabase(ComicDatabase):
    def __init__(self):
        super().__init__()
        self.jobs = {}
        self.job_lock = asyncio.Lock()
        self.offset = 0
        self.job_failure = None
        self.job_commands = []

    def now(self):
        return datetime.now(timezone.utc) + timedelta(seconds=self.offset)

    async def __call__(self, request):
        if request.url.path not in {'/rest/v1/comic_generation_jobs', '/rest/v1/rpc/comic_job_command'}:
            return await super().__call__(request)
        self.requests.append(request)
        assert request.headers['apikey'] == FAKE_SECRET and 'authorization' not in request.headers
        if request.method == 'GET':
            rows = list(self.jobs.values())
            for key in ('user_id', 'session_id', 'client_request_id'):
                rows = [r for r in rows if 'eq.' + r[key] == request.url.params[key]]
            return httpx.Response(200, json=deepcopy(rows))
        body = json.loads(request.content)
        action, args = body['p_action'], body['p_args']
        self.job_commands.append(action)
        if self.job_failure == action:
            return httpx.Response(503)
        async with self.job_lock:
            result = self.job_command(action, args)
        if self.job_failure == action + '_lost':
            self.job_failure = None
            raise httpx.ReadTimeout('synthetic lost DB response')
        return httpx.Response(200, content=b'null') if result is None else httpx.Response(200, json=deepcopy(result))

    def job_command(self, action, args):
        now = self.now()
        lease = (now + timedelta(seconds=300)).isoformat()
        if action == 'probe': return {'version': 1}
        if action == 'admit':
            j = args['job']
            for old in self.jobs.values():
                if (old['user_id'], old['session_id'], old['client_request_id']) == (j['user_id'], j['session_id'], j['client_request_id']):
                    return old if old['fingerprint'] == j['fingerprint'] else {'error': 'IDEMPOTENCY_CONFLICT'}
                if all(old[k] == j[k] for k in ('user_id','session_id','draft_id','history_revision')):
                    return {'error': 'COMIC_DRAFT_ALREADY_REQUESTED'}
            job = {**deepcopy(j), 'progress': {s['scene_id']: {'state':'waiting','asset':None}
                   for s in j['confirmed_plan']['comic4']['scenes'] if not s['repeated']},
                   'state':'queued','claim_token':None,'lease_until':None,'result_saved':False,'created_at':now.isoformat()}
            self.jobs[j['preview_id']] = job
            return job
        if action == 'claim':
            for j in self.jobs.values():
                if j['state'] == 'queued' or (j['state'] == 'running' and datetime.fromisoformat(j['lease_until']) <= now):
                    j.update(state='running',claim_token=args['claim_token'],lease_until=lease)
                    return j
            return None
        j = self.jobs.get(args['preview_id'])
        if not j or j['state'] != 'running' or j['claim_token'] != args['claim_token'] or datetime.fromisoformat(j['lease_until']) <= now:
            return {'error':'COMIC_CLAIM_LOST'}
        if action in {'dispatch','checkpoint','ready','outcome'}:
            p = j['progress'][args['scene_id']]
            if action == 'dispatch':
                assert p['state'] == 'waiting' and not any(x['state']=='unknown' for x in j['progress'].values())
                p['state'] = 'dispatched'
            elif action == 'checkpoint':
                assert p['state'] == 'dispatched'
                p.update(state='checkpoint',asset=args['asset'])
            elif action == 'ready':
                assert p['state'] == 'checkpoint'
                p['state'] = 'ready'
            else:
                assert args['outcome'] in {'failed','rejected','mock','unknown'}
                assert p['state'] in ({'waiting','dispatched','checkpoint','ready'} if args['outcome']=='unknown' else {'dispatched'})
                p.update(state=args['outcome'],asset=p['asset'] if args['outcome']=='unknown' else None)
        elif action == 'complete':
            assert all(x['state'] not in {'waiting','dispatched','checkpoint'} for x in j['progress'].values())
            assert args['preview_id'] in self.previews
            j.update(result_saved=True,state='unknown' if any(p['state']=='unknown' for p in j['progress'].values()) else 'completed')
        else: assert action == 'renew'
        j['lease_until'] = lease
        return j


class JobStorage(DrawingStorage):
    async def __call__(self, request):
        if request.method == 'GET' and '/object/diary-private/' in request.url.path:
            key=request.url.path.split('/object/diary-private/')[1]
            if key not in self.objects:
                self.requests.append(request)
                return httpx.Response(404)
        return await super().__call__(request)


class JobAI(FakeAI):
    def __init__(self):
        super().__init__()
        self.images = []
        self.failure = None
        self.block = None
        self.entered = asyncio.Event()

    async def __call__(self, request):
        if request.url.path not in {'/api/visual-story', '/api/visual-story/image'}:
            return await super().__call__(request)
        assert 'authorization' not in request.headers and 'apikey' not in request.headers
        payload=json.loads(request.content)
        plan=plan_for(payload['diary_text'])
        if request.url.path.endswith('/visual-story'):
            return httpx.Response(200,json=plan.model_dump())
        self.images.append(payload)
        self.entered.set()
        if self.block is not None: await self.block.wait()
        if self.failure == 'timeout': raise httpx.ReadTimeout('synthetic provider timeout')
        if self.failure in {502,503,504}: return httpx.Response(self.failure)
        scene=next(s for s in plan.comic4.scenes if s.source_ids==payload['source_ids'])
        return httpx.Response(200,json=image_for(plan,scene,status=self.failure or 'generated').model_dump())
