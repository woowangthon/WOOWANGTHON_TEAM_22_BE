import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from tests.fake_comic_jobs import JobAI, JobDatabase
from tests.test_comic_jobs import runtime, request_data, finished

pytestmark = pytest.mark.anyio

@pytest.mark.parametrize('mode', ['before_write', 'reply_lost', 'reply_invalid'])
async def test_definitive_failure_retries_only_database_storage(mode):
    class Database(JobDatabase):
        remaining = 2 if mode == 'before_write' else 1

        async def __call__(self, request):
            if request.url.path.endswith('/comic_job_command'):
                body = json.loads(request.content)
                if (self.remaining and body['p_action'] == 'outcome'
                        and body['p_args'].get('outcome') == 'failed'):
                    self.remaining -= 1
                    if mode == 'before_write':
                        return httpx.Response(503)
                    await super().__call__(request)
                    if mode == 'reply_lost':
                        raise httpx.ReadTimeout('synthetic lost DB reply')
                    return httpx.Response(200, json={'malformed': True})
            return await super().__call__(request)

    ai = JobAI()
    ai.failure = 'failed'
    async with runtime(db=Database(), ai=ai) as (client, service, db, storage, ai):
        _, _, sid, body = await request_data(client, service)
        await client.post(f'/api/chat-sessions/{sid}/comic-previews', json=body.model_dump())
        await finished(db)
        result = await client.get(f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}')
        assert result.json()['job_state'] == 'completed'
        assert [p['status'] for p in result.json()['panels']] == ['failed'] * 4
        assert len(ai.images) == 4 and not storage.objects and db.remaining == 0

@pytest.mark.parametrize('outcome,expected', [
    (400, 'failed'), (401, 'failed'), (403, 'failed'), (422, 'failed'), (429, 'failed'),
    ('connect', 'failed'), ('connect_timeout', 'failed'), ('pool_timeout', 'failed'),
    (302, 'unknown'), (408, 'unknown'), (499, 'unknown'),
    (502, 'unknown'), (503, 'unknown'), (504, 'unknown'),
    ('read_timeout', 'unknown'), ('write_timeout', 'unknown'), ('malformed', 'unknown'),
])
async def test_worker_distinguishes_ai_rejection_from_lost_result(outcome, expected):
    class AI(JobAI):
        async def __call__(self, request):
            if request.url.path.endswith('/visual-story/image') and len(self.images) == 1:
                self.images.append(json.loads(request.content))
                failures = {'connect':httpx.ConnectError, 'connect_timeout':httpx.ConnectTimeout,
                            'pool_timeout':httpx.PoolTimeout, 'read_timeout':httpx.ReadTimeout,
                            'write_timeout':httpx.WriteTimeout}
                if outcome in failures:
                    raise failures[outcome]('synthetic failure', request=request)
                if outcome == 'malformed':
                    return httpx.Response(200, content=b'{')
                return httpx.Response(outcome, json={'detail':'synthetic refusal'})
            return await super().__call__(request)
    async with runtime(ai=AI()) as (client, service, db, storage, ai):
        _, _, sid, body = await request_data(client, service)
        post = f'/api/chat-sessions/{sid}/comic-previews'
        get = f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}'
        await client.post(post, json=body.model_dump())
        await finished(db)
        statuses = ['ready','failed','ready','ready'] if expected == 'failed' else ['ready']+['unknown']*3
        snapshot = (await client.get(get)).json()
        assert [p['status'] for p in snapshot['panels']] == statuses
        assert not snapshot['retry_generation_allowed']
        calls, saved = len(ai.images), deepcopy(storage.objects)
        job = deepcopy(db.jobs)
        for _ in range(3):
            panels = (await client.get(get)).json()['panels']
            assert [p['status'] for p in panels] == statuses
            assert [p['imageUrl'] for p in panels] == [p['imageUrl'] for p in snapshot['panels']]
        assert (await client.post(post, json=body.model_dump())).status_code == 200
        assert db.jobs == job and storage.objects == saved and len(ai.images) == calls
    async with runtime(db, storage, ai) as (client, service, _, _, _):
        assert [p['status'] for p in (await client.get(get)).json()['panels']] == statuses
        await asyncio.sleep(.03)
        assert storage.objects == saved and len(ai.images) == calls

async def test_restart_after_failed_response_before_db_save_stays_unknown(monkeypatch):
    # A terminal reply held only in RAM cannot be reconstructed after a process crash.
    ai = JobAI()
    async with runtime(ai=ai) as (client, service, db, storage, ai):
        _, _, sid, body = await request_data(client, service)
        reached = asyncio.Event()
        gate = asyncio.Event()
        original = service.jobs.repository.transition
        async def pause(job, action, **args):
            if action == 'outcome' and args.get('outcome') == 'failed':
                reached.set()
                await gate.wait()
            return await original(job, action, **args)
        service.jobs.repository.transition = pause
        ai.failure = 'failed'
        await client.post(f'/api/chat-sessions/{sid}/comic-previews', json=body.model_dump())
        await asyncio.wait_for(reached.wait(), 2)
        assert next(iter(db.jobs.values()))['progress']['comic4-1']['state'] == 'dispatched'
    db.offset = 301
    async with runtime(db, storage, ai) as (client, service, _, _, _):
        await finished(db)
        result = (await client.get(f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}')).json()
        assert result['job_state'] == 'unknown' and len(ai.images) == 1
        assert all(p['status'] == 'unknown' for p in result['panels'])
