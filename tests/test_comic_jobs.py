import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.errors import AppError
from app.main import create_app
from tests.conftest import FakeAuth, MEMBER_A, TEST_PUBLISHABLE_KEY
from tests.fake_comic_jobs import JobAI, JobDatabase, JobStorage
from tests.fake_comics import prepared
from tests.test_saved_diaries import FAKE_SECRET


@asynccontextmanager
async def runtime(db=None, storage=None, ai=None, enabled=True):
    db, storage, ai = db or JobDatabase(), storage or JobStorage(), ai or JobAI()
    app=create_app(Settings(supabase_publishable_key=TEST_PUBLISHABLE_KEY,supabase_secret_key=FAKE_SECRET),
                   httpx.MockTransport(ai),httpx.MockTransport(FakeAuth()),httpx.MockTransport(db),
                   httpx.MockTransport(storage),enable_comic_worker=enabled)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app),
        base_url='http://be.test',headers={'Authorization':'Bearer fake-member-a'}) as client:
        service=app.state.comic_service
        service.jobs.poll_seconds=.005
        yield client,service,db,storage,ai


async def request_data(client, service):
    url, confirm, sid, body, plan, _=await prepared(client,service,'공을 굴렸다. 책을 읽었다. 그림을 그렸다. 블록을 쌓았다.')
    body=body.model_copy(update={'input_confirmed':True,'scene_confirmed':True,'confirmed_plan':plan})
    return url,confirm,sid,body


async def finished(db, timeout=4):
    async with asyncio.timeout(timeout):
        while not db.jobs or not all(j['result_saved'] for j in db.jobs.values()):
            await asyncio.sleep(.01)


@pytest.mark.anyio
async def test_accepted_request_is_async_concurrent_idempotent_and_read_only_recovery():
    ai=JobAI(); ai.block=asyncio.Event()
    async with runtime(ai=ai) as (client,service,db,storage,ai):
        _,_,sid,body=await request_data(client,service)
        path=f'/api/chat-sessions/{sid}/comic-previews'
        results=await asyncio.gather(*[client.post(path,json=body.model_dump()) for _ in range(8)])
        assert all(r.status_code==202 for r in results), [(r.status_code,r.text) for r in results]
        await asyncio.wait_for(ai.entered.wait(),2)
        assert len(ai.images)==len(db.jobs)==1
        other=await client.post(path,json={**body.model_dump(),'client_request_id':'other-key'})
        assert other.status_code==409
        snapshot=deepcopy(db.jobs); count=len(storage.objects)
        url=f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}'
        for _ in range(3):
            r=await client.get(url)
            assert r.status_code==200 and r.json()['job_state']=='running' and not r.json()['retry_generation_allowed']
        assert db.jobs==snapshot and len(storage.objects)==count and len(ai.images)==1
        missing=await client.get(url+'-missing')
        assert missing.json()['job_state']=='unknown' and not missing.json()['receipt_found']
        foreign=await client.get(url,headers={'Authorization':'Bearer fake-member-b'})
        assert not foreign.json()['receipt_found'] and foreign.json()['panels']==[]
        ai.block.set()
        await finished(db)
        assert len(ai.images)==4 and len(storage.objects)==4
        r=await client.get(url)
        assert r.json()['job_state']=='completed' and r.json()['status']=='ready'
        assert all(p['imageUrl'] for p in r.json()['panels'])
        service.store.sessions.clear()
        replay=await client.post(path,json=body.model_dump())
        assert replay.status_code==200 and replay.json()['job_state']=='completed' and len(ai.images)==4


@pytest.mark.anyio
@pytest.mark.parametrize('failure',[502,503,504,'timeout'])
async def test_provider_unknown_never_regenerates_across_restart(failure):
    ai=JobAI(); ai.failure=failure
    async with runtime(ai=ai) as (client,service,db,storage,ai):
        _,_,sid,body=await request_data(client,service)
        await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        await finished(db)
        assert len(ai.images)==1 and next(iter(db.jobs.values()))['state']=='unknown'
    async with runtime(db,storage,ai) as (client,service,_,_,_):
        r=await client.get(f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}')
        assert r.json()['job_state']=='unknown' and r.json()['error_code']=='COMIC_RESULT_UNKNOWN'
        await asyncio.sleep(.03)
        assert len(ai.images)==1


@pytest.mark.anyio
@pytest.mark.parametrize('mode',['upload_lost','offline','checkpoint_lost','ready_lost','complete_lost','admit_lost','dispatch_lost'])
async def test_checkpoint_and_receipt_response_loss_no_duplicate_images(mode):
    async with runtime() as (client,service,db,storage,ai):
        _,_,sid,body=await request_data(client,service)
        if mode in {'upload_lost','offline'}: storage.failure=mode
        else: db.job_failure=mode
        r=await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        assert r.status_code==(503 if mode=='admit_lost' else 202)
        await finished(db)
        assert len(ai.images)==(0 if mode=='dispatch_lost' else 4)
        result=await client.get(f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}')
        assert result.json()['job_state']==('unknown' if mode=='dispatch_lost' else 'completed')


