import asyncio

import httpx
import pytest
from app import runtime as factory
from app.main import create_app
from tests.conftest import FakeAuth, TEST_PUBLISHABLE_KEY
from tests.fake_comic_jobs import JobAI, JobDatabase, JobStorage
from tests.test_comic_jobs import request_data, finished
from tests.test_saved_diaries import FAKE_SECRET


def app_with_fakes(monkeypatch, db=None):
    db, storage, ai = db or JobDatabase(), JobStorage(), JobAI()
    monkeypatch.setenv('COMIC_WORKER_ENABLED', '1')
    monkeypatch.setenv('SUPABASE_PUBLISHABLE_KEY', TEST_PUBLISHABLE_KEY)
    monkeypatch.setenv('SUPABASE_SECRET_KEY', FAKE_SECRET)
    monkeypatch.setenv('SESSION_STORE_MODE', 'memory')
    def injected(settings, **kwargs):
        return create_app(settings, httpx.MockTransport(ai), httpx.MockTransport(FakeAuth()),
                          httpx.MockTransport(db), httpx.MockTransport(storage), **kwargs)
    monkeypatch.setattr(factory, 'create_app', injected)
    return factory.create_runtime_app(), db, storage, ai


@pytest.mark.anyio
async def test_enabled_factory_generates_four_stores_polls_and_stops(monkeypatch):
    app, db, storage, ai = app_with_fakes(monkeypatch)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app),
            base_url='http://be.test', headers={'Authorization':'Bearer fake-member-a'}) as client:
        jobs = app.state.comic_service.jobs
        jobs.poll_seconds = .005
        assert (await client.get('/ready')).json() == {'status':'ok','comic_worker':True}
        _, _, sid, body = await request_data(client, app.state.comic_service)
        r = await client.post(f'/api/chat-sessions/{sid}/comic-previews', json=body.model_dump())
        assert r.status_code == 202
        await finished(db)
        result = (await client.get(f'/api/chat-sessions/{sid}/comic-requests/{body.client_request_id}')).json()
        assert result['job_state'] == 'completed' and len(ai.images) == len(storage.objects) == 4
        await jobs.stop()
        assert (await client.get('/ready')).status_code == 503
    assert not jobs.available


@pytest.mark.anyio
async def test_enabled_factory_missing_schema_aborts_startup(monkeypatch):
    db = JobDatabase()
    db.job_failure = 'probe'
    # Probe transport failure represents an unavailable or incompatible DB function.
    app, _, _, _ = app_with_fakes(monkeypatch, db)
    with pytest.raises(RuntimeError, match='startup_probe_failed'):
        async with app.router.lifespan_context(app):
            pytest.fail('must not accept traffic')


def test_default_factory_stays_off_and_invalid_mode_fails(monkeypatch):
    monkeypatch.delenv('COMIC_WORKER_ENABLED', raising=False)
    monkeypatch.setenv('SESSION_STORE_MODE','memory')
    app = factory.create_runtime_app()
    async def run():
        async with app.router.lifespan_context(app):
            assert not app.state.comic_service.jobs.available
    asyncio.run(run())
    monkeypatch.setenv('SESSION_STORE_MODE','supabase')
    with pytest.raises(RuntimeError, match='memory'):
        factory.create_runtime_app()
