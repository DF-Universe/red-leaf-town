"""夏活限定皮肤兑换（运行时补丁包，不改动 red_leaf_town 包内文件）。

数据：<repo>/data/summer_skins.json（改完重启后端；文件小，每次现读）。
存档：解锁记录写 ``PlayerState.crossover_claims["summer_skin:<id>"] = 时刻``。
该字段是「账号级一次性领取记录」的 string->int 字典，皮肤解锁机制完全同构，
且 crossover 逻辑只按 campaign_id 精确 get，`summer_skin:` 前缀不会撞键。

接线（应用装配层，详见 patches/README.md）：
    install_summer_shop()   —— patch SummerServiceMixin._summer_snapshot，
                               给 state.summer_event 追加 skins 数组；并挂 claim_summer_skin。
    路由 POST /web/api/red-leaf-town/summer/skins/<skin_id>/claim
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent  # patches/
# 默认读上游 <repo>/data/summer_skins.json；装配层可用 install(path=...) 覆盖。
DEFAULT_SKINS_PATH = HERE.parent / "data" / "summer_skins.json"
_SKINS_PATH = DEFAULT_SKINS_PATH
CLAIM_PREFIX = "summer_skin:"


def load_skin_catalog() -> dict:
    """每次现读：皮肤数量极少，读文件成本可忽略，换来改完重启即生效的直觉。"""
    if not _SKINS_PATH.is_file():
        return {"skins": []}
    data = json.loads(_SKINS_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) and isinstance(data.get("skins"), list) else {"skins": []}


def find_skin(skin_id: str) -> dict | None:
    return next((s for s in load_skin_catalog()["skins"] if s.get("id") == skin_id), None)


def _owned_amount(player, item_id: str, min_quality: int) -> int:
    return sum(
        amount
        for quality, amount in (player.inventory.get(item_id) or {}).items()
        if quality >= min_quality
    )


def _item_name(service, item_id: str) -> str:
    item_map = getattr(service.content, "item_map", None) or {}
    item = item_map.get(item_id)
    return getattr(item, "name", "") or item_id


def install_summer_shop(path: str | Path | None = None) -> None:
    """patch 进 SummerServiceMixin：state 快照追加 skins + 挂兑换方法。幂等。

    path：皮肤数据文件；默认 <repo>/data/summer_skins.json。
    """
    global _SKINS_PATH
    if path is not None:
        _SKINS_PATH = Path(path)
    from red_leaf_town.application import summer as summer_mixin_module
    from red_leaf_town.application.service import GameError

    mixin = summer_mixin_module.SummerServiceMixin

    if getattr(mixin, "_summer_shop_installed", False):
        return

    original_snapshot = mixin._summer_snapshot

    def _summer_snapshot(self, player, now):
        snapshot = original_snapshot(self, player, now)
        snapshot["skins"] = _skins_view(self, player, now)
        return snapshot

    def _skins_view(self, player, now):
        season = self._summer_season()
        active = bool(season and season.is_open(now))
        view = []
        for skin in load_skin_catalog()["skins"]:
            skin_id = str(skin.get("id") or "")
            if not skin_id:
                continue
            unlocked = bool(player.crossover_claims.get(CLAIM_PREFIX + skin_id))
            cost = []
            affordable = True
            for entry in skin.get("cost") or []:
                item_id = str(entry.get("item_id") or "")
                quantity = int(entry.get("quantity") or 0)
                min_quality = int(entry.get("min_quality") or 0)
                owned = _owned_amount(player, item_id, min_quality)
                cost.append({
                    "item_id": item_id,
                    "item_name": _item_name(self, item_id),
                    "quantity": quantity,
                    "min_quality": min_quality,
                    "owned": owned,
                })
                if owned < quantity:
                    affordable = False
            view.append({
                "id": skin_id,
                "partner_id": str(skin.get("partner_id") or ""),
                "name": str(skin.get("name") or skin_id),
                "display_name": str(skin.get("display_name") or skin.get("name") or skin_id),
                "subtitle": str(skin.get("subtitle") or ""),
                "asset_key": str(skin.get("asset_key") or ""),
                "width": int(skin.get("width") or 0),
                "height": int(skin.get("height") or 0),
                "unlocked": unlocked,
                "can_claim": bool(active and not unlocked and affordable),
                "cost": cost,
            })
        return view

    def claim_summer_skin(self, oauth_sub: str, skin_id: str):
        skin = find_skin(skin_id)
        if skin is None:
            raise GameError("skin_not_found", "没有这款限定皮肤", 404)
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            season = self._summer_season()
            if not season or not season.is_open(now):
                raise GameError("event_closed", "活动不在进行中，无法兑换", 409)
            key = CLAIM_PREFIX + skin_id
            if player.crossover_claims.get(key):
                return {"skin_id": skin_id, "duplicate": True}
            for entry in skin.get("cost") or []:
                item_id = str(entry.get("item_id") or "")
                quantity = int(entry.get("quantity") or 0)
                min_quality = int(entry.get("min_quality") or 0)
                if _owned_amount(player, item_id, min_quality) < quantity:
                    raise GameError(
                        "material_insufficient",
                        f"兑换材料不足：{_item_name(self, item_id)}",
                        409,
                    )
            for entry in skin.get("cost") or []:
                self._consume_minimum_quality_items(
                    player,
                    str(entry.get("item_id") or ""),
                    int(entry.get("quantity") or 0),
                    int(entry.get("min_quality") or 0),
                )
            player.crossover_claims[key] = int(now)
            return {"skin_id": skin_id, "duplicate": False, "name": skin.get("name")}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    mixin._summer_snapshot = _summer_snapshot
    mixin.claim_summer_skin = claim_summer_skin
    mixin._summer_shop_installed = True
