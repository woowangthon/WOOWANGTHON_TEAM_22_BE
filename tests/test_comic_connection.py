import pytest

from app.comic_schemas import ComicPlanRequest
from app.errors import AppError
from tests.conftest import MEMBER_A
from tests.fake_comics import comic_api, prepared, image_for


class VisualAI:
    def __init__(self, plan, outcome=None):
        self.value, self.outcome, self.calls = plan, outcome, []

    async def plan(self, text, *, input_confirmed):
        assert input_confirmed and text.strip() == self.value.diary_text
        self.calls.append('plan')
        return self.value

    async def render(self, plan, scene, *, scene_confirmed):
        assert scene_confirmed
        self.calls.append(scene.scene_id)
        if self.outcome:
            raise AppError(504, self.outcome, 'synthetic')
        return image_for(plan, scene)


@pytest.mark.anyio
async def test_plan_api_explicit_confirmation_and_default_generation_gate():
    async with comic_api() as (client, service, db, storage, _):
        _, _, sid, body, plan, _ = await prepared(client, service)
        service.ai = ai = VisualAI(plan)
        payload = dict(history_revision=body.history_revision, draft_id=body.draft_id)
        url = f'/api/chat-sessions/{sid}/comic-plans'
        assert (await client.post(url, json=payload)).status_code == 409
        assert not ai.calls
        payload['input_confirmed'] = True
        r = await client.post(url, json=payload)
        assert r.status_code == 200 and r.json() == plan.model_dump()
        body = body.model_copy(update=dict(input_confirmed=True, scene_confirmed=True, confirmed_plan=plan))
        r = await client.post(f'/api/chat-sessions/{sid}/comic-previews', json=body.model_dump())
        assert r.status_code == 503 and r.json()['error']['code'] == 'COMIC_PREVIEW_NOT_ENABLED'
        assert ai.calls == ['plan'] and not db.previews and not storage.objects


@pytest.mark.anyio
@pytest.mark.parametrize('case', ['owner', 'revision', 'text', 'input', 'scene'])
async def test_execution_refuses_unconfirmed_or_mismatched_plan(case):
    async with comic_api() as (client, service, db, storage, _):
        _, _, sid, body, plan, _ = await prepared(client, service)
        service.ai = ai = VisualAI(plan)
        body = body.model_copy(update=dict(input_confirmed=True, scene_confirmed=True, confirmed_plan=plan))
        owner = MEMBER_A
        if case == 'owner':
            from tests.conftest import MEMBER_B
            owner = MEMBER_B
        if case == 'revision': body = body.model_copy(update={'history_revision':body.history_revision + 1})
        if case == 'text': body = body.model_copy(update={'confirmed_plan':plan.model_copy(update={'diary_text':'다른 원문'})})
        if case == 'input': body = body.model_copy(update={'input_confirmed':False})
        if case == 'scene': body = body.model_copy(update={'scene_confirmed':False})
        with pytest.raises(AppError):
            await service.render_confirmed(owner, sid, body)
        assert not ai.calls and not db.previews and not storage.objects


@pytest.mark.anyio
@pytest.mark.parametrize('outcome', [None, 'COMIC_RESULT_UNKNOWN', 'COMIC_AI_BAD_RESPONSE'])
async def test_execution_reuses_repeated_panels_and_stops_after_unknown(outcome):
    async with comic_api() as (client, service, db, storage, _):
        _, _, sid, body, plan, _ = await prepared(client, service, '공놀이를 했다. 책을 읽었다.')
        service.ai = ai = VisualAI(plan, outcome)
        body = body.model_copy(update=dict(input_confirmed=True, scene_confirmed=True, confirmed_plan=plan))
        saved = await service.render_confirmed(MEMBER_A, sid, body)
        response = await service.response(saved, 'Bearer fake-member-a')
        assert len(saved.panels) == 4
        assert len(ai.calls) == (1 if outcome else 2)
        assert len(storage.objects) == (0 if outcome else 2)
        assert response.status == ('failed' if outcome else 'ready')
        assert response.error_code == ('COMIC_RESULT_UNKNOWN' if outcome else None)
