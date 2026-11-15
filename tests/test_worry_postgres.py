"""명시적으로 준비한 합성 전용 로컬 PostgreSQL 검증. 운영 DB 사용 불가.

WORRY_TEST_PG_ROOT는 initdb로 새로 만든 서버의 루트. 127.0.0.1:54679,
worry_contract_test 고정. 이 옵션이 없으면 건너뛴다. AI/Auth만 MockTransport.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeAuth, MEMBER_A, MEMBER_B, TEST_PUBLISHABLE_KEY

ROOT=os.environ.get('WORRY_TEST_PG_ROOT')
pytestmark=[pytest.mark.anyio,pytest.mark.skipif(not ROOT,reason='isolated PostgreSQL not configured')]
BE=Path(__file__).resolve().parents[1]


def sql(query):
    env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
    env['PGCLIENTENCODING']='UTF8'
    r=subprocess.run([str(Path(ROOT)/'pgsql/bin/psql.exe'),'-X','-w','-A','-t','-q',
        '-v','ON_ERROR_STOP=1','-h','127.0.0.1','-p','54679','-U','postgres','-d','worry_contract_test'],
        input=query,encoding='utf-8',capture_output=True,env=env,timeout=30)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


def literal(value):
    return "'"+value.replace("'","''")+"'"


@pytest.fixture(scope='module',autouse=True)
def schema():
    if not ROOT: return
    assert sql('select current_database()')=='worry_contract_test'
    if sql("select to_regclass('public.worry_sessions') is not null")=='t':
        return
    sql("create role anon; create role authenticated; create role service_role bypassrls; "
        "create schema auth; create table auth.users(id uuid primary key, is_anonymous boolean, "
        "role text, deleted_at timestamptz); grant usage on schema auth to service_role; "
        "grant select on auth.users to service_role;")
    sql((BE/'supabase/candidates/worry_conversations_v1.sql').read_text(encoding='utf-8'))


@pytest.fixture
def clean_db(schema):
    # 이 테스트가 만든 전용 DB의 합성 행만 초기화한다.
    sql('truncate public.worry_sessions,public.worry_access_grants,auth.users cascade;')
    for owner in (MEMBER_A,MEMBER_B):
        sql(f"insert into auth.users values('{owner}',false,'authenticated',null);"
            f"insert into public.worry_access_grants values('{owner}','worry-v1-proposal',"
            "'synthetic-review',true,now()-interval '1 hour',now()+interval '30 days',null);")


class DBTransport:
    def __init__(self): self.fault=None

    async def __call__(self,req):
        assert req.url.path=='/rest/v1/rpc/worry_command'
        b=json.loads(req.content)
        query=('set role service_role; select public.worry_command('
            +literal(b['p_owner'])+'::uuid,'+literal(b['p_action'])+','
            +(literal(b['p_session'])+'::uuid' if b['p_session'] else 'null')+','
            +literal(json.dumps(b['p_args'],ensure_ascii=False))+'::jsonb);')
        if self.fault==(b['p_action'],'before'):
            self.fault=None
            raise httpx.ReadTimeout('synthetic DB response loss')
        result=await asyncio.to_thread(sql,query)
        if self.fault==(b['p_action'],'after'):
            self.fault=None
            raise httpx.ReadTimeout('synthetic DB response loss')
        return httpx.Response(200,json=json.loads(result))


class WorryAI:
    def __init__(self): self.calls=[]; self.outcome=None; self.entered=asyncio.Event(); self.release=None

    async def __call__(self,req):
        assert req.url.path=='/api/worry/chat'
        data=json.loads(req.content)
        self.calls.append(data)
        self.entered.set()
        if self.release: await self.release.wait()
        if isinstance(self.outcome,Exception): raise self.outcome
        if isinstance(self.outcome,int): return httpx.Response(self.outcome,json={'error':'private-provider-body'})
        status=self.outcome or ('reply' if data['messages'] else 'opening')
        return httpx.Response(200,json={'status':status,'message':'합성 응답','mock':True})


@asynccontextmanager
async def api(db=None,ai=None,member='a'):
    db=db or DBTransport(); ai=ai or WorryAI()
    settings=Settings(worry_enabled=True,supabase_publishable_key=TEST_PUBLISHABLE_KEY,
                      supabase_secret_key='sb_secret_synthetic_test')
    app=create_app(settings,ai_transport=httpx.MockTransport(ai),auth_transport=httpx.MockTransport(FakeAuth()),
                   db_transport=httpx.MockTransport(db))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
        base_url='http://be.test',headers={'Authorization':f'Bearer fake-member-{member}'}) as client:
        yield client,db,ai


async def start(c,key='open1'):
    return await c.post('/api/worry/sessions',json={'notice_version':'worry-v1-proposal'},headers={'Idempotency-Key':key})


async def send(c,sid,text='합성 아이 입력',key='m1'):
    return await c.post(f'/api/worry/sessions/{sid}/messages',json={'text':text,'client_message_id':key})


async def test_lifecycle_history_and_fresh_app(clean_db):
    async with api() as (c,db,ai):
        opened=await start(c); assert opened.status_code==201,opened.text
        s=opened.json(); sid=s['id']
        assert s['last_ai_status']=='opening' and s['messages'][0]['position']==0
        assert (await c.get('/api/worry/sessions')).json()['sessions']==[]
        assert (await start(c)).json()==s and len(ai.calls)==1
        r=await send(c,sid); assert r.status_code==200,r.text
        s=r.json(); assert len(s['messages'])==3 and s['last_ai_status']=='reply'
        assert ai.calls[-1]=={'messages':[{'role':'assistant','text':'합성 응답'},{'role':'child','text':'합성 아이 입력'}]}
        assert len((await c.get('/api/worry/sessions')).json()['sessions'])==1
        finish=await c.post(f'/api/worry/sessions/{sid}/finish')
        assert finish.json()['state']=='finished'
        assert (await c.post(f'/api/worry/sessions/{sid}/finish')).json()==finish.json()
        assert (await send(c,sid,key='m2')).json()['error']['code']=='WORRY_FINISHED'
        assert len(ai.calls)==2
        new=(await start(c,'open2')).json(); assert new['id']!=sid
    # 새 앱·HTTP client·repository 인스턴스. Python 메모리 공유 없이 DB에서 복구한다.
    async with api() as (c,_,ai):
        restored=await c.get(f'/api/worry/sessions/{sid}')
        assert restored.json()==finish.json() and not ai.calls
        assert sql(f"select extract(epoch from expires_at-created_at) from worry_sessions where id='{sid}'")=='604800.000000'


async def test_idempotency_conflict_and_concurrent_delivery(clean_db):
    async with api() as (c,_,ai):
        sid=(await start(c)).json()['id']
        ai.entered.clear(); ai.release=asyncio.Event()
        pending=asyncio.create_task(send(c,sid))
        await ai.entered.wait()
        duplicate=await send(c,sid)
        assert duplicate.status_code==200 and duplicate.json()['requests'][-1]['status']=='dispatched'
        conflict=await send(c,sid,text='다른 합성 입력')
        assert conflict.status_code==409 and conflict.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
        assert (await send(c,sid,key='m2')).json()['error']['code']=='WORRY_RECOVERY_REQUIRED'
        ai.release.set(); result=await pending
        assert len(ai.calls)==2 and len(result.json()['messages'])==3
        assert (await send(c,sid)).json()==result.json() and len(ai.calls)==2


async def test_concurrent_new_sessions_and_finish_during_ai(clean_db):
    async with api() as (c,db,ai):
        results=await asyncio.gather(start(c,'a'),start(c,'b'))
        assert all(r.status_code==201 for r in results)
        assert sql(f"select count(*) from worry_sessions where user_id='{MEMBER_A}' and state='active'")=='1'
        sid=sql(f"select id from worry_sessions where user_id='{MEMBER_A}' and state='active'")
        ai.entered.clear(); ai.release=asyncio.Event()
        task=asyncio.create_task(send(c,sid)); await ai.entered.wait()
        finished=await c.post(f'/api/worry/sessions/{sid}/finish')
        ai.release.set(); result=await task
        assert result.json()==finished.json()
        assert result.json()['requests'][-1]['status']=='unknown'


async def test_owner_consent_and_no_guardian_access(clean_db):
    async with api() as (c,_,ai):
        sid=(await start(c)).json()['id']
        await send(c,sid)
        headers={'Authorization':'Bearer fake-member-b'}
        for method,path,body in [('GET',f'/api/worry/sessions/{sid}',None),
            ('POST',f'/api/worry/sessions/{sid}/finish',None),
            ('POST',f'/api/worry/sessions/{sid}/messages',{'text':'합성','client_message_id':'other'})]:
            assert (await c.request(method,path,json=body,headers=headers)).status_code==404
        assert (await c.get('/api/worry/sessions',headers=headers)).json()['sessions']==[]
        assert (await c.get(f'/api/worry/sessions/{sid}?guardian=true',headers=headers)).status_code==404
        sql(f"update worry_access_grants set revoked_at=now() where user_id='{MEMBER_A}'")
        assert (await c.get(f'/api/worry/sessions/{sid}')).status_code==403
        assert (await start(c,'revoked')).status_code==403
        assert len(ai.calls)==2


async def test_expiry_blocks_before_cleanup_and_purge_is_scoped(clean_db):
    async with api() as (c,_,ai):
        sid=(await start(c)).json()['id']; await send(c,sid)
        sql(f"update worry_sessions set created_at=now()-interval '168 hours'+interval '5 seconds',expires_at=now()+interval '5 seconds' where id='{sid}'")
        assert (await c.get(f'/api/worry/sessions/{sid}')).status_code==200
        sql(f"update worry_sessions set created_at=now()-interval '168 hours',expires_at=now() where id='{sid}'")
        assert (await c.get('/api/worry/sessions')).json()['sessions']==[]
        for method,path,body in [('GET',f'/api/worry/sessions/{sid}',None),
            ('POST',f'/api/worry/sessions/{sid}/finish',None),
            ('POST',f'/api/worry/sessions/{sid}/messages',{'text':'합성','client_message_id':'m2'})]:
            assert (await c.request(method,path,json=body)).status_code==404
        assert (await start(c)).status_code==404
        new=(await start(c,'new')).json()['id']
        assert sql('select public.purge_expired_worry_sessions(1000)')=='1'
        assert sql('select public.purge_expired_worry_sessions(1000)')=='0'
        assert sql(f"select count(*) from worry_messages where session_id='{sid}'")=='0'
        assert sql(f"select count(*) from worry_requests where session_id='{sid}'")=='0'
        assert sql('select count(*) from auth.users')=='2'
        assert (await c.get(f'/api/worry/sessions/{new}')).status_code==200


@pytest.mark.parametrize(('outcome','status'), [('needs_adult','completed'),('finished','completed'),
    ('unavailable','unavailable'),(422,'rejected'),(503,'unknown'),(httpx.ReadTimeout('synthetic'),'unknown'),
    (httpx.ConnectError('synthetic'),'unavailable')])
async def test_ai_outcomes_preserve_child_and_block_retry(clean_db,outcome,status):
    async with api() as (c,_,ai):
        sid=(await start(c)).json()['id']; ai.outcome=outcome
        r=await send(c,sid); assert r.status_code==200,r.text
        s=r.json(); assert s['requests'][-1]['status']==status
        assert s['messages'][1]['text']=='합성 아이 입력'
        assert (await send(c,sid)).json()==s and len(ai.calls)==2
        assert (await send(c,sid,key='m2')).status_code==409
        if outcome in ('needs_adult','finished'): assert s['state']=='finished'
        if isinstance(outcome,str): assert s['last_ai_status']==outcome


@pytest.mark.parametrize(('action','when','expected_calls','expected'), [
    ('admit','before',1,'completed'),('admit','after',1,'accepted'),
    ('dispatch','before',1,'accepted'),('dispatch','after',1,'dispatched'),
    ('complete','before',2,'dispatched'),('complete','after',2,'completed'),
])
async def test_db_response_loss_never_reexecutes_model(clean_db,action,when,expected_calls,expected):
    async with api() as (c,db,ai):
        sid=(await start(c)).json()['id']; db.fault=(action,when)
        assert (await send(c,sid)).status_code==503
        detail=(await c.get(f'/api/worry/sessions/{sid}')).json()
        assert detail['requests'][-1]['status']==expected
        if action=='admit' and when=='before': return  # DB 미접수 상태이므로 동일 요청을 새로 접수할 수 있다.
        retry=await send(c,sid)
        assert retry.status_code==200 and len(ai.calls)==expected_calls
        if expected in ('accepted','dispatched'):
            sql(f"update worry_requests set deadline=now() where session_id='{sid}' and status in ('accepted','dispatched')")
            assert (await c.get(f'/api/worry/sessions/{sid}')).json()['requests'][-1]['status']=='unknown'
            assert (await send(c,sid)).json()['requests'][-1]['status']=='unknown'
            assert len(ai.calls)==expected_calls


async def test_history_count_and_character_limits_without_truncation(clean_db):
    async with api() as (c,_,ai):
        sid=(await start(c)).json()['id']
        for i in range(12): assert (await send(c,sid,key=f'm{i}')).status_code==200
        before=(await c.get(f'/api/worry/sessions/{sid}')).json()
        assert len(before['messages'])==25 and len(ai.calls[-1]['messages'])==24
        assert (await send(c,sid,key='overflow')).json()['error']['code']=='WORRY_HISTORY_LIMIT'
        assert (await c.get(f'/api/worry/sessions/{sid}')).json()==before
        sid=(await start(c,'chars')).json()['id']
        for i in range(3): assert (await send(c,sid,'x'*2000,f'm{i}')).status_code==200
        assert (await send(c,sid,'x'*2000,'too-long')).json()['error']['code']=='WORRY_HISTORY_LIMIT'


async def test_rls_and_rpc_execute_privileges(clean_db):
    for role in ('anon','authenticated'):
        for table in ('worry_access_grants','worry_sessions','worry_messages','worry_requests'):
            assert sql(f"select has_table_privilege('{role}','public.{table}','select')")=='f'
            assert sql(f"select relrowsecurity from pg_class where oid='public.{table}'::regclass")=='t'
        assert sql(f"select has_function_privilege('{role}','public.worry_command(uuid,text,uuid,jsonb)','execute')")=='f'
        with pytest.raises(AssertionError,match='permission denied'):
            sql(f'set role {role}; select * from worry_sessions;')
    assert sql("select has_table_privilege('service_role','worry_access_grants','insert')")=='f'


async def test_same_start_key_concurrent_and_new_python_process(clean_db):
    import sys
    async with api() as (c,_,ai):
        results=await asyncio.gather(start(c),start(c))
        assert all(r.status_code==201 for r in results)
        sid=results[0].json()['id']
        assert all(r.json()['id']==sid for r in results) and len(ai.calls)==1
        saved=(await send(c,sid)).json()
        code=("import asyncio,json; from tests.test_worry_postgres import api; "
              "exec(\"async def read():\\n async with api() as (c,db,ai):\\n"
              f"  r=await c.get('/api/worry/sessions/{sid}')\\n"
              "  assert r.status_code==200 and not ai.calls\\n  print(json.dumps(r.json()))\"); "
              "asyncio.run(read())")
        run=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',code],cwd=BE,
            capture_output=True,encoding='utf-8',timeout=30)
        assert run.returncode==0,run.stderr
        assert json.loads(run.stdout)==saved


@pytest.mark.parametrize('change', ['delete','expire','future','anonymous'])
async def test_server_grant_required_not_client_boolean(clean_db,change):
    if change=='delete': sql(f"delete from worry_access_grants where user_id='{MEMBER_A}'")
    if change=='expire': sql(f"update worry_access_grants set valid_until=now() where user_id='{MEMBER_A}'")
    if change=='future': sql(f"update worry_access_grants set consent_verified_at=now()+interval '1 day' where user_id='{MEMBER_A}'")
    if change=='anonymous': sql(f"update auth.users set is_anonymous=true where id='{MEMBER_A}'")
    async with api() as (c,_,ai):
        assert (await start(c)).status_code==403
        forged=await c.post('/api/worry/sessions',headers={'Idempotency-Key':'forged'},
            json={'notice_version':'worry-v1-proposal','consent':True})
        assert forged.status_code==422 and not ai.calls
        assert sql('select count(*) from worry_sessions')=='0'


async def test_opening_failure_preserved_and_requires_new_session(clean_db):
    async with api() as (c,_,ai):
        ai.outcome=httpx.ReadTimeout('synthetic')
        s=(await start(c)).json()
        assert s['messages']==[] and s['requests'][0]['status']=='unknown'
        assert (await start(c)).json()==s and len(ai.calls)==1
        assert (await send(c,s['id'])).status_code==409
        assert (await c.get('/api/worry/sessions')).json()['sessions']==[]


async def test_expiry_during_inflight_reply_does_not_store_late_answer(clean_db):
    async with api() as (c,_,ai):
        sid=(await start(c)).json()['id']
        ai.entered.clear(); ai.release=asyncio.Event()
        task=asyncio.create_task(send(c,sid)); await asyncio.wait_for(ai.entered.wait(),10)
        sql(f"update worry_sessions set created_at=now()-interval '168 hours',expires_at=now() where id='{sid}'")
        ai.release.set()
        assert (await task).status_code==404
        assert sql(f"select count(*) from worry_messages where session_id='{sid}'")=='2'


async def test_exact_character_boundary_and_retention_not_extended(clean_db):
    async with api() as (c,_,ai):
        original=(await start(c)).json(); sid=original['id']
        for i in range(3): assert (await send(c,sid,'x'*2000,f'm{i}')).status_code==200
        current=(await c.get(f'/api/worry/sessions/{sid}')).json()
        remaining=8000-sum(len(m['text']) for m in current['messages'])
        result=await send(c,sid,'x'*remaining,'exact')
        assert result.status_code==200 and sum(len(m['text']) for m in ai.calls[-1]['messages'])==8000
        assert result.json()['expires_at']==original['expires_at']
        assert (await send(c,sid,'x','extra')).json()['error']['code']=='WORRY_HISTORY_LIMIT'
