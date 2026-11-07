r"""명시적으로 실행하는 로컬 통합 검증: BE ASGI → 기존 AI mock 실제 HTTP.

BE 루트에서 .\.venv\Scripts\python.exe -m scripts.verify_mock_diary 로 실행합니다.
AI 서버를 시작하거나 수정하지 않으며 /api/status가 mock일 때만 생성 API를 호출합니다.
"""
import asyncio
import json
from uuid import uuid4

import httpx

from app.config import Settings
from app.main import create_app

AI_URL = "http://127.0.0.1:8002"


def fake_auth(request):
    # 로컬 mock 검증 전용 가짜 회원
    assert request.url.path == "/auth/v1/user"
    assert request.headers["authorization"] == "Bearer fake-mock-member"
    return httpx.Response(200, json={
        "id": "33333333-3333-4333-8333-333333333333", "role": "authenticated",
        "aud": "authenticated", "is_anonymous": False,
    })


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append((request.url.path, json.loads(request.content)))
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


async def main():
    async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as probe:
        try:
            status = await probe.get(f"{AI_URL}/api/status")
            status.raise_for_status()
            if status.json().get("mode") != "mock":
                raise SystemExit("AI가 mock 모드가 아니므로 생성 요청 없이 종료합니다.")
        except (httpx.HTTPError, ValueError) as error:
            raise SystemExit("기존 AI의 mock 상태를 확인하지 못해 생성 요청 없이 종료합니다.") from error

    transport = RecordingTransport()
    app = create_app(Settings(
        ai_base_url=AI_URL, supabase_publishable_key="sb_publishable_fake_mock_key",
    ), transport, httpx.MockTransport(fake_auth))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://be.test",
        headers={"Authorization": "Bearer fake-mock-member"},
    ) as be:
        start = await be.post("/api/chat-sessions", json={}, headers={
            "Idempotency-Key": f"verify-{uuid4().hex}",
        })
        assert start.status_code == 201, start.text
        first = start.json()
        assert first["mock"] is True
        session_id = first["session_id"]
        path = f"/api/chat-sessions/{session_id}/diary-draft"
        no_child = await be.post(path, json={"client_request_id": "empty", "history_revision": 0})
        assert no_child.status_code == 422 and no_child.json()["error"]["code"] == "NO_CHILD_MESSAGES"
        history = [{"role": "assistant", "text": first["reply"]["text"]}]
        for index, text in enumerate(["가상 인물이 공놀이한 이야기야.", "가상 인물은 즐거웠다고 했어."], 1):
            reply = await be.post(f"/api/chat-sessions/{session_id}/messages", json={
                "client_message_id": f"turn-{index}", "text": text,
            })
            assert reply.status_code == 200, reply.text
            assert reply.json()["mock"] is True and reply.json()["history_revision"] == index
            history.extend([{"role": "child", "text": text},
                            {"role": "assistant", "text": reply.json()["reply"]["text"]}])
        request = {"client_request_id": "draft-1", "history_revision": 2}
        response = await be.post(path, json=request)
        assert response.status_code == 200, response.text
        result = response.json()
        draft = result["draft"]
        assert result["history_revision"] == 2 and draft["status"] == "draft"
        assert draft["mock"] is draft["fallback"] is draft["requires_confirmation"] is True
        assert draft["sources"] == [{"id": i, "text": item["text"]}
                                    for i, item in enumerate(history) if item["role"] == "child"]
        assert draft["text"] == "\n".join(source["text"] for source in draft["sources"])
        assert all(page["imageUrl"] is None and set(page["source_ids"]) <= {1, 3}
                   for page in draft["pages"])
        assert (await be.post(path, json=request)).json() == result
        assert [path for path, _ in transport.requests] == ["/api/chat"] * 3 + ["/api/diary"]
        assert transport.requests[-1][1] == {"messages": history}
        session = app.state.chat_service.store.sessions[session_id]
        assert [item.model_dump() for item in session.messages] == history
        assert session.history_revision == 2 and session.busy is None
        print(json.dumps({"result": "passed", "ai_mode": "mock", "chat_http_calls": 3,
                          "diary_http_calls": 1, "history_messages": len(history),
                          "source_ids": [source["id"] for source in draft["sources"]],
                          "draft_replay_same": True, "no_child_rejected": True}))


if __name__ == "__main__":
    asyncio.run(main())
