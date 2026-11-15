from uuid import uuid4

from app.errors import AppError
from app.worry_schemas import WorryList


class WorryService:
    def __init__(self, repository, ai, enabled=False):
        self.repository, self.ai, self.enabled = repository, ai, enabled

    def require_enabled(self):
        if not self.enabled:
            raise AppError(503, "WORRY_NOT_ENABLED", "고민 대화는 아직 운영 준비 중이에요.")

    async def start(self, owner, key, body):
        self.require_enabled()
        result = await self.repository.command(owner, "start", key=key,
                                               notice_version=body.notice_version)
        return await self._execute(owner, result)

    async def send(self, owner, session_id, body):
        self.require_enabled()
        result = await self.repository.command(owner, "admit", session_id,
                                               key=body.client_message_id, text=body.text)
        return await self._execute(owner, result)

    async def _execute(self, owner, result):
        session = self.repository.snapshot(result)
        # admission의 응답을 받은 최초 요청만 실행한다. 재시작/중복 요청은 원장 조회만 한다.
        if result.get("execute") is not True:
            return session
        request_id, token = result["request_id"], str(uuid4())
        dispatched = await self.repository.command(owner, "dispatch", session.id,
                                                   request_id=request_id, token=token)
        if dispatched.get("execute") is not True:
            return self.repository.snapshot(dispatched)
        session = self.repository.snapshot(dispatched)
        try:
            reply = await self.ai.chat(session.messages)
            args = {"reply": reply.model_dump(), "error_code": None}
        except AppError as error:
            args = {"reply": None, "error_code": error.code}
        # 전송 후 저장 실패 시 재호출하지 않는다. GET/동일 ID 조회로 원장을 복구한다.
        saved = await self.repository.command(owner, "complete", session.id,
                                              request_id=request_id, token=token, **args)
        return self.repository.snapshot(saved)

    async def get(self, owner, session_id):
        self.require_enabled()
        return self.repository.snapshot(await self.repository.command(owner, "get", session_id))

    async def finish(self, owner, session_id):
        self.require_enabled()
        return self.repository.snapshot(await self.repository.command(owner, "finish", session_id))

    async def list(self, owner, limit, offset):
        self.require_enabled()
        return WorryList(sessions=await self.repository.list(owner, limit, offset), limit=limit, offset=offset)
