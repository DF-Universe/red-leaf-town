from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain.economy import add_item
from red_leaf_town.domain.models import GachaDropRecord, GachaRequestRecord, GachaPoolProgressState
from red_leaf_town.harvest_content import load_harvest_festival
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.season_content import load_seasons

POOL = "harvest-rotation-1"
SUB = "harvest-sub"


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


@pytest.fixture(scope="module")
def season_window():
    festival = load_harvest_festival()
    season = load_seasons()[festival.season_id]
    return int(season.starts_at.timestamp()), int(season.ends_at.timestamp())


@pytest.fixture
def game(season_window):
    content = load_content()
    repository = InMemoryPlayerRepository(content)
    started, _ = season_window
    clock = Clock(started + 5 * 86400 + 12 * 3600)   # 第 2 期正中
    seasons = load_seasons()
    seasons["autumn_harvest_festival"] = seasons["autumn_harvest_festival"].model_copy(update={"enabled": True})
    service = GameService(content, repository, clock=clock, rng=random.Random(11), season_loader=lambda: seasons)
    player = service.ensure_player(SUB, "收秋居民")
    return service, repository, clock, player


def stock(repository, player, item_id, quantity, quality=0):
    repository.update(player.player_id, lambda state: add_item(state, item_id, quantity, quality))


def fill(service, team):
    for dish_id, quantity in team:
        service.harvest_submit(SUB, "table_1", dish_id, quantity)


def test_snapshot_lists_three_tables_and_two_rites(game):
    service, _, _, _ = game
    state = service.harvest_snapshot(SUB)
    assert state["visible"] and state["active"]
    assert [table["id"] for table in state["tables"]] == ["table_1", "table_2", "table_3"]
    assert [rite["id"] for rite in state["rites"]] == ["field", "final"]
    assert state["daily_limit"] == load_harvest_festival().daily_limit_per_table
    # 第一桌开局就能交，后两桌各等前一张摆满
    assert [table["unlocked"] for table in state["tables"]] == [True, False, False]


def test_submit_consumes_dishes_and_stops_at_daily_limit(game):
    service, repository, _, player = game
    stock(repository, player, "pumpkin_soup", 12)

    sent = service.harvest_submit(SUB, "table_1", "pumpkin_soup", 4)
    assert sent["result"]["submitted"] == 4
    assert sent["state"]["tables"][0]["today_sent"] == 4

    with pytest.raises(GameError) as error:
        service.harvest_submit(SUB, "table_1", "pumpkin_soup", 1)
    assert error.value.code == "daily_limit"

    # 跨天额度清零（日界与每日委托同口径）
    service.clock.now += 86400
    again = service.harvest_submit(SUB, "table_1", "pumpkin_soup", 1)
    assert again["state"]["tables"][0]["today_sent"] == 1


def test_filling_a_table_unlocks_the_next_one(game):
    service, repository, _, player = game
    festival = load_harvest_festival()
    first = festival.tables[0]
    for dish in first.dishes:
        stock(repository, player, dish.item_id, dish.quantity)

    state = None
    limit = load_harvest_festival().daily_limit_per_table
    for dish in first.dishes:
        remaining = dish.quantity
        while remaining > 0:
            take = min(remaining, limit)      # 一天最多交 limit 份
            state = service.harvest_submit(SUB, "table_1", dish.item_id, take)["state"]
            remaining -= take
            service.clock.now += 86400

    assert state["tables"][0]["filled"]
    assert state["tables"][0]["claimed"]
    assert state["tables"][1]["unlocked"]

    with pytest.raises(GameError) as error:
        service.harvest_submit(SUB, "table_1", first.dishes[0].item_id, 1)
    assert error.value.code == "table_filled"


def test_submit_is_rejected_outside_the_season(game, season_window):
    service, repository, _, player = game
    _, ends = season_window
    stock(repository, player, "pumpkin_soup", 10)

    service.clock.now = ends + 3600
    state = service.harvest_snapshot(SUB)
    assert not state["active"]
    with pytest.raises(GameError) as error:
        service.harvest_submit(SUB, "table_1", "pumpkin_soup", 1)
    assert error.value.code == "event_closed"


