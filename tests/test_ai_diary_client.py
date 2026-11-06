import asyncio
import copy
import json

import httpx
import pytest

from app.clients.ai import AIClient
from app.errors import AppError
from app.schemas import AIMessage

pytestmark = pytest.mark.anyio

MESSAGES = [
    AIMessage(role="assistant", text="가상 첫 질문"),
    AIMessage(role="child", text="  가상 공놀이\n이야기  "),
    AIMessage(role="assistant", text="가상 다음 질문"),
    AIMessage(role="child", text="가상으로 즐거웠어."),
    AIMessage(role="assistant", text="가상 마지막 질문"),
]
GOOD = {
    "text": "  가상 공놀이\n이야기  \n가상으로 즐거웠어.",
    "pages": [{"text": "  가상 공놀이\n이야기  \n가상으로 즐거웠어.",
               "source_ids": [1, 3], "imageUrl": None}],
    "sources": [{"id": 1, "text": "  가상 공놀이\n이야기  "},
                {"id": 3, "text": "가상으로 즐거웠어."}],
    "fallback": True, "requires_confirmation": True, "mock": True,
}


async def call_diary(outcome, timeout=1):
    requests = []

    async def handler(request):
        requests.append(request)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return await outcome(request)
        return outcome

    async with httpx.AsyncClient(
        base_url="http://ai.test", transport=httpx.MockTransport(handler),
        follow_redirects=False, trust_env=False,
    ) as http:
        try:
            return await AIClient(http, timeout).diary(MESSAGES), requests
        except AppError as error:
            assert len(requests) == 1  # AI 자체 재시도 없음
            raise error


async def test_diary_full_history_only_allowed_fields_and_exact_response():
    result, requests = await call_diary(httpx.Response(200, json=GOOD))
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://ai.test/api/diary"
    assert json.loads(requests[0].content) == {
        "messages": [item.model_dump() for item in MESSAGES],
    }
    assert result.model_dump() == GOOD


def invalid_responses():
    for field in GOOD:
        body = copy.deepcopy(GOOD)
        del body[field]
        yield f"missing-{field}", body
    for field, values in {
        "text": ["", " \n", 1, None, "\ud800"],
        "pages": [[], {}, None],
        "sources": [[], {}, None],
        "fallback": [1, "false", None],
        "requires_confirmation": [False, 1, "true", None],
        "mock": [1, "true", None],
    }.items():
        for index, value in enumerate(values):
            yield f"{field}-{index}", {**GOOD, field: value}
    for field, values in {
        "text": ["", " \n", 1, None, "다른 일기"],
        "source_ids": [[], [0], [-1], [5], [1, 1], [True], ["1"], ["msg_be"], None],
        "imageUrl": ["https://unexpected.test/image.png", 1],
    }.items():
        for index, value in enumerate(values):
            body = copy.deepcopy(GOOD)
            body["pages"][0][field] = value
            yield f"page-{field}-{index}", body
    for page_field in GOOD["pages"][0]:
        body = copy.deepcopy(GOOD)
        del body["pages"][0][page_field]
        yield f"missing-page-{page_field}", body
    for index, sources in enumerate([
        [{"id": 1, "text": "수정된 원문"}, GOOD["sources"][1]],
        [GOOD["sources"][0]],
        GOOD["sources"][::-1],
        [*GOOD["sources"], GOOD["sources"][0]],
        [{"id": 0, "text": MESSAGES[0].text}, *GOOD["sources"]],
        [{"id": True, "text": MESSAGES[1].text}, GOOD["sources"][1]],
        [{"text": MESSAGES[1].text}, GOOD["sources"][1]],
        [{"id": 1}, GOOD["sources"][1]],
    ]):
        yield f"sources-content-{index}", {**GOOD, "sources": sources}
    yield "extra", {**GOOD, "error": "secret"}
    yield "extra-page", {**GOOD, "pages": [{**GOOD["pages"][0], "extra": 1}]}
    yield "extra-source", {**GOOD, "sources": [{**GOOD["sources"][0], "extra": 1}]}


@pytest.mark.parametrize("name,body", list(invalid_responses()), ids=lambda x: x if isinstance(x, str) else "body")
async def test_invalid_diary_response_rejected(name, body):
    # 잘못된 surrogate도 JSON escape 상태로 AI 경계를 통과시킵니다.
    response = httpx.Response(200, content=json.dumps(body).encode())
    with pytest.raises(AppError) as caught:
        await call_diary(response)
    assert caught.value.status == 502
    assert caught.value.code == "AI_BAD_RESPONSE"
    assert caught.value.retryable is False


@pytest.mark.parametrize("outcome,status,code,retryable", [
    (httpx.ConnectError("secret"), 503, "AI_CONNECTION_FAILED", True),
    (httpx.ConnectTimeout("secret"), 503, "AI_CONNECTION_FAILED", True),
    (httpx.PoolTimeout("secret"), 503, "AI_CONNECTION_FAILED", True),
    (httpx.ReadTimeout("secret"), 504, "AI_OUTCOME_UNKNOWN", False),
    (httpx.WriteTimeout("secret"), 504, "AI_OUTCOME_UNKNOWN", False),
    (httpx.ReadError("secret"), 502, "AI_OUTCOME_UNKNOWN", False),
    (httpx.RemoteProtocolError("secret"), 502, "AI_OUTCOME_UNKNOWN", False),
    (httpx.Response(422, json={"error": "secret"}), 502, "AI_REQUEST_REJECTED", False),
    (httpx.Response(429, json={"error": "secret"}), 503, "AI_UNAVAILABLE", False),
    (httpx.Response(503, json={"error": "secret"}), 503, "AI_UNAVAILABLE", False),
    (httpx.Response(500, text="secret"), 502, "AI_BAD_RESPONSE", False),
    (httpx.Response(302, headers={"Location": "http://elsewhere.test"}), 502, "AI_BAD_RESPONSE", False),
    (httpx.Response(204), 502, "AI_BAD_RESPONSE", False),
    (httpx.Response(200, text="not-json-secret"), 502, "AI_BAD_RESPONSE", False),
    (httpx.Response(200, json=[]), 502, "AI_BAD_RESPONSE", False),
])
async def test_diary_transport_errors(outcome, status, code, retryable):
    with pytest.raises(AppError) as caught:
        await call_diary(outcome)
    error = caught.value
    assert (error.status, error.code, error.retryable) == (status, code, retryable)
    assert "secret" not in error.message


async def test_diary_total_deadline():
    async def blocked(request):
        await asyncio.Event().wait()

    with pytest.raises(AppError) as caught:
        await call_diary(blocked, timeout=0.02)
    assert (caught.value.status, caught.value.code) == (504, "AI_OUTCOME_UNKNOWN")


async def test_reviewed_diary_preserves_ai_text_and_page_grouping():
    # AI 정상 생성은 text는 공백, 페이지 내부 문장들은 줄바꿈으로 연결합니다.
    body = {**GOOD, "text": "가상으로 공놀이했다. 즐거웠다.", "fallback": False, "mock": False,
            "pages": [{"text": "가상으로 공놀이했다.\n즐거웠다.",
                       "source_ids": [1, 3], "imageUrl": None}]}
    result, _ = await call_diary(httpx.Response(200, json=body))
    assert result.model_dump() == body
