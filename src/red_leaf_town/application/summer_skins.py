"""Summer skin redemption and gallery support, contributed in PR #2."""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
_SKINS_PATH = Path(__file__).resolve().parents[3] / 'data/summer_skins.json'
CLAIM_PREFIX = 'summer_skin:'
VIEW_PREFIX = 'summer_skin_view:'
SENTINEL_BASE = 100

@lru_cache(maxsize=1)
def load_skin_catalog() -> dict:
    """皮肤配置按进程缓存；修改后重启服务。"""
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


def _skins_for(partner_id: str) -> list[dict]:
    """该伙伴名下的全部皮肤（按 catalog 顺序，保证伪阶段号稳定）。"""
    return [s for s in load_skin_catalog()["skins"] if str(s.get("partner_id") or "") == partner_id]


def _artwork_entry(skin: dict, stage: int) -> dict:
    return {
        "breakthrough": stage,
        "asset_key": str(skin.get("asset_key") or ""),
        "width": int(skin.get("width") or 0),
        "height": int(skin.get("height") or 0),
        "content_type": "image/webp",
        "name": str(skin.get("display_name") or skin.get("name") or ""),
        "skin_id": str(skin.get("id") or ""),
    }


def skin_artworks(player, partner_id):
    return [_artwork_entry(skin, SENTINEL_BASE + index)
            for index, skin in enumerate(_skins_for(partner_id))
            if player.crossover_claims.get(CLAIM_PREFIX + skin['id'])]


def attach_skin_artworks(player, records):
    for record in records:
        if record.get('missing'):
            continue
        partner_id = record['partner_id']
        artworks = skin_artworks(player, partner_id)
        record['available_artworks'].extend(artworks)
        view = player.crossover_claims.get(VIEW_PREFIX + partner_id)
        selected = next((a for a in artworks if a['breakthrough'] == view), None)
        if selected:
            record['artwork_stage'] = view
            record['artwork'] = selected
            record['avatar_crop'] = None
    return records

class SummerSkinsMixin:
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
        from .service import GameError
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