def test_autumn_skin_claim_is_once_and_only_during_the_event(game, season_window):
    service, repository, _, player = game
    skin = next(s for s in service.harvest_snapshot(SUB)["skins"] if s["id"] == "leilei_qipao")
    for entry in skin["cost"]:
        stock(repository, player, entry["item_id"], entry["quantity"])

    first = service.harvest_claim_skin(SUB, "leilei_qipao")
    assert first["result"]["duplicate"] is False
    second = service.harvest_claim_skin(SUB, "leilei_qipao")
    assert second["result"]["duplicate"] is True

    # 皮肤解锁记在 autumn_skin: 前缀，和夏活互不占位
    saved = repository.get_by_sub(SUB)
    assert saved.crossover_claims.get("autumn_skin:leilei_qipao")

    _, ends = season_window
    service.clock.now = ends + 3600
    with pytest.raises(GameError) as error:
        service.harvest_claim_skin(SUB, "luo_nali_qipao")
    assert error.value.code == "event_closed"

    # 夏活皮肤不能从这条路由兑走
    with pytest.raises(GameError) as error:
        service.harvest_claim_skin(SUB, "ai_xinyu_swimsuit")
    assert error.value.code == "skin_not_found"


def test_rotating_pool_follows_the_season_and_carries_pity_over(game, season_window):
    service, repository, clock, _ = game
    started, ends = season_window

    def pool():
        return next(
            entry for entry in service._gacha_pools_snapshot(repository.get_by_sub(SUB))
            if entry["pool_id"] == POOL
        )

    first = pool()
    assert first["featured_partner_id"] == "luo_nali"
    assert first["rotation"]["index"] == 1
    season = service.season_loader()["autumn_harvest_festival"]
    assert first["rotation"]["starts_at"] == int((season.starts_at.replace(hour=4, minute=0, second=0) + timedelta(days=3)).timestamp())

    # 换期：第 3 期是古剑喵，UP 跟着换、卡面跟着换
    clock.now = started + 7 * 86400
    third = pool()
    assert third["featured_partner_id"] == "gujian_miao"
    assert third["rotation"]["next_partner_id"] == "mami"
    assert third["background"]["id"] == "gacha_rot_gujian_miao"

    # 大保底跨期继承：第 2 期歪了别人，换到第 3 期仍"下个五星必出 UP"
    pid = service.repository.get_by_sub(SUB).player_id

    def set_history(records):
        repository.update(pid, lambda state: setattr(state, "gacha_history", records))

    def history(at: int, content_id: str):
        return GachaRequestRecord(
            request_id=f"hist-{at}-{content_id}",
            pool_id=POOL,
            count=1,
            created_at=at,
            results=[GachaDropRecord(kind="partner", content_id=content_id, rarity=5)],
        )

    set_history([history(started + 4 * 86400, "guqi")])
    player = repository.get_by_sub(SUB)
    assert service._gacha_featured_guaranteed(player, service._gacha_pool(POOL)) is True

    # 上一期出的是上一期的 UP，不算歪，也不能白送一次大保底
    set_history([history(started + 4 * 86400, "luo_nali")])
    player = repository.get_by_sub(SUB)
    assert service._gacha_featured_guaranteed(player, service._gacha_pool(POOL)) is False

    # 活动结束，池子撤下
    clock.now = ends + 3600
    assert all(entry["pool_id"] != POOL for entry in service._gacha_pools_snapshot(repository.get_by_sub(SUB)))


def test_story_replay_returns_a_seen_story(game):
    service, _, _, player = game
    # 回看通道与具体剧本无关，取仓库里任意一段已接入的剧本即可
    catalog = service.story_catalog_loader().script_map
    story_id = next(iter(catalog))
    script = catalog[story_id]
    service.repository.update(player.player_id, lambda state: state.seen_story_ids.append(story_id))

    replay = service.story_replay(SUB, story_id)
    assert replay["story"]["id"] == script.id
    assert replay["seen"] is True
    # 只读：不写 seen，也不结算
    assert story_id in service.repository.get_by_sub(SUB).seen_story_ids

    with pytest.raises(GameError) as error:
        service.story_replay(SUB, "story_that_does_not_exist")
    assert error.value.code == "story_not_found"


