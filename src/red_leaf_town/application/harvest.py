"""Autumn harvest festival: three tables to fill, two rites to watch.

Data lives in `data/harvest_festival.json`, the season window in
`data/seasons.json`. Everything the player earns is recorded in
`PlayerState.crossover_claims`, so no save schema migration is needed.

Design notes (they are not obvious from the code):

* **One table at a time.** A table may require the previous one to be full;
  the dishes carry a fixed quota each, so "fill the table" is deterministic
  rather than a quality contest.
* **Daily quota per table.** Four portions a day, not accumulated. The day
  boundary reuses the commission reset hour, otherwise commissions would roll
  over while the tables still count yesterday.
* **Rites are stories, not claims.** `done` reads `seen_story_ids`; there is no
  stake and no reward, so re-watching costs nothing.
"""
from __future__ import annotations

from red_leaf_town.domain.commissions import commission_day
from red_leaf_town.harvest_content import AUTUMN_ID, HarvestFestival, HarvestTable, load_harvest_festival
from red_leaf_town.rewards import serialize_reward

SENT = "harvest:sent:"
DAY = "harvest:day:"
DAYN = "harvest:dayn:"
TABLE_DONE = "harvest:table:"


def _owned(player, item_id: str) -> int:
    return sum(int(amount) for amount in (player.inventory.get(item_id) or {}).values())


def _day_number(day: str) -> int:
    """'2026-10-29' -> 20261029; crossover_claims only holds ints."""
    return int(day.replace("-", ""))


def _table_required(table: HarvestTable) -> int:
    return sum(entry.quantity for entry in table.dishes)


def _table_sent(player, table: HarvestTable) -> int:
    return sum(
        int(player.crossover_claims.get(f"{SENT}{table.id}:{entry.item_id}", 0))
        for entry in table.dishes
    )


def _dish_required(table: HarvestTable, dish_id: str) -> int:
    return sum(entry.quantity for entry in table.dishes if entry.item_id == dish_id)


def _table_unlocked(player, table: HarvestTable) -> bool:
    if not table.requires:
        return True
    return bool(player.crossover_claims.get(TABLE_DONE + table.requires))


