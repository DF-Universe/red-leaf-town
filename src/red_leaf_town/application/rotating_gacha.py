"""Rotating featured partner for one seasonal pool.

A pool normally carries a single static `featured_partner_id`. For the autumn
harvest festival the five 5★ partners take turns, three days each, inside one
pool id — so the pool definition has to be rewritten on the way out.

Two facts keep this consistent:

* `recruit()` and `_gacha_pools_snapshot()` both go through `_rotate_gacha`,
  so the poster and the actual draw can never disagree about who is featured.
* The window is read from `seasons.json`, not hard-coded here. The first
  rotation starts the moment the event starts, which keeps the schedule owned
  by whoever runs the event.

Pity rules for this pool (deliberately different from the standard pools):
soft pity and the "next 5★ must be featured" guarantee **both carry over**
between rotations. A partner is only featured for three days; resetting on
rotation would punish anyone who saved pulls for her.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import isfinite
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from red_leaf_town.gacha_pools import GachaDefinition

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[3] / "data/rotating_gacha.json"


class RotationConfigError(ValueError):
    """配置或档期不可用。调用点应降级为"池子不下发"，不要把页面打成 500。"""


@dataclass(frozen=True)
class RotationSlot:
    pool_id: str
    index: int
    total: int
    round_no: int
    partner: dict[str, Any]
    starts_at: int
    ends_at: int
    season_start_at: int
    next_partner: dict[str, Any] | None

    @property
    def partner_id(self) -> str:
        return str(self.partner.get("partner_id") or "")

    def as_payload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "total": self.total,
            "round_no": self.round_no,
            "partner_id": self.partner_id,
            "partner_name": self.partner.get("name") or self.partner_id,
            "title": self.partner.get("title") or "",
            "tagline": self.partner.get("tagline") or "",
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "season_start_at": self.season_start_at,
            "next_partner_id": (self.next_partner or {}).get("partner_id"),
            "next_partner_name": (self.next_partner or {}).get("name"),
            "next_title": (self.next_partner or {}).get("title"),
        }


class RotationConfig:
    def __init__(self, payload: dict[str, Any], season_window: tuple[int, int] | None = None) -> None:
        self.pool_id: str = payload["pool_id"]
        self.title: str = payload.get("title") or ""
        self.season_id: str = payload.get("season_id") or ""
        self.featured_rate: float = float(payload.get("featured_rate", 0.8))
        self.period_seconds: int = int(payload.get("period_hours", 72)) * 3600
        self.partners: list[dict[str, Any]] = list(payload.get("partners") or [])
        self.rounds = int(payload.get("rounds", 0))
        self.reset_hour = int(payload.get("reset_hour", 4))
        self.timezone = ZoneInfo(payload.get("timezone", "Asia/Shanghai"))

        explicit_start, explicit_end = payload.get("starts_at"), payload.get("ends_at")
        if explicit_start and explicit_end:
            window = (
                int(datetime.fromisoformat(str(explicit_start)).timestamp()),
                int(datetime.fromisoformat(str(explicit_end)).timestamp()),
            )
            self.window_source = "config"
        elif season_window is not None:
            window = season_window
            self.window_source = "season"
        else:
            raise RotationConfigError(
                "rotating gacha: no starts_at/ends_at in config and no matching season window"
            )
        self.starts_at, self.ends_at = int(window[0]), int(window[1])

        if self.period_seconds <= 0:
            raise RotationConfigError("rotating gacha: period_hours must be positive")
        if not self.partners:
            raise RotationConfigError("rotating gacha: partners list is empty")
        if not isfinite(self.featured_rate) or not 0 < self.featured_rate <= 1:
            raise RotationConfigError("rotating gacha: featured_rate must be in (0, 1]")
        ids = [entry.get("partner_id") for entry in self.partners]
        if any(not isinstance(pid, str) or not pid.strip() for pid in ids) or len(set(ids)) != len(ids):
            raise RotationConfigError("rotating gacha: partner ids must be nonempty and unique")
        if not self.pool_id or not self.season_id:
            raise RotationConfigError("rotating gacha: pool_id and season_id are required")
        if self.rounds < 0 or not 0 <= self.reset_hour <= 23:
            raise RotationConfigError("rotating gacha: invalid rounds or reset hour")
        if self.ends_at <= self.starts_at:
            raise RotationConfigError("rotating gacha: ends_at must be after starts_at")

        # Calendar rotations: publish at the actual instant; subsequent changes
        # occur at the reset hour every three calendar days. Exactly 15 slots,
        # without a phantom fourth round during the final four hours.
        if self.rounds:
            if self.period_seconds % 86400:
                raise RotationConfigError("calendar rotations require whole-day periods")
            launch_day = datetime.fromtimestamp(self.starts_at, self.timezone).date()
            days = self.period_seconds // 86400
            count = self.rounds * len(self.partners)
            boundaries = [int(datetime.combine(
                launch_day + timedelta(days=days * index),
                time(hour=self.reset_hour), self.timezone,
            ).timestamp()) for index in range(1, count + 1)]
            if boundaries[-1] != self.ends_at:
                raise RotationConfigError("season end must match the final calendar rotation")
            self.boundaries = [self.starts_at, *boundaries]
        else:
            self.boundaries = list(range(self.starts_at, self.ends_at, self.period_seconds)) + [self.ends_at]

    def slot_at(self, now: int) -> RotationSlot | None:
        if not (self.starts_at <= now < self.ends_at):
            return None
        raw_index = next(index for index, end in enumerate(self.boundaries[1:]) if now < end)
        total = len(self.boundaries) - 1
        slot_start, slot_end = self.boundaries[raw_index:raw_index + 2]
        next_partner = self.partners[(raw_index + 1) % len(self.partners)] if raw_index + 1 < total else None
        return RotationSlot(
            pool_id=self.pool_id,
            index=int(raw_index),
            total=int(total),
            round_no=int(raw_index // len(self.partners)) + 1,
            partner=self.partners[raw_index % len(self.partners)],
            starts_at=slot_start,
            ends_at=slot_end,
            season_start_at=self.starts_at,
            next_partner=next_partner,
        )

    def all_slots(self) -> list[tuple[int, int, dict[str, Any]]]:
        """整段档期的排期表（调试/文档用）：[(期号, 起始时间戳, 角色)]。"""
        return [(index, start, self.partners[index % len(self.partners)])
                for index, start in enumerate(self.boundaries[:-1])]


def _season_window(season_id: str, seasons: dict) -> tuple[int, int]:
    season = seasons.get(season_id)
    if season is None or not season.enabled:
        known = "、".join(sorted(seasons)) or "(none)"
        raise RotationConfigError(
            f"rotating gacha: season {season_id!r} not found (have {known})"
        )
    return int(season.starts_at.timestamp()), int(season.ends_at.timestamp())


_cache: dict[str, tuple[tuple, RotationConfig]] = {}
_warned: set[str] = set()


def load_rotation_config(seasons: dict, path: Path | str | None = None) -> RotationConfig:
    """按 mtime 缓存；档期改了就重新算，不必重启服务。"""
    target = Path(path) if path is not None else CONFIG_PATH
    if not target.is_file():
        raise FileNotFoundError(f"rotating gacha config not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    needs_season = not (payload.get("starts_at") and payload.get("ends_at"))
    window = _season_window(str(payload.get("season_id") or ""), seasons) if needs_season else None
    stat = target.stat()
    signature = (stat.st_mtime_ns, stat.st_size, window)
    cached = _cache.get(str(target))
    if cached is not None and cached[0] == signature:
        return cached[1]
    config = RotationConfig(payload, window)
    _cache[str(target)] = (signature, config)
    return config


def _safe_config(seasons: dict) -> RotationConfig | None:
    try:
        return load_rotation_config(seasons)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        message = str(exc)
        if message not in _warned:
            _warned.add(message)
            logger.warning("%s (rotating pool hidden until the config is fixed)", message)
        return None


class RotatingGachaMixin:
    """Hooks the service calls; the default behaviour of the base pool is kept.

    `_rotate_gacha` is looked up on every pool read, so it also covers the pool
    list snapshot. `_rotation_decorate` adds the countdown payload, and
    `_rotation_guaranteed` returns None when this pool is not a rotating one.
    """

    def _rotation(self) -> RotationConfig | None:
        try:
            seasons = self.season_loader()
        except (ValueError, OSError):
            return None
        return _safe_config(seasons)

    def _rotate_gacha(self, definition: GachaDefinition, now: int | None = None) -> GachaDefinition | None:
        """把池定义换成"当前这一期"；档期外返回 None（池子撤下）。"""
        if not definition.rotating:
            return definition
        config = self._rotation()
        if config is None or definition.pool_id != config.pool_id or definition.season_id != config.season_id:
            return None
        partners = self.partner_catalog_loader().partner_map
        if any(entry["partner_id"] not in partners or partners[entry["partner_id"]].rarity != 5
               for entry in config.partners):
            return None
        slot = config.slot_at(self._now() if now is None else now)
        if slot is None:
            return None
        return definition.model_copy(update={
            "featured_partner_id": slot.partner_id,
            "featured_rate": config.featured_rate,
            "title": config.title or definition.title,
            "season_id": config.season_id or definition.season_id,
            # 每期自己的卡面：抽卡与快照都走同一处，海报不会串期。
            "background_asset_id": slot.partner.get("background_asset_id") or definition.background_asset_id,
        })

    def _rotation_decorate(self, pools: list[dict], now: int | None = None) -> list[dict]:
        """给池子快照补当前期次/倒计时/下期预告，前端不再自己算期号。"""
        config = self._rotation()
        if config is None:
            return pools
        slot = config.slot_at(self._now() if now is None else now)
        if slot is None:
            return pools
        for entry in pools:
            if entry.get("pool_id") == config.pool_id:
                entry["rotation"] = slot.as_payload()
                entry["rotation_rate"] = config.featured_rate
        return pools

    def _rotation_guaranteed(self, player, gacha: GachaDefinition, now: int | None = None) -> bool | None:
        """轮替池的大保底口径；返回 None 表示交给上游原逻辑。"""
        config = self._rotation()
        if config is None or gacha.pool_id != config.pool_id:
            return None
        slot = config.slot_at(self._now() if now is None else now)
        if slot is None:
            return False
        # 大保底跨期继承：取最近一次 5★，用**那次抽卡的时间戳**回溯当时是哪一期。
        # 出了那一刻的 UP 就不算歪（所以上一期的 UP 不会被本期当成歪）；
        # 歪了就一直挂着，换期后第一个五星仍必出当期 UP。
        for record in reversed(player.gacha_history):
            if record.pool_id != config.pool_id:
                continue
            for drop in reversed(record.results):
                if drop.kind == "partner" and drop.rarity == 5:
                    at = config.slot_at(int(record.created_at))
                    featured_id = record.featured_partner_id or (at.partner_id if at is not None else gacha.featured_partner_id)
                    return drop.content_id != featured_id
        return False