def test_story_replay_rejects_unseen_story_without_changing_save(game):
    service, repository, _, player = game
    story_id = next(iter(service.story_catalog_loader().script_map))
    before = repository.get_by_sub(SUB).model_dump()
    with pytest.raises(GameError) as error:
        service.story_replay(SUB, story_id)
    assert error.value.code == "story_locked"
    assert error.value.status == 403
    assert repository.get_by_sub(SUB).model_dump() == before


def test_all_tables_take_19_days_and_pay_exactly_once(game):
    service, repository, clock, player = game
    for table in load_harvest_festival().tables:
        for dish in table.dishes:
            stock(repository, player, dish.item_id, dish.quantity)
    before = repository.get_by_sub(SUB)
    rewards = []
    for day in range(1, 20):
        for table in service.harvest_snapshot(SUB)["tables"]:
            # Refresh after completing the preceding table on the same day.
            table = next(t for t in service.harvest_snapshot(SUB)["tables"] if t["id"] == table["id"])
            if not table["unlocked"] or table["filled"]:
                continue
            remaining = table["today_left"]
            for dish in table["dishes"]:
                count = min(remaining, dish["remaining"])
                if count:
                    result = service.harvest_submit(SUB, table["id"], dish["item_id"], count)
                    remaining -= result["result"]["submitted"]
                    if result["result"]["table_filled"]:
                        rewards.append(result["result"]["reward"])
        state = service.harvest_snapshot(SUB)
        assert state["all_filled"] == (day == 19)
        clock.advance(86400)
    after = repository.get_by_sub(SUB)
    assert after.coins - before.coins == 26000
    assert after.guide_leaves - before.guide_leaves == 8
    assert len(rewards) == 3
    assert sum(after.inventory["miracle_crystal"].values()) - sum(before.inventory.get("miracle_crystal", {}).values()) == 3
    assert sum(after.inventory["harvest_ear"].values()) == 1
    for table in load_harvest_festival().tables:
        with pytest.raises(GameError) as error:
            service.harvest_submit(SUB, table.id, table.dishes[0].item_id, 1)
        assert error.value.code == "table_filled"
    assert repository.get_by_sub(SUB).coins == after.coins


@pytest.mark.parametrize("broken", [None, "{", "[]", '{"pool_id":"harvest-rotation-1"}'])
def test_bad_rotation_config_hides_pool_without_breaking_other_pools(game, monkeypatch, tmp_path, broken):
    from red_leaf_town.application import rotating_gacha
    service, repository, _, _ = game
    path = tmp_path / "rotation.json"
    if broken is not None:
        path.write_text(broken)
    monkeypatch.setattr(rotating_gacha, "CONFIG_PATH", path)
    pools = service._gacha_pools_snapshot(repository.get_by_sub(SUB))
    assert pools
    assert all(pool["pool_id"] != POOL for pool in pools)
    with pytest.raises(GameError) as error:
        service.recruit(SUB, 1, "invalid-config-recruit", POOL)
    assert error.value.code == "gacha_pool_not_found"


def test_disabled_harvest_has_no_entry_or_actions(game):
    service, repository, _, player = game
    season = service.season_loader()["autumn_harvest_festival"]
    season.enabled = False
    assert not service.harvest_snapshot(SUB)["visible"]
    assert not service.harvest_snapshot(SUB)["active"]
    with pytest.raises(GameError) as error:
        service.harvest_submit(SUB, "table_1", "pumpkin_soup", 1)
    assert error.value.code == "event_closed"
    assert all(pool["pool_id"] != POOL for pool in service._gacha_pools_snapshot(player))


def test_history_freezes_featured_partner_across_schedule_edits(game):
    service, repository, clock, player = game
    record = GachaRequestRecord(request_id="frozen-up-history", pool_id=POOL, count=1,
                               created_at=int(clock.now), featured_partner_id="leilei",
                               results=[GachaDropRecord(kind="partner", content_id="leilei", rarity=5)])
    repository.update(player.player_id, lambda state: state.gacha_history.append(record))
    # The timestamp now falls in Luo Nali's period. The saved actual UP wins.
    assert service._gacha_pool(POOL).featured_partner_id == "luo_nali"
    assert not service._gacha_featured_guaranteed(repository.get_by_sub(SUB), service._gacha_pool(POOL))


