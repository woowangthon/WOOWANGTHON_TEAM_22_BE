"""SQL 명령의 모의 모델. PostgreSQL의 잠금/RLS/롤백 검증을 대신하지 않는다."""
import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json

import httpx

from app.clients.room import DOMAIN_ERRORS

FAKE_SECRET = "sb_secret_room_test_only"


def item(item_id="chair", category="furniture", price=30, width=100, height=80):
    theme = category == "theme"
    return {
        "item_id": item_id, "name": "가상 상품 " + item_id, "category": category,
        "price_coins": price, "asset_key": None if theme else f"test/{item_id}",
        "width": None if theme else width, "height": None if theme else height,
        "background_asset_key": f"test/{item_id}/room" if theme else None,
        "diary_cover_asset_key": f"test/{item_id}/cover" if theme else None,
        "adult_tree_asset_key": f"test/{item_id}/adult" if theme else None,
        "is_active": True,
    }


@dataclass
class MemberState:
    coins: int = 0
    water: int = 0
    grants: dict = field(default_factory=dict)
    owned: dict = field(default_factory=dict)
    purchases: dict = field(default_factory=dict)
    refunds: dict = field(default_factory=dict)
    placements: dict = field(default_factory=dict)
    theme: str | None = None
    stage: int = 0
    events: dict = field(default_factory=dict)


class RuleError(Exception):
    pass


