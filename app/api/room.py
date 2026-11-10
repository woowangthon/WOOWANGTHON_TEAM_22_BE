from fastapi import APIRouter, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from typing import Annotated

from app.auth import MemberId
from app.errors import AppError
from app.room_schemas import (
    ItemId, PlacementRequest, PlacementResponse, PurchaseRequest, PurchaseResponse,
    RewardSyncRequest, RewardSyncResponse, RoomResponse, ShopResponse,
    ThemeRequest, )

class RoomRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # v0.3.1은 누락/소수/bool을 포함한 좌표 입력 오류를 같은 코드로 정의한다.
                if request.method == "PUT" and "/room/placements/" in request.url.path:
                    raise AppError(422, "ROOM_POSITION_INVALID", "정수 좌표와 방 안의 위치를 확인해 주세요.") from None
                raise
        return handle


router = APIRouter(prefix="/api", tags=["room / rewards v0.3.1"], route_class=RoomRoute)
ItemPath = Annotated[ItemId, Path()]


@router.get("/me/room", response_model=RoomResponse)
async def get_room(request: Request, member_id: MemberId):
    return await request.app.state.room_service.execute(member_id, "get_room")


@router.post("/me/rewards/sync", response_model=RewardSyncResponse)
async def sync_rewards(body: RewardSyncRequest, request: Request, member_id: MemberId):
    return await request.app.state.room_service.execute(member_id, "sync_rewards", body)


@router.get("/shop/items", response_model=ShopResponse)
async def shop_items(request: Request, member_id: MemberId):
    return await request.app.state.room_service.execute(member_id, "list_items")


@router.post("/shop/purchases", response_model=PurchaseResponse)
async def purchase(body: PurchaseRequest, request: Request, member_id: MemberId):
    return await request.app.state.room_service.execute(member_id, "purchase", body)




@router.put("/me/room/placements/{item_id}", response_model=PlacementResponse)
async def place(item_id: ItemPath, body: PlacementRequest, request: Request, member_id: MemberId):
    return await request.app.state.room_service.execute(member_id, "place", _Placement(item_id=item_id, **body.model_dump()))


@router.delete("/me/room/placements/{item_id}", status_code=204)
async def remove_placement(item_id: ItemPath, request: Request, member_id: MemberId):
    await request.app.state.room_service.execute(member_id, "remove_placement", _Item(item_id=item_id))
    return Response(status_code=204)






class _Item(ThemeRequest):
    item_id: ItemId


class _Placement(PlacementRequest):
    item_id: ItemId
