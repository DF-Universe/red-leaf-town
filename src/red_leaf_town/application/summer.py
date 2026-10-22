from red_leaf_town.domain.models import SeasonProgressState
from red_leaf_town.rewards import serialize_reward, validate_reward_references
from red_leaf_town.summer_content import POINT_UNITS, SUMMER_ID, load_summer_event


class SummerServiceMixin:
    def _summer_season(self):
        try:
            return self.season_loader().get(SUMMER_ID)
        except (ValueError, OSError):
            return None

    def _award_summer_units(self, player, definition, units, now):
        if units <= 0 or getattr(definition, "season_id", "") != SUMMER_ID:
            return
        season = self._summer_season()
        if not season or not season.is_open(now):
            return
        progress = player.season_progress.setdefault(SUMMER_ID, SeasonProgressState())
        progress.units += units

    def _finish_summer_battle(self, player, run, expedition, now):
        self._award_summer_units(player, expedition, run.season_pending_units, now)
        run.season_pending_units = 0

    def _summer_chapter_unlocked(self, player, chapter, now):
        season = self._summer_season()
        progress = player.season_progress.get(SUMMER_ID, SeasonProgressState())
        return bool(season and now >= season.starts_at.timestamp()
                    and progress.units >= chapter.points * POINT_UNITS
                    and (not chapter.previous_story_id or chapter.previous_story_id in player.seen_story_ids))

    def _require_summer_story(self, player, story_id, now):
        from .service import GameError
        chapter = next((c for c in load_summer_event().chapters if c.story_id == story_id), None)
        if chapter is None or not self._summer_chapter_unlocked(player, chapter, now):
            raise GameError("story_locked", "这段夏活剧情尚未解锁", 409)

    def _summer_snapshot(self, player, now):
        event = load_summer_event()
        season = self._summer_season()
        progress = player.season_progress.get(SUMMER_ID, SeasonProgressState())
        started = bool(season and now >= season.starts_at.timestamp())
        active = bool(season and season.is_open(now))
        claim_end = int(season.ends_at.timestamp()) + event.claim_grace_days * 86400 if season else 0
        can_claim = started and now < claim_end
        partners = self.partner_catalog_loader()
        return {
            "visible": started, "active": active, "can_claim": can_claim,
            "skins": self._skins_view(player, now),
            "name": season.name if season else "夏夜潮祭",
            "starts_at": int(season.starts_at.timestamp()) if season else 0,
            "ends_at": int(season.ends_at.timestamp()) if season else 0,
            "claim_ends_at": claim_end,
            "points": progress.units / POINT_UNITS,
            "target": event.milestones[-1].points,
            "milestones": [{"points": m.points, "reward": serialize_reward(m.reward, self.content, partners),
                "claimed": m.points in progress.claimed_thresholds,
                "claimable": can_claim and progress.units >= m.points * POINT_UNITS and m.points not in progress.claimed_thresholds}
                for m in event.milestones],
            "chapters": [{"story_id": c.story_id, "title": c.title, "points": c.points,
                "cue": "summer:" + c.story_id,
                "unlocked": self._summer_chapter_unlocked(player, c, now),
                "seen": c.story_id in player.seen_story_ids,
                "previous_seen": not c.previous_story_id or c.previous_story_id in player.seen_story_ids}
                for c in event.chapters],
        }

    def claim_summer_reward(self, oauth_sub, points):
        from .service import GameError
        event = load_summer_event()
        milestone = next((m for m in event.milestones if m.points == points), None)
        if milestone is None:
            raise GameError("reward_not_found", "没有这一档活动奖励", 404)
        validate_reward_references(milestone.reward, self.content, self.partner_catalog_loader(), "summer")
        now = self._now()
        def mutation(player):
            self._settle(player, now)
            progress = player.season_progress.setdefault(SUMMER_ID, SeasonProgressState())
            if points in progress.claimed_thresholds:
                return {"points": points, "duplicate": True}
            season = self._summer_season()
            if not season or not (season.starts_at.timestamp() <= now < season.ends_at.timestamp() + event.claim_grace_days * 86400):
                raise GameError("event_closed", "当前不在活动奖励领取期", 409)
            if progress.units < points * POINT_UNITS:
                raise GameError("points_insufficient", "活动点数不足", 409)
            granted = self._grant_reward(player, milestone.reward, now)
            progress.claimed_thresholds.append(points)
            return {"points": points, "duplicate": False, "granted": granted}
        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}
