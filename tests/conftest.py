import pytest


@pytest.fixture
def anyio_backend():
    # httpx가 설치하는 AnyIO pytest 플러그인을 사용하되 asyncio로만 실행합니다.
    return "asyncio"
