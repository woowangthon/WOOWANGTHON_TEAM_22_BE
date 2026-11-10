import asyncio
from uuid import UUID

import httpx

from app.errors import AppError


DOMAIN_ERRORS = {
    "ITEM_NOT_REFUNDABLE": (422, "환불할 수 없는 상품이에요."),
    "PURCHASE_ALREADY_REFUNDED": (409, "이미 환불된 구매 요청이에요. 재구매에는 새 요청 ID가 필요해요."),
    "INSUFFICIENT_COINS": (409, "코인이 부족해요."),
    "INSUFFICIENT_WATER": (409, "물방울이 부족해요."),
    "ITEM_ALREADY_OWNED": (409, "이미 보유한 상품이에요."),
    "ITEM_NOT_OWNED": (409, "보유한 상품만 사용할 수 있어요."),
    "ITEM_NOT_PLACEABLE": (422, "방에 배치할 수 없는 상품이에요."),
    "ITEM_NOT_FOUND": (404, "상품을 찾을 수 없어요."),
    "ROOM_POSITION_INVALID": (422, "상품 크기와 방 안의 위치를 확인해 주세요."),
    "TREE_ALREADY_WATERED_TODAY": (409, "오늘은 이미 물을 주었어요."),
    "TREE_FULL_GROWN": (409, "나무가 모두 자랐어요."),
    "IDEMPOTENCY_CONFLICT": (409, "같은 요청 ID의 내용이 달라요."),
}


def room_storage_unavailable() -> AppError:
    return AppError(503, "ROOM_STORAGE_UNAVAILABLE", "방 저장소를 사용할 수 없어요.", True)


class RoomRepository:
    """회원 검증 뒤 서버 키 전용 RPC 하나로 읽기/원자적 명령을 실행한다."""

    def __init__(self, writer: httpx.AsyncClient | None, timeout_seconds: float):
        self.writer = writer
        self.timeout_seconds = timeout_seconds

    async def command(self, member_id: UUID, action: str, payload: dict):
        if self.writer is None:
            raise room_storage_unavailable()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.writer.post(
                    "/rest/v1/rpc/member_room_command", json={
                        "p_member_id": str(member_id), "p_action": action,
                        "p_payload": payload,
                    }, timeout=self.timeout_seconds,
                )
        except (httpx.HTTPError, TimeoutError):
            # 응답 유실 시 자동 재시도하지 않는다. 클라이언트는 같은 요청 ID로 복구한다.
            raise room_storage_unavailable() from None
        if not 200 <= response.status_code < 300:
            try:
                error = response.json()
            except ValueError:
                error = None
            if isinstance(error, dict):
                code = error.get("details")
                if (isinstance(code, str) and code in DOMAIN_ERRORS
                        and error.get("code") == f"PT{DOMAIN_ERRORS[code][0]}"
                        and response.status_code == DOMAIN_ERRORS[code][0]):
                    status, message = DOMAIN_ERRORS[code]
                    raise AppError(status, code, message)
            raise room_storage_unavailable()
        try:
            return response.json()
        except ValueError:
            raise room_storage_unavailable() from None