class HarvestServiceMixin:
    # ---------------------------------------------------------------- 基础
    def _harvest_festival(self) -> HarvestFestival:
        return load_harvest_festival()

    def _harvest_season(self):
        festival = self._harvest_festival()
        try:
            return self.season_loader().get(festival.season_id)
        except (ValueError, OSError):
            # 档期配置坏了就当活动没开——上游 seasons 也是这个口径，别把页面打 500。
            return None

    def _harvest_clock(self, now: int):
        festival = self._harvest_festival()
        season = self._harvest_season()
        active = bool(season and season.is_open(now))
        started = bool(season and season.enabled and now >= season.starts_at.timestamp())
        return festival, season, active, started

    def _harvest_day(self, now: int) -> str:
        commissions = self.content.commissions
        return commission_day(now, int(commissions.reset_hour), str(commissions.timezone))

    def _harvest_quota(self, player, table_id: str, day: str, limit: int) -> int:
        if int(player.crossover_claims.get(DAY + table_id, 0)) != _day_number(day):
            return limit
        return max(0, limit - int(player.crossover_claims.get(DAYN + table_id, 0)))

    def _require_harvest_story(self, player, story_id: str, now: int) -> None:
        """Gate first playback and the seen endpoint, not just the page buttons."""
        from .service import GameError

        festival, _, _, started = self._harvest_clock(now)
        entry = next((entry for entry in [*festival.chapters, *festival.rites]
                      if entry.story_id == story_id), None)
        if (entry is None or not started or getattr(entry, "pending", False)
                or (entry.unlock != "start" and not player.crossover_claims.get(TABLE_DONE + entry.unlock))):
            raise GameError("story_locked", "这段丰收祭剧情尚未解锁", 409)

    def _require_harvest_table(self, festival: HarvestFestival, table_id: str) -> HarvestTable:
        from .service import GameError

        table = festival.table_map().get(table_id)
        if table is None:
            raise GameError("table_not_found", "没有这张桌子", 404)
        return table

    # ---------------------------------------------------------------- 视图
    def _harvest_dish_view(self, player, table: HarvestTable, entry) -> dict:
        item = self.content.item_map.get(entry.item_id)
        sent = int(player.crossover_claims.get(f"{SENT}{table.id}:{entry.item_id}", 0))
        return {
            "item_id": entry.item_id,
            "name": getattr(item, "name", entry.item_id) if item else entry.item_id,
            "icon": getattr(item, "icon", "package") if item else "package",
            "required": entry.quantity,
            "sent": sent,
            "remaining": max(0, entry.quantity - sent),
            "owned": _owned(player, entry.item_id),
            "done": sent >= entry.quantity,
        }

    def _harvest_snapshot(self, player, now: int) -> dict:
        festival, season, active, started = self._harvest_clock(now)
        day = self._harvest_day(now)
        limit = festival.daily_limit_per_table
        partners = self.partner_catalog_loader()

        tables = []
        for table in festival.tables:
            required = _table_required(table)
            sent = _table_sent(player, table)
            tables.append({
                "id": table.id,
                "name": table.name or table.id,
                "subtitle": table.subtitle,
                "requires": table.requires,
                "unlocked": _table_unlocked(player, table),
                "required": required,
                "sent": sent,
                "filled": sent >= required and required > 0,
                "claimed": bool(player.crossover_claims.get(TABLE_DONE + table.id)),
                "dishes": [self._harvest_dish_view(player, table, entry) for entry in table.dishes],
                "today_sent": (
                    int(player.crossover_claims.get(DAYN + table.id, 0))
                    if int(player.crossover_claims.get(DAY + table.id, 0)) == _day_number(day)
                    else 0
                ),
                "today_left": self._harvest_quota(player, table.id, day, limit),
                "reward": serialize_reward(table.reward, self.content, partners),
            })

        chapters = []
        for chapter in festival.chapters:
            unlocked = (
                True if chapter.unlock == "start"
                else bool(player.crossover_claims.get(TABLE_DONE + chapter.unlock))
            )
            chapters.append({
                "story_id": chapter.story_id,
                "title": chapter.title or chapter.story_id,
                "cue": chapter.cue,
                "note": chapter.note,
                # pending = 剧本还没写，卡片留着位置但明说「未完待续」，别让人点了空转
                "pending": chapter.pending,
                "unlocked": unlocked and started,
                "seen": chapter.story_id in player.seen_story_ids,
            })

        rites = []
        for rite in festival.rites:
            unlocked = (
                True if rite.unlock == "start"
                else bool(player.crossover_claims.get(TABLE_DONE + rite.unlock))
            )
            rites.append({
                "id": rite.id,
                "name": rite.name or rite.id,
                "host": rite.host,
                "pending": rite.pending,
                "story_id": rite.story_id,
                "cue": rite.cue,
                "unlocked": unlocked and started and not rite.pending,
                "done": bool(rite.story_id) and rite.story_id in player.seen_story_ids,
            })

        return {
            "visible": started,
            "active": active,
            "name": festival.name,
            "starts_at": int(season.starts_at.timestamp()) if season else 0,
            "ends_at": int(season.ends_at.timestamp()) if season else 0,
            "today": day,
            "daily_limit": limit,
            "min_level": festival.min_level,
            "tables": tables,
            "chapters": chapters,
            "rites": rites,
            "skins": self._event_skins_view(player, "autumn", active),
            "total_required": sum(table["required"] for table in tables),
            "total_sent": sum(table["sent"] for table in tables),
            "all_filled": bool(tables) and all(table["filled"] for table in tables),
        }

    # ---------------------------------------------------------------- 接口
    def harvest_snapshot(self, oauth_sub: str) -> dict:
        from .service import GameError

        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        return self._harvest_snapshot(player, self._now())

    def harvest_submit(self, oauth_sub: str, table_id: str, dish_id: str, quantity: int = 1) -> dict:
        from .service import GameError

        quantity = int(quantity)
        if quantity < 1:
            raise GameError("invalid_quantity", "至少要交一份", 400)
        festival = self._harvest_festival()
        table = self._require_harvest_table(festival, table_id)
        now = self._now()
        day = self._harvest_day(now)
        day_key = _day_number(day)
        limit = festival.daily_limit_per_table

        def mutation(player):
            self._settle(player, now)
            _, _, active, started = self._harvest_clock(now)
            if not started:
                raise GameError("event_closed", "丰收祭还没有开始", 409)
            if not active:
                raise GameError("event_closed", "丰收祭已经结束了", 409)
            if not _table_unlocked(player, table):
                raise GameError("table_locked", "上一张桌子还没摆满", 409)
            if player.crossover_claims.get(TABLE_DONE + table_id):
                raise GameError("table_filled", "这张桌子已经摆满了", 409)
            required = _dish_required(table, dish_id)
            if required <= 0:
                raise GameError("dish_not_on_table", "这道菜不在这一桌上", 400)
            sent = int(player.crossover_claims.get(f"{SENT}{table_id}:{dish_id}", 0))
            room = required - sent
            if room <= 0:
                raise GameError("dish_filled", "这个菜位已经摆满了", 409)
            quota = self._harvest_quota(player, table_id, day, limit)
            if quota <= 0:
                raise GameError("daily_limit", f"这张桌子今天已经交满 {limit} 份了", 409)
            count = min(quantity, room, quota)
            if _owned(player, dish_id) < count:
                item = self.content.item_map.get(dish_id)
                raise GameError(
                    "item_insufficient",
                    f"{getattr(item, 'name', dish_id)} 不够 {count} 份",
                    409,
                )

            self._consume_minimum_quality_items(player, dish_id, count, 0)
            player.crossover_claims[f"{SENT}{table_id}:{dish_id}"] = sent + count
            if int(player.crossover_claims.get(DAY + table_id, 0)) != day_key:
                player.crossover_claims[DAY + table_id] = day_key
                player.crossover_claims[DAYN + table_id] = 0
            player.crossover_claims[DAYN + table_id] = (
                int(player.crossover_claims.get(DAYN + table_id, 0)) + count
            )

            result = {
                "table_id": table_id,
                "dish_id": dish_id,
                "submitted": count,
                "table_filled": False,
                "granted": None,
            }
            if _table_sent(player, table) >= _table_required(table):
                player.crossover_claims[TABLE_DONE + table_id] = 1
                result["table_filled"] = True
                result["granted"] = self._grant_reward(player, table.reward, now)
                result["reward"] = table.reward.model_dump()
            return result

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._harvest_snapshot(player, self._now())}

    def harvest_claim_skin(self, oauth_sub: str, skin_id: str) -> dict:
        """秋活限定皮肤：与夏活同一套兑换逻辑，只是目录与存档前缀不同。"""
        return self._claim_skin_for_event(oauth_sub, skin_id, "autumn")

    def _event_skin_open(self, event: str, now: int) -> bool:
        if event == "autumn":
            _, _, active, _started = self._harvest_clock(now)
            return active
        return super()._event_skin_open(event, now)

    def story_replay(self, oauth_sub: str, story_id: str) -> dict:
        """仅允许回看已经读过的剧本；不修改存档，也不结算奖励。

        `story_cue` 会把已读剧本过滤掉（那是给自动触发用的），活动页的"回看"
        按钮走的也是 cue，于是看过一次就再也拿不到。这里按 id 直接取。
        """
        from .service import GameError
        from red_leaf_town.story_content import serialize_script

        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        script = self.story_catalog_loader().script_map.get(story_id)
        if script is None:
            raise GameError("story_not_found", "剧情不存在", 404)
        if story_id not in player.seen_story_ids:
            raise GameError("story_locked", "尚未读过这段剧情，不能回看", 403)
        return {
            "story": serialize_script(
                script,
                self.story_asset_loader(),
                self.partner_catalog_loader(),
                self.content,
            ),
            "seen": story_id in player.seen_story_ids,
        }