def test_recruit_persists_actual_up_and_keeps_pity_across_boundary(game):
    service, repository, clock, player = game
    from red_leaf_town.application.rotating_gacha import load_rotation_config
    config = load_rotation_config(service.season_loader())
    clock.now = config.boundaries[1] - 1

    def prepare(state):
        state.experience = service.content.levels[-1].total_xp
        state.guide_leaves = 10
        state.gacha_progress[POOL] = GachaPoolProgressState(five_pity=49)
        state.gacha_history = [GachaRequestRecord(
            request_id="previous-missed-up", pool_id=POOL, count=1,
            created_at=config.starts_at, featured_partner_id="leilei",
            results=[GachaDropRecord(kind="partner", content_id="guqi", rarity=5)],
        )]
    repository.update(player.player_id, prepare)
    # Save pity in the first period, then draw exactly at the second period.
    clock.advance(1)
    pulled = service.recruit(SUB, 1, "boundary-guaranteed-draw", POOL)
    assert pulled["result"]["featured_partner_id"] == "luo_nali"
    assert pulled["result"]["results"][0]["content_id"] == "luo_nali"
    assert repository.get_by_sub(SUB).gacha_progress[POOL].five_pity == 0
    balance = repository.get_by_sub(SUB).guide_leaves
    repeated = service.recruit(SUB, 1, "boundary-guaranteed-draw", POOL)
    assert repeated["result"]["replayed"]
    assert repository.get_by_sub(SUB).guide_leaves == balance


def test_harvest_story_cannot_be_marked_seen_to_bypass_replay_lock(game):
    from red_leaf_town.story_content import StoryCatalog, StoryScript
    service, repository, clock, player = game
    script = StoryScript.model_validate({
        "id": "harvest_act_2", "title": "第二幕测试",
        "trigger": {"hook": "cue", "params": {"cue": "harvest:act_2"}},
        "steps": [{"type": "dialogue", "speaker": "洛纳莉", "text": "测试"}],
    })
    service.story_catalog_loader = lambda: StoryCatalog(scripts=[script])
    for action in [lambda: service.story_cue(SUB, "harvest:act_2"),
                   lambda: service.mark_story_seen(SUB, script.id),
                   lambda: service.story_replay(SUB, script.id)]:
        with pytest.raises(GameError) as error:
            action()
        assert error.value.code == "story_locked"
    assert script.id not in repository.get_by_sub(SUB).seen_story_ids
    repository.update(player.player_id, lambda state: state.crossover_claims.update({"harvest:table:table_1": 1}))
    assert service.story_cue(SUB, "harvest:act_2")["stories"][0]["id"] == script.id
    service.mark_story_seen(SUB, script.id)
    assert service.story_replay(SUB, script.id)["seen"]


def test_published_harvest_scripts_preserve_author_steps_and_gate_unfinished_content(game):
    import hashlib
    import json
    from pathlib import Path
    service, repository, clock, player = game
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'docs/harvest-story-import.json').read_text())
    catalog = service.story_catalog_loader()
    assert len(manifest['chapters']) == 4
    for entry in manifest['chapters']:
        script = catalog.script_map[entry['story_id']]
        assert len(script.steps) == entry['steps']
        digest = hashlib.sha256(json.dumps([s.model_dump() for s in script.steps], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        assert digest == entry['steps_sha256']
        for asset_id in script.asset_ids:
            asset = service.story_asset_loader().asset_map[asset_id]
            assert asset.asset_key and '/community/' not in asset.asset_key
    assert service.story_cue(SUB, 'harvest:act_1')['stories'][0]['id'] == 'harvest_act_1'
    with pytest.raises(GameError):
        service.story_cue(SUB, 'harvest:act_3')
    repository.update(player.player_id, lambda state: state.crossover_claims.update({
        'harvest:table:table_1': 1, 'harvest:table:table_2': 1, 'harvest:table:table_3': 1,
    }))
    assert service.story_cue(SUB, 'harvest:act_3')['stories'][0]['id'] == 'harvest_act_3'
    assert service.story_cue(SUB, 'harvest:rite_field')['stories'][0]['id'] == 'harvest_rite_field'
    state = service.harvest_snapshot(SUB)
    assert state['chapters'][-1]['pending']
    assert state['rites'][-1]['pending'] and not state['rites'][-1]['unlocked']
    service.mark_story_seen(SUB, 'harvest_rite_field')
    assert service.story_replay(SUB, 'harvest_rite_field')['seen']
