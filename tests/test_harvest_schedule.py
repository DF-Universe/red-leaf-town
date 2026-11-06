from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import runpy

import pytest

from red_leaf_town.application.rotating_gacha import RotationConfig, load_rotation_config
from red_leaf_town.season_content import SeasonCatalog


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = runpy.run_path(str(ROOT / "scripts/schedule_harvest.py"))


def scheduled(at):
    payload = json.loads((ROOT / "data/seasons.json").read_text())
    for entry in payload["seasons"]:
        if entry["id"] == "autumn_harvest_festival":
            entry["enabled"] = False
    return LAUNCH["schedule"](payload, at)


@pytest.mark.parametrize("at", [
    "2026-11-06T00:01:23+08:00", "2026-11-06T03:59:59+08:00",
    "2026-11-06T04:00:00+08:00", "2026-11-06T23:59:59+08:00",
    "2026-11-05T18:23:45+00:00",
])
def test_launch_uses_exact_instant_and_15_calendar_rotations(at):
    at = datetime.fromisoformat(at)
    catalog = SeasonCatalog.model_validate(scheduled(at))
    season = next(s for s in catalog.seasons if s.id == "autumn_harvest_festival")
    assert season.enabled
    assert season.starts_at.timestamp() == at.timestamp()
    assert season.ends_at.isoformat() == "2026-12-21T04:00:00+08:00"
    payload = json.loads((ROOT / "data/rotating_gacha.json").read_text())
    config = RotationConfig(payload, (int(season.starts_at.timestamp()), int(season.ends_at.timestamp())))
    slots = config.all_slots()
    assert len(slots) == 15
    assert Counter(entry[2]["partner_id"] for entry in slots) == {
        "leilei": 3, "luo_nali": 3, "gujian_miao": 3, "mami": 3, "zhuoyan": 3,
    }
    assert config.slot_at(config.starts_at - 1) is None
    assert config.slot_at(config.starts_at).index == 0
    for index, start, partner in slots:
        slot = config.slot_at(start)
        assert slot.index == index
        assert slot.round_no == index // 5 + 1
        assert slot.partner_id == partner["partner_id"]
        assert config.slot_at(slot.ends_at - 1).index == index
        if index:
            local = datetime.fromtimestamp(start, LAUNCH["ZONE"])
            assert local.hour == 4 and local.minute == 0
            assert local.date() == season.starts_at.date() + timedelta(days=3 * index)
            assert config.slot_at(start - 1).index == index - 1
        assert slot.next_partner is None if index == 14 else slot.next_partner is not None
    assert config.slot_at(config.ends_at - 1).round_no == 3
    assert config.slot_at(config.ends_at) is None
    assert not season.is_open(config.ends_at)


def test_launch_does_not_change_other_seasons_or_rearm_on_restart():
    payload = json.loads((ROOT / "data/seasons.json").read_text())
    updated = scheduled(datetime.fromisoformat("2026-11-06T12:34:56+08:00"))
    assert updated["seasons"][0] == payload["seasons"][0]
    with pytest.raises(ValueError, match="已经启用"):
        LAUNCH["schedule"](updated, datetime.fromisoformat("2026-11-07T12:34:56+08:00"))
    with pytest.raises(ValueError, match="时区"):
        LAUNCH["launch_window"](datetime(2026, 11, 6, 12))


def test_rotation_cache_observes_season_changes_without_config_touch(tmp_path):
    path = tmp_path / "rotation.json"
    path.write_text((ROOT / "data/rotating_gacha.json").read_text())
    def seasons(at):
        catalog = SeasonCatalog.model_validate(scheduled(datetime.fromisoformat(at)))
        return {s.id: s for s in catalog.seasons}
    first = load_rotation_config(seasons("2026-11-06T12:00:00+08:00"), path)
    next_seasons = seasons("2026-11-07T12:00:00+08:00")
    second = load_rotation_config(next_seasons, path)
    assert second.starts_at - first.starts_at == 86400
    assert second.ends_at - first.ends_at == 86400
    assert load_rotation_config(next_seasons, path) is second
    next_seasons["autumn_harvest_festival"].enabled = False
    with pytest.raises(ValueError):
        load_rotation_config(next_seasons, path)