@pytest.mark.anyio
async def test_restart_after_two_checkpoints_generates_only_remaining_panels():
    async with runtime() as (client,service,db,storage,ai):
        _,_,sid,body=await request_data(client,service)
        original=service.jobs.repository.transition
        gate=asyncio.Event(); reached=asyncio.Event()
        async def pause(job,action,**args):
            result=await original(job,action,**args)
            if action=='ready' and args['scene_id']=='comic4-2':
                reached.set(); await gate.wait()
            return result
        service.jobs.repository.transition=pause
        await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        await asyncio.wait_for(reached.wait(),2)
        assert len(ai.images)==2 and len(storage.objects)==2
    db.offset=301
    async with runtime(db,storage,ai) as (client,service,_,_,_):
        await finished(db)
        assert [p['source_ids'] for p in ai.images]==[[0],[1],[2],[3]]
        r=await client.get(f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}')
        assert r.json()['status']=='ready'


@pytest.mark.anyio
async def test_lost_dispatch_on_shutdown_is_unknown_and_old_claim_is_fenced():
    ai=JobAI(); ai.block=asyncio.Event()
    async with runtime(ai=ai) as (client,service,db,storage,ai):
        _,_,sid,body=await request_data(client,service)
        await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        await asyncio.wait_for(ai.entered.wait(),2)
        old=await service.jobs.repository.find(MEMBER_A,sid,body.client_request_id)
    db.offset=301; ai.block=None
    async with runtime(db,storage,ai) as (client,service,_,_,_):
        await finished(db)
        assert len(ai.images)==1 and not storage.objects
        with pytest.raises(AppError,match='COMIC_CLAIM_LOST'):
            await service.jobs.repository.transition(old,'renew')


@pytest.mark.anyio
@pytest.mark.parametrize('enabled,broken',[(False,False),(True,True)])
async def test_no_worker_or_missing_schema_keeps_503_and_normal_diary_save(enabled,broken):
    db=JobDatabase()
    if broken: db.job_failure='probe'
    async with runtime(db=db,enabled=enabled) as (client,service,db,storage,ai):
        url,confirm,sid,body=await request_data(client,service)
        r=await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        assert r.status_code==503 and not db.jobs and not ai.images
        assert (await client.post(url,json=confirm)).status_code==200


@pytest.mark.anyio
async def test_completed_preview_attach_and_failed_comic_do_not_break_diary():
    async with runtime() as (client,service,db,storage,ai):
        url,confirm,sid,body=await request_data(client,service)
        # Restore fixture draft text in confirm's server snapshot; confirmation uses server data.
        await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        await finished(db)
        d=await client.post(url,json=confirm)
        assert d.status_code==200
        did=d.json()['diary_id']; pid=next(iter(db.jobs))
        attached=await client.post(f'/api/diaries/{did}/comics',json={'client_request_id':'attach-1','preview_id':pid})
        assert attached.status_code==200 and attached.json()['status']=='ready'
        assert (await client.get(f'/api/diaries/{did}/comics')).json()['status']=='ready'


@pytest.mark.anyio
async def test_two_workers_claim_only_once():
    db,storage,ai=JobDatabase(),JobStorage(),JobAI()
    async with runtime(db,storage,ai) as (client,service,_,_,_):
        async with runtime(db,storage,ai):
            _,_,sid,body=await request_data(client,service)
            await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
            await finished(db)
            assert len(ai.images)==4


@pytest.mark.anyio
async def test_received_metadata_without_uploaded_bytes_remains_unknown_after_restart():
    async with runtime() as (client,service,db,storage,ai):
        _,_,sid,body=await request_data(client,service)
        original=service.jobs.repository.transition
        reached=asyncio.Event(); gate=asyncio.Event()
        async def pause(job,action,**args):
            result=await original(job,action,**args)
            if action=='checkpoint': reached.set(); await gate.wait()
            return result
        service.jobs.repository.transition=pause
        await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        await asyncio.wait_for(reached.wait(),2)
        assert len(ai.images)==1 and not storage.objects
        asset=deepcopy(next(iter(db.jobs.values()))['progress']['comic4-1']['asset'])
    db.offset=301
    async with runtime(db,storage,ai) as (client,service,_,_,_):
        await finished(db)
        job=next(iter(db.jobs.values()))
        assert len(ai.images)==1 and job['state']=='unknown'
        assert job['progress']['comic4-1']=={'state':'unknown','asset':asset}


@pytest.mark.anyio
async def test_expired_old_worker_cannot_publish_provider_reply_or_regenerate():
    ai=JobAI(); ai.block=asyncio.Event()
    db,storage=JobDatabase(),JobStorage()
    async with runtime(db,storage,ai) as (client,service,_,_,_):
        _,_,sid,body=await request_data(client,service)
        await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        await asyncio.wait_for(ai.entered.wait(),2)
        db.offset=301
        async with runtime(db,storage,ai):
            await finished(db)
            ai.block.set()
            await asyncio.sleep(.05)
            assert len(ai.images)==1 and not storage.objects
            assert next(iter(db.jobs.values()))['state']=='unknown'


@pytest.mark.anyio
async def test_failed_images_still_allow_normal_diary_save():
    ai=JobAI(); ai.failure='failed'
    async with runtime(ai=ai) as (client,service,db,storage,ai):
        url,confirm,sid,body=await request_data(client,service)
        await client.post(f'/api/chat-sessions/{sid}/comic-previews',json=body.model_dump())
        await finished(db)
        r=await client.get(f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}')
        assert r.json()['job_state']=='completed' and r.json()['status']=='failed'
        assert not storage.objects
        assert (await client.post(url,json=confirm)).status_code==200
