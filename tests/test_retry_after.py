import httpx
import pytest

from app.errors import AppError


@pytest.mark.anyio
@pytest.mark.parametrize('status', [429, 503])
@pytest.mark.parametrize('header', ['30', 'Wed, 21 Oct 2037 07:28:00 GMT'])
async def test_provider_wait_header_is_exposed_without_provider_body(api_factory, status, header):
    async with api_factory() as (client, _, ai):
        ai.outcomes.append(httpx.Response(status, headers={'Retry-After': header}, text='provider-private'))
        response = await client.post('/api/chat-sessions', json={}, headers={
            'Idempotency-Key': 'rate-limit', 'Origin': 'http://localhost:5500',
        })
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'AI_UNAVAILABLE'
        assert response.headers['retry-after'] == header
        assert 'retry-after' in response.headers['access-control-expose-headers'].lower()
        assert 'provider-private' not in response.text
        assert len(ai.requests) == 1


@pytest.mark.parametrize('header', ['-1', '1.5', '1000000', '30 seconds', '',
                                   'Wed, 99 Oct 2037 07:28:00 GMT', '30\r\nX-Test: value'])
def test_invalid_wait_header_is_not_reflected(header):
    assert AppError(503, 'AI_UNAVAILABLE', '대기', retry_after=header).retry_after is None


def test_cached_failure_wait_decreases_instead_of_restarting(monkeypatch):
    now = [100.0]
    monkeypatch.setattr('app.errors.monotonic', lambda: now[0])
    error = AppError(503, 'AI_UNAVAILABLE', '대기', retry_after='30')
    now[0] += 10.1
    assert error.retry_after == '20'
    now[0] += 20
    assert error.retry_after == '0'
    assert AppError(502, 'AI_BAD_RESPONSE', '오류', retry_after='30').retry_after is None
