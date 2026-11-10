from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas import RequestKey, StrictModel

ItemId = RequestKey
Coordinate = Annotated[int, Field(ge=0, le=1440)]
Category = Literal["furniture", "decoration", "theme"]
AssetKey = Annotated[str, Field(min_length=1, max_length=256)]


class RewardSyncRequest(StrictModel):
    pass


class PurchaseRequest(StrictModel):
    client_request_id: RequestKey
    item_id: ItemId


class PlacementRequest(StrictModel):
    x: Coordinate
    y: Annotated[int, Field(ge=0, le=900)]


class ThemeRequest(StrictModel):
    item_id: ItemId | None


class WaterRequest(StrictModel):
    client_request_id: RequestKey


class Wallet(StrictModel):
    coins: int = Field(ge=0)
    water_drops: int = Field(ge=0)


class Theme(StrictModel):
    item_id: ItemId | None
    background_asset_key: AssetKey
    diary_cover_asset_key: AssetKey


class Tree(StrictModel):
    stage: int = Field(ge=0, le=7)
    max_stage: Literal[7]
    days_watered: int = Field(ge=0, le=7)
    watered_today: bool
    asset_key: AssetKey

    @model_validator(mode="after")
    def cumulative_growth(self):
        if self.days_watered != self.stage:
            raise ValueError("누적 물 주기와 성장 단계 불일치")
        return self


class Room(StrictModel):
    width: Literal[1440]
    height: Literal[900]
    theme: Theme


class Position(StrictModel):
    x: Coordinate
    y: Annotated[int, Field(ge=0, le=900)]


class InventoryItem(StrictModel):
    item_id: ItemId
    name: str
    category: Category
    asset_key: AssetKey | None
    width: int | None
    height: int | None
    position: Position | None
    refundable: bool
    refund_coins: int | None = Field(ge=0)


class RoomResponse(StrictModel):
    as_of_date: date
    wallet: Wallet
    room: Room
    tree: Tree
    inventory: list[InventoryItem]


class ShopItem(StrictModel):
    item_id: ItemId
    name: str
    category: Category
    price_coins: int = Field(ge=0)
    asset_key: AssetKey | None
    width: int | None
    height: int | None
    background_asset_key: AssetKey | None
    diary_cover_asset_key: AssetKey | None
    adult_tree_asset_key: AssetKey | None
    owned: bool


class ShopResponse(StrictModel):
    items: list[ShopItem]


class PurchaseResponse(StrictModel):
    item_id: ItemId
    owned: Literal[True]
    wallet: Wallet






class PlacementResponse(StrictModel):
    item_id: ItemId
    position: Position


class ThemeResponse(StrictModel):
    theme: Theme
    tree: Tree


class WaterResponse(StrictModel):
    watered_on: date
    wallet: Wallet
    tree: Tree


class DiaryReward(StrictModel):
    diary_date: date
    coins: Literal[30]
    water_drops: Literal[1]


class RewardSyncResponse(StrictModel):
    new_rewards: list[DiaryReward]
    wallet: Wallet
