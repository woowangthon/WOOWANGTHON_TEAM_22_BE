import asyncio

import httpx


class RequestRecoveryRepository:
    """기존 service_role SELECT 권한만 사용한다. RPC/쓰기/자동 재시도 없음."""

    def __init__(self, writer, timeout_seconds):
        self.writer = writer
        self.timeout_seconds = timeout_seconds

    async def receipt(self, table, columns, owner_column, owner_id, key, unavailable):
        if self.writer is None:
            raise unavailable()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.writer.get(f"/rest/v1/{table}", params={
                    "select": columns, owner_column: f"eq.{owner_id}",
                    "client_request_id": f"eq.{key}", "limit": "2",
                })
        except (httpx.HTTPError, TimeoutError):
            raise unavailable() from None
        # 부분 응답/권한 오류/미설치 테이블을 영수증 부재로 바꾸지 않는다.
        if response.status_code != 200:
            raise unavailable()
        try:
            rows = response.json()
            if not isinstance(rows, list) or len(rows) > 1:
                raise ValueError
            if not rows:
                return None
            if not isinstance(rows[0], dict):
                raise ValueError
            return rows[0]
        except ValueError:
            raise unavailable() from None
