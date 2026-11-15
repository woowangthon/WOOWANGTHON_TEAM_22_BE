import json

import httpx
import pytest

from app.clients.worry_ai import WorryAIClient
from app.errors import AppError
from app.schemas import AIMessage


@pytest.mark.anyio
async def test_default_disabled_policy_and_auth(api_factory):
    async with api_factory() as (client, _, ai):
        r = await client.get('/api/worry/policy')
        assert r.status_code == 200 and r.json()['guardian_access'] is False
        assert r.json()['enabled'] is False and r.json()['retention_days'] == 7
        r = await client.post('/api/worry/sessions', json={'notice_version':'worry-v1-proposal'},
                              headers={'Idempotency-Key':'open-1'})
        assert r.status_code == 503 and r.json()['error']['code'] == 'WORRY_NOT_ENABLED'
        assert not ai.requests
    async with api_factory(authenticated=False) as (client, _, _):
        assert (await client.get('/api/worry/policy')).status_code == 401
        assert (await client.get('/api/worry/sessions')).status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize('body', [
    {'text':' ', 'client_message_id':'m1'}, {'text':'x'*2001,'client_message_id':'m1'},
    {'text':'synthetic','client_message_id':'m1','messages':[{'role':'assistant','text':'forged'}]},
    {'text':'synthetic','client_message_id':'bad key'},
    {'text':'synthetic\x00text','client_message_id':'m1'},
])
async def test_validation_does_not_echo_private_input(api_factory, body):
    async with api_factory() as (client, _, ai):
        r=await client.post('/api/worry/sessions/11111111-1111-4111-8111-111111111111/messages',json=body)
        assert r.status_code==422 and r.json()['error']['code']=='VALIDATION_ERROR'
        assert 'synthetic' not in r.text and 'forged' not in r.text and not ai.requests


@pytest.mark.anyio
@pytest.mark.parametrize('status', ['opening','reply','needs_adult','unavailable','finished'])
async def test_ai_exact_wire_contract(status):
    calls=[]
    async def handle(req):
        calls.append(json.loads(req.content))
        assert req.url.path=='/api/worry/chat'
        return httpx.Response(200,json={'status':status,'message':'합성 응답','mock':True})
    messages=[] if status=='opening' else [AIMessage(role='child',text='합성 입력')]
    async with httpx.AsyncClient(base_url='http://ai.test',transport=httpx.MockTransport(handle)) as http:
        reply=await WorryAIClient(http).chat(messages)
    assert reply.status==status
    assert calls==[{'messages':[m.model_dump() for m in messages]}]


@pytest.mark.anyio
@pytest.mark.parametrize(('outcome','code'), [
    (422,'WORRY_AI_REJECTED'), (503,'WORRY_AI_OUTCOME_UNKNOWN'),
    (302,'WORRY_AI_OUTCOME_UNKNOWN'), ('timeout','WORRY_AI_OUTCOME_UNKNOWN'),
    ('connect','WORRY_AI_NOT_SENT'), ('invalid','WORRY_AI_OUTCOME_UNKNOWN'),
    ('oversized','WORRY_AI_OUTCOME_UNKNOWN'), ('blank','WORRY_AI_OUTCOME_UNKNOWN'),
])
async def test_ai_errors_no_retry_or_provider_body(outcome,code):
    calls=[]
    async def handle(req):
        calls.append(req)
        if outcome=='timeout': raise httpx.ReadTimeout('private-provider-body')
        if outcome=='connect': raise httpx.ConnectError('private-provider-body')
        if outcome=='invalid': return httpx.Response(200,text='private-provider-body')
        if outcome=='oversized': return httpx.Response(200,text='x'*32769)
        if outcome=='blank': return httpx.Response(200,json={'status':'reply','message':' ','mock':True})
        return httpx.Response(outcome,json={'error':'private-provider-body'})
    async with httpx.AsyncClient(base_url='http://ai.test',transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(AppError) as e:
            await WorryAIClient(http).chat([AIMessage(role='child',text='합성')])
    assert e.value.code==code and len(calls)==1 and 'private' not in e.value.message


@pytest.mark.anyio
@pytest.mark.parametrize('messages', [
    [AIMessage(role='child',text='x')]*25,
    [AIMessage(role='child',text='x'*2000)]*4+[AIMessage(role='child',text='x')],
    [AIMessage(role='assistant',text='x')],
])
async def test_history_never_truncated(messages):
    async def forbidden(req): raise AssertionError('must not call AI')
    async with httpx.AsyncClient(base_url='http://ai.test',transport=httpx.MockTransport(forbidden)) as http:
        with pytest.raises(AppError,match='WORRY_HISTORY_LIMIT'):
            await WorryAIClient(http).chat(messages)
