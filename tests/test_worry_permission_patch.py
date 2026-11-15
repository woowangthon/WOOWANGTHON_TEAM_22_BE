"""v2 증분 SQL의 실제 로컬 PostgreSQL 검증. 운영 연결은 지원하지 않는다."""
import json

import httpx
import pytest

from tests.conftest import FakeAuth, MEMBER_A, MEMBER_B
from tests.test_worry_postgres import (BE, ROOT, api, clean_db, schema, send, sql, start)

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(not ROOT, reason='isolated PostgreSQL not configured')]
PATCH = (BE/'docs/sql/worry_open_access_patch_20261113_v2.sql').read_text(encoding='utf-8')
ROLLBACK = (BE/'docs/sql/worry_open_access_rollback_20261113_v2.sql').read_text(encoding='utf-8')
SIGNATURE = 'public.worry_command(uuid,text,uuid,jsonb)'


def definition():
    return sql(f"select pg_get_functiondef('{SIGNATURE}'::regprocedure)")


def preserved_state():
    return sql("select jsonb_build_object("
        "'sessions',(select jsonb_agg(to_jsonb(s) order by id) from worry_sessions s),"
        "'messages',(select jsonb_agg(to_jsonb(m) order by session_id,position) from worry_messages m),"
        "'requests',(select jsonb_agg(to_jsonb(r) order by id) from worry_requests r),"
        "'users',(select jsonb_agg(to_jsonb(u) order by id) from auth.users u),"
        "'grants',(select jsonb_agg(to_jsonb(g) order by user_id) from worry_access_grants g),"
        "'cron',(select jsonb_agg(to_jsonb(j) order by jobid) from cron.job j),"
        "'tables',(select jsonb_agg(jsonb_build_array(oid,relacl,relrowsecurity) order by oid) "
        "from pg_class where relnamespace in ('public'::regnamespace,'auth'::regnamespace)),"
        "'constraints',(select jsonb_agg(jsonb_build_array(oid,pg_get_constraintdef(oid)) order by oid) "
        "from pg_constraint where connamespace='public'::regnamespace),"
        "'purge',pg_get_functiondef('public.purge_expired_worry_sessions(integer)'::regprocedure),"
        "'snapshot',pg_get_functiondef('public.worry_snapshot(uuid)'::regprocedure),"
        "'function',(select jsonb_build_array(oid,proowner,proacl,prosecdef,proconfig) from pg_proc "
        f"where oid='{SIGNATURE}'::regprocedure))")


async def test_no_auth_select_and_only_verified_uuid_reaches_all_rpc_actions(clean_db):
    with pytest.raises(AssertionError, match='permission denied'):
        sql('set role service_role; select id from auth.users;')
    auth = FakeAuth()
    async with api(auth=auth) as (client, db, ai):
        # 토큰 이름이나 query/header 대신 Auth 응답의 UUID를 사용하는지 확인한다.
        auth.outcomes.append(httpx.Response(200, json={
            'id': str(MEMBER_B), 'is_anonymous': False, 'role': 'authenticated', 'aud': 'authenticated'}))
        opened = await client.post(f'/api/worry/sessions?p_owner={MEMBER_A}', json={},
            headers={'Idempotency-Key':'verified', 'X-User-Id': str(MEMBER_A)})
        assert opened.status_code == 201, opened.text
        sid = opened.json()['id']
        assert all(call['p_owner'] == str(MEMBER_B) for call in db.calls)
        client.headers['Authorization'] = 'Bearer fake-member-b'
        assert (await send(client, sid)).status_code == 200
        assert (await client.get('/api/worry/sessions')).status_code == 200
        assert (await client.get(f'/api/worry/sessions/{sid}')).status_code == 200
        assert (await client.post(f'/api/worry/sessions/{sid}/finish')).status_code == 200
        assert all(call['p_owner'] == str(MEMBER_B) for call in db.calls)
        assert {call['p_action'] for call in db.calls} == {
            'start','admit','dispatch','complete','get','list','finish'}
        assert len(auth.requests) == 5  # HTTP 요청마다 재인증, 내부 RPC에 클라이언트 Bearer 전달 없음.