class FakeRoomDatabase:
    def __init__(self):
        self.secret = FAKE_SECRET
        self.members = {}
        self.locks = {}
        self.items = {}
        self.diaries = []
        self.legacy_diary_ids = set()
        self.requests = []
        self.now = datetime(2026, 11, 10, 3, tzinfo=timezone.utc)
        self.fail_during = None
        self.lose_response = None

    @property
    def today(self):
        return (self.now + timedelta(hours=9)).date().isoformat()

    def member(self, member_id):
        return self.members.setdefault(str(member_id), MemberState())

    async def __call__(self, request):
        assert request.method == "POST" and request.url.path == "/rest/v1/rpc/member_room_command"
        assert request.headers["apikey"] == self.secret
        assert "authorization" not in request.headers
        params = json.loads(request.content)
        assert set(params) == {"p_member_id", "p_action", "p_payload"}
        self.requests.append(params)
        member_id, action, body = params["p_member_id"], params["p_action"], params["p_payload"]
        lock = self.locks.setdefault(member_id, asyncio.Lock())
        async with lock:
            state = self.member(member_id)
            before = deepcopy(state)
            try:
                await asyncio.sleep(0)
                result = self.apply(state, action, body)
                if self.fail_during == action:
                    raise RuntimeError("fake failure before commit")
            except RuleError as error:
                self.members[member_id] = before
                code = str(error)
                status = DOMAIN_ERRORS[code][0]
                return httpx.Response(status, json={"code": f"PT{status}", "details": code, "message": "internal"})
            except RuntimeError:
                self.members[member_id] = before
                return httpx.Response(500, text="fake internal transaction error")
            if self.lose_response == action:
                self.lose_response = None
                raise httpx.ReadTimeout("fake response loss after commit")
            return httpx.Response(200, json=result)

    def snapshot(self, state):
        theme = self.items.get(state.theme, {})
        return {
            "as_of_date": self.today, "wallet": {"coins": state.coins, "water_drops": state.water},
            "room": {"width": 1440, "height": 900, "theme": {
                "item_id": state.theme, "background_asset_key": theme.get("background_asset_key", "room_default"),
                "diary_cover_asset_key": theme.get("diary_cover_asset_key", "cover_default"),
            }},
            "tree": {"stage": state.stage, "max_stage": 7, "days_watered": state.stage,
                     "watered_today": self.today in state.events.values(),
                     "asset_key": theme.get("adult_tree_asset_key", "tree_adult_default") if state.stage == 7 else f"tree_stage_{state.stage}"},
            "inventory": [{**{k: self.items[key][k] for k in (
                "item_id", "name", "category", "asset_key", "width", "height")},
                "position": state.placements.get(key),
                "refundable": self.items[key]["category"] != "theme",
                "refund_coins": state.purchases[state.owned[key]]["price_paid"] if self.items[key]["category"] != "theme" else None} for key in sorted(state.owned)],
        }

    def apply(self, state, action, body):
        def response():
            return self.snapshot(state)

        if action == "get_room":
            return response()
        if action == "list_items":
            return {"items": [{**{k: v for k, v in value.items() if k != "is_active"},
                               "owned": key in state.owned}
                              for key, value in sorted(self.items.items()) if value["is_active"]]}
        if action == "sync_rewards":
            rewards = []
            owner = next(k for k, value in self.members.items() if value is state)
            first_dates = set()
            for diary in sorted(self.diaries, key=lambda value: value["date"]):
                if diary["member"] != owner or not diary.get("confirmed", True):
                    continue
                if diary["date"] in first_dates:
                    continue
                first_dates.add(diary["date"])
                if (diary["member"] == owner and diary.get("confirmed", True)
                        and diary["id"] not in self.legacy_diary_ids and diary["date"] not in state.grants.values()):
                    state.grants[diary["id"]] = diary["date"]
                    state.coins += 30
                    state.water += 1
                    rewards.append({"diary_date": diary["date"], "coins": 30, "water_drops": 1})
            return {"new_rewards": rewards, "wallet": response()["wallet"]}
        if action == "purchase":
            key = body["item_id"]
            receipt = state.purchases.get(body["client_request_id"])
            old_item = receipt["item_id"] if receipt else None
            if old_item is not None:
                if old_item != key:
                    raise RuleError("IDEMPOTENCY_CONFLICT")
                if receipt["refunded"]:
                    raise RuleError("PURCHASE_ALREADY_REFUNDED")
            else:
                product = self.items.get(key)
                if product is None or not product["is_active"]:
                    raise RuleError("ITEM_NOT_FOUND")
                if key in state.owned:
                    raise RuleError("ITEM_ALREADY_OWNED")
                if state.coins < product["price_coins"]:
                    raise RuleError("INSUFFICIENT_COINS")
                state.coins -= product["price_coins"]
                state.owned[key] = body["client_request_id"]
                state.purchases[body["client_request_id"]] = {"item_id": key, "price_paid": product["price_coins"], "refunded": False}
            return {"item_id": key, "owned": True, "wallet": response()["wallet"]}
        if action == "refund":
            key, request_id = body["item_id"], body["client_request_id"]
            if request_id in state.refunds:
                receipt = state.refunds[request_id]
                if receipt["item_id"] != key:
                    raise RuleError("IDEMPOTENCY_CONFLICT")
                return deepcopy(receipt)
            if key not in state.owned:
                raise RuleError("ITEM_NOT_OWNED")
            if self.items[key]["category"] == "theme":
                raise RuleError("ITEM_NOT_REFUNDABLE")
            purchase = state.purchases[state.owned[key]]
            state.coins += purchase["price_paid"]
            purchase["refunded"] = True
            del state.owned[key]
            state.placements.pop(key, None)
            receipt = {"item_id": key, "refunded_coins": purchase["price_paid"],
                       "owned": False, "wallet": response()["wallet"]}
            state.refunds[request_id] = deepcopy(receipt)
            return receipt
        if action in {"place", "remove_placement", "theme"}:
            key = body["item_id"]
            if key is not None:
                if key not in self.items:
                    raise RuleError("ITEM_NOT_FOUND")
                if key not in state.owned:
                    raise RuleError("ITEM_NOT_OWNED")
            if action == "theme":
                if key is not None and self.items[key]["category"] != "theme":
                    raise RuleError("ITEM_NOT_FOUND")
                state.theme = key
                return {"theme": response()["room"]["theme"], "tree": response()["tree"]}
            product = self.items[key]
            if product["category"] not in {"furniture", "decoration"}:
                raise RuleError("ITEM_NOT_PLACEABLE")
            if action == "remove_placement":
                state.placements.pop(key, None)
                return {}
            if body["x"] + product["width"] > 1440 or body["y"] + product["height"] > 900:
                raise RuleError("ROOM_POSITION_INVALID")
            state.placements[key] = {"x": body["x"], "y": body["y"]}
            return {"item_id": key, "position": state.placements[key]}
        if action == "water":
            key = body["client_request_id"]
            if key not in state.events:
                if state.stage == 7:
                    raise RuleError("TREE_FULL_GROWN")
                if self.today in state.events.values():
                    raise RuleError("TREE_ALREADY_WATERED_TODAY")
                if state.water < 1:
                    raise RuleError("INSUFFICIENT_WATER")
                state.water -= 1
                state.stage += 1
                state.events[key] = self.today
            return {"watered_on": state.events[key], "wallet": response()["wallet"], "tree": response()["tree"]}
        raise AssertionError(action)
