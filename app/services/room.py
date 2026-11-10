import json
from uuid import UUID

from pydantic import ValidationError

from app.clients.room import RoomRepository, room_storage_unavailable
from app.room_schemas import (
    RefundResponse, PlacementResponse, PurchaseResponse, RewardSyncResponse, RoomResponse,
    ShopResponse, ThemeResponse, WaterResponse,
)


class RoomService:
    def __init__(self, repository: RoomRepository):
        self.repository = repository

    async def execute(self, member_id: UUID, action: str, body=None):
        payload = body.model_dump(mode="json") if body is not None else {}
        raw = await self.repository.command(member_id, action, payload)
        if action == "remove_placement":
            if raw != {}:
                raise room_storage_unavailable()
            return None
        schema = {
            "get_room": RoomResponse, "list_items": ShopResponse,
            "sync_rewards": RewardSyncResponse, "purchase": PurchaseResponse, "refund": RefundResponse,
            "place": PlacementResponse, "theme": ThemeResponse, "water": WaterResponse,
        }[action]
        try:
            # JSON 날짜 문자열은 허용하되 문자열 잔액과 bool 좌표는 거절한다.
            result = schema.model_validate_json(json.dumps(raw))
        except (ValidationError, ValueError, TypeError):
            raise room_storage_unavailable() from None
        return result
