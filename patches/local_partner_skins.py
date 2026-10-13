"""伙伴详情页 · 限定皮肤立绘并入「突破立绘」画廊（本地扩展，上游零改动）。

背景（上游机制，只读）：
  * 伙伴详情快照 `_partner_snapshots()` 输出
      - `available_artworks` = 该伙伴 artworks 里 breakthrough <= owned.breakthrough 的那些；
      - `artwork_stage`       = `owned.artwork_stage`（模型硬约束 ge=0, le=2）；
      - `artwork`             = `definition.artwork_for(artwork_stage)`。
  * 前端 `PartnersView.vue` 用 available_artworks 做画廊、左右滑动调
    `select_partner_artwork(partner_id, breakthrough)`。
  * `select_partner_artwork` 校验 `0 <= stage <= owned.breakthrough`。

问题：`OwnedPartner.artwork_stage` 的 pydantic 约束是 0..2，**不能**把皮肤塞进这个字段，
否则存档回读即校验失败。

做法（全部在本地扩展里）：
  * 已解锁的皮肤以「伪阶段」`breakthrough = 100 + 序号` 追加进 `available_artworks`，
    并带上 `name`（皮肤名，前端画廊标题用）与 `skin_id`；
  * 当前看的是不是皮肤，记在 `player.crossover_claims["summer_skin_view:<partner_id>"]`
    （该字段本就是 string->int 字典，皮肤解锁用的是 `summer_skin:<id>`，前缀不撞）；
  * 命中该键时把快照里的 `artwork_stage` / `artwork` 覆写成皮肤条目——
    `owned.artwork_stage` 本身始终留在 0..2，存档结构零改动。
  * `select_partner_artwork` 包一层：伪阶段走本地记键，普通阶段先清键再交回上游逻辑。

接线：应用装配层里 `install_partner_skins()`（在 install_summer_shop 之后调用）。
"""

from __future__ import annotations

from patches.local_summer_shop import CLAIM_PREFIX, load_skin_catalog

# 皮肤伪阶段起点：上游突破立绘只有 0/1/2，100 起不会撞
SENTINEL_BASE = 100
VIEW_PREFIX = "summer_skin_view:"


def _skins_for(partner_id: str) -> list[dict]:
    """该伙伴名下的全部皮肤（按 catalog 顺序，保证伪阶段号稳定）。"""
    return [s for s in load_skin_catalog()["skins"] if str(s.get("partner_id") or "") == partner_id]


def _owned_skins(player, partner_id: str) -> list[dict]:
    return [s for s in _skins_for(partner_id) if player.crossover_claims.get(CLAIM_PREFIX + str(s.get("id") or ""))]


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


def install_partner_skins() -> None:
    """patch GameService：画廊追加皮肤 + 允许选中皮肤伪阶段。幂等。"""
    from red_leaf_town.application.service import GameError, GameService

    if getattr(GameService, "_partner_skins_installed", False):
        return

    original_snapshots = GameService._partner_snapshots
    original_select = GameService.select_partner_artwork

    def _partner_snapshots(self, player, now=None):
        records = original_snapshots(self, player, now)
        for record in records:
            if record.get("missing"):
                continue
            partner_id = str(record.get("partner_id") or "")
            owned_skins = _owned_skins(player, partner_id)
            if not owned_skins:
                continue
            stage_map = {}
            artworks = list(record.get("available_artworks") or [])
            for index, skin in enumerate(owned_skins):
                stage = SENTINEL_BASE + index
                stage_map[stage] = skin
                artworks.append(_artwork_entry(skin, stage))
            record["available_artworks"] = artworks
            record["skin_stages"] = {str(stage): str(skin.get("id") or "") for stage, skin in stage_map.items()}

            view = int(player.crossover_claims.get(VIEW_PREFIX + partner_id) or 0)
            skin = stage_map.get(view)
            if skin is not None:
                record["artwork_stage"] = view
                record["artwork"] = _artwork_entry(skin, view)
        return records

    def select_partner_artwork(self, oauth_sub, partner_id, stage):
        if isinstance(stage, int) and not isinstance(stage, bool) and stage >= SENTINEL_BASE:
            def mutation(player):
                owned_skins = _owned_skins(player, partner_id)
                index = stage - SENTINEL_BASE
                if index < 0 or index >= len(owned_skins):
                    raise GameError("partner_artwork_locked", "尚未解锁这张立绘", 409)
                player.crossover_claims[VIEW_PREFIX + str(partner_id)] = int(stage)
                return {"partner_id": partner_id, "artwork_stage": int(stage)}

            player, result = self._update_by_sub(oauth_sub, mutation)
            return {"result": result, "state": self._snapshot(player)}

        # 普通突破立绘：先清掉"正在看皮肤"的标记，再交回上游逻辑
        def clear_view(player):
            player.crossover_claims.pop(VIEW_PREFIX + str(partner_id), None)
            return None

        try:
            self._update_by_sub(oauth_sub, clear_view)
        except Exception:  # noqa: BLE001 - 清标记失败不该挡住原选择流程
            pass
        return original_select(self, oauth_sub, partner_id, stage)

    GameService._partner_snapshots = _partner_snapshots
    GameService.select_partner_artwork = select_partner_artwork
    GameService._partner_skins_installed = True