async def test_auth_failure_on_existing_session_blocks_every_route_before_rpc(clean_db):
    auth = FakeAuth()
    async with api(auth=auth) as (client, db, ai):
        sid = (await start(client)).json()['id']
        db.calls.clear()
        before = len(ai.calls)
        for method, path, body in [
            ('GET','/api/worry/policy',None), ('GET','/api/worry/sessions',None),
            ('GET',f'/api/worry/sessions/{sid}',None),
            ('POST',f'/api/worry/sessions/{sid}/messages',{'text':'synthetic','client_message_id':'m'}),
            ('POST',f'/api/worry/sessions/{sid}/finish',None),
        ]:
            auth.outcomes.append(httpx.Response(401, json={'message':'synthetic deleted or expired user'}))
            assert (await client.request(method,path,json=body)).status_code == 401
        assert not db.calls and len(ai.calls) == before


async def test_patch_repeat_rollback_reapply_preserves_data_acl_constraints_and_cron(clean_db):
    async with api() as (client, db, ai):
        sid = (await start(client)).json()['id']
        await send(client, sid)
    before = preserved_state()
    sql(PATCH)
    assert preserved_state() == before
    sql(ROLLBACK)
    assert preserved_state() == before
    assert sql(f"select md5(replace(prosrc,chr(13),'')) from pg_proc where oid='{SIGNATURE}'::regprocedure") == '78308b5c52f3488896a078085b6679ec'
    sql(PATCH)
    assert preserved_state() == before
    async with api() as (client, db, ai):
        assert (await client.get(f'/api/worry/sessions/{sid}')).status_code == 200
        assert (await send(client, sid, key='resume')).status_code == 200


async def test_unknown_definition_fails_without_overwrite(clean_db):
    original = definition()
    changed = original.replace('WORRY_MEMBER_REQUIRED','SYNTHETIC_DRIFT')
    sql(changed)
    try:
        with pytest.raises(AssertionError, match='Unexpected worry_command'):
            sql(PATCH)
        assert 'SYNTHETIC_DRIFT' in definition()
    finally:
        sql(original)


@pytest.mark.parametrize('role', ['anon','authenticated'])
async def test_unexpected_browser_execute_acl_blocks_patch(clean_db, role):
    original = definition()
    sql(f'grant execute on function {SIGNATURE} to {role}')
    try:
        with pytest.raises(AssertionError, match='Unexpected RPC ACL'):
            sql(PATCH)
        assert definition() == original
    finally:
        sql(f'revoke execute on function {SIGNATURE} from {role}')


@pytest.mark.parametrize('owner', ['null', "'00000000-0000-0000-0000-000000000000'"])
async def test_rpc_rejects_missing_or_zero_owner(clean_db, owner):
    result = sql(f"set role service_role; select public.worry_command({owner},'list',null,'{{}}')")
    assert json.loads(result) == {'error':'WORRY_MEMBER_REQUIRED'}


async def test_fk_still_rejects_nonexistent_user_without_auth_read(clean_db):
    with pytest.raises(AssertionError, match='foreign key constraint'):
        sql("set role service_role; select public.worry_command("
            "'33333333-3333-4333-8333-333333333333','start',null,'{\"key\":\"missing\"}')")
    assert sql('select count(*) from worry_sessions') == '0'


async def test_post_install_hash_mismatch_rolls_back_function_replacement(clean_db):
    original = definition()
    before = preserved_state()
    tampered = PATCH.replace('WORRY_MEMBER_REQUIRED','SYNTHETIC_BAD_INSTALL')
    with pytest.raises(AssertionError, match='Installed function hash mismatch'):
        sql(tampered)
    assert definition() == original and preserved_state() == before


async def test_missing_server_execute_blocks_patch(clean_db):
    original = definition()
    sql(f'revoke execute on function {SIGNATURE} from service_role')
    try:
        with pytest.raises(AssertionError, match='Unexpected RPC ACL'):
            sql(PATCH)
        assert definition() == original
    finally:
        sql(f'grant execute on function {SIGNATURE} to service_role')


async def test_disabled_rls_blocks_patch(clean_db):
    original = definition()
    sql('alter table worry_sessions disable row level security')
    try:
        with pytest.raises(AssertionError, match='Worry RLS must remain enabled'):
            sql(PATCH)
        assert definition() == original
    finally:
        sql('alter table worry_sessions enable row level security')
