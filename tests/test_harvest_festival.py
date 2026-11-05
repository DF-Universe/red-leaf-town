from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain.economy import add_item
from red_leaf_town.domain.models import GachaDropRecord, GachaRequestRecord
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
    service = GameService(content, repository, clock=clock, rng=random.Random(11))
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
    assert first["rotation"]["starts_at"] == started + 3 * 86400

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
