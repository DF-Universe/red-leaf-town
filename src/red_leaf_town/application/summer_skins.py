"""Summer skin redemption and gallery support, contributed in PR #2.

Generalised to multiple events in PR #4 (autumn harvest festival): every event
keeps its own catalog file and its own claim prefix, while the gallery numbers
pseudo stages over **all** skins a partner owns, so a partner can carry one
costume per event without the stage numbers colliding.
"""
from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path

_DATA_ROOT = Path(__file__).resolve().parents[3] / 'data'

# 事件 → 皮肤目录。顺序即合并顺序，决定伪阶段号的排布，别随手调整。
EVENT_SKINS = {
    'summer': 'summer_skins.json',
    'autumn': 'autumn_skins.json',
}
CLAIM_PREFIX = 'summer_skin:'
VIEW_PREFIX = 'summer_skin_view:'
SENTINEL_BASE = 100


def claim_prefix(event: str) -> str:
    """存档键前缀：每个活动各记各的，互不占位。"""
    return f'{event}_skin:'


def skin_events() -> tuple[str, ...]:
    return tuple(EVENT_SKINS)


@lru_cache(maxsize=1)
def load_skin_catalog() -> dict:
    """全部活动的皮肤合并成一份；每条补上 `event` 标记。改配置后重启服务。"""
    skins: list[dict] = []
    for event, filename in EVENT_SKINS.items():
        path = _DATA_ROOT / filename
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("skins") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                skins.append({**entry, "event": event})
    return {"skins": skins}


def find_skin(skin_id: str, event: str | None = None) -> dict | None:
    return next(
        (
            skin
            for skin in load_skin_catalog()["skins"]
            if skin.get("id") == skin_id and (event is None or skin.get("event") == event)
        ),
        None,
    )


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
    """该伙伴名下的全部皮肤（跨活动，按 catalog 顺序，保证伪阶段号稳定）。"""
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
            if player.crossover_claims.get(claim_prefix(str(skin.get('event') or '')) + skin['id'])]


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


def _skin_view(service, player, skin: dict, active: bool) -> dict:
    skin_id = str(skin.get("id") or "")
    unlocked = bool(player.crossover_claims.get(claim_prefix(str(skin.get("event") or "")) + skin_id))
    cost = []
    affordable = True
    for entry in skin.get("cost") or []:
        item_id = str(entry.get("item_id") or "")
        quantity = int(entry.get("quantity") or 0)
        min_quality = int(entry.get("min_quality") or 0)
        owned = _owned_amount(player, item_id, min_quality)
        cost.append({
            "item_id": item_id,
            "item_name": _item_name(service, item_id),
            "quantity": quantity,
            "min_quality": min_quality,
            "owned": owned,
        })
        if owned < quantity:
            affordable = False
    return {
        "id": skin_id,
        "event": str(skin.get("event") or ""),
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
    }


class SummerSkinsMixin:
    def _event_skins_view(self, player, event: str, active: bool) -> list[dict]:
        """某个活动的皮肤列表。秋活面板复用这套视图，别另写一份。"""
        return [
            _skin_view(self, player, skin, active)
            for skin in load_skin_catalog()["skins"]
            if skin.get("event") == event and skin.get("id")
        ]

    def _skins_view(self, player, now):
        season = self._summer_season()
        active = bool(season and season.is_open(now))
        return self._event_skins_view(player, "summer", active)

    def _claim_skin_for_event(self, oauth_sub: str, skin_id: str, event: str):
        from .service import GameError
        skin = find_skin(skin_id, event)
        if skin is None:
            raise GameError("skin_not_found", "没有这款限定皮肤", 404)
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            if not self._event_skin_open(event, now):
                raise GameError("event_closed", "活动不在进行中，无法兑换", 409)
            key = claim_prefix(event) + skin_id
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

    def _event_skin_open(self, event: str, now: int) -> bool:
        """活动是否在可兑换窗口内。默认只认夏活；其它活动由各自的 Mixin 覆盖。"""
        if event == "summer":
            season = self._summer_season()
            return bool(season and season.is_open(now))
        return False

    def claim_summer_skin(self, oauth_sub: str, skin_id: str):
        return self._claim_skin_for_event(oauth_sub, skin_id, "summer")
