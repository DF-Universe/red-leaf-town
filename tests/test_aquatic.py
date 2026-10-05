from __future__ import annotations

import random

import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import GameContent, load_content
from red_leaf_town.domain import PlayerState
from red_leaf_town.domain.aquatic import decayed_combo, pond_cycle_seconds
from red_leaf_town.domain.economy import add_item
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_content import PartnerCatalog, PartnerDefinition
from red_leaf_town.partner_traits import record_partner_trait_effect, register_partner_trait


HOUR = 3600


class Clock:
    def __init__(self, now: int = 1_700_000_000):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds: int):
        self.now += seconds


class SequenceRandom:
    """按序列返回随机数；randint 取下界，方便断言加权抽取落在哪一项上。"""

    def __init__(self, draws: list[float]):
        self.draws = list(draws)
        self.index = 0

    def randint(self, lower: int, upper: int) -> int:
        return lower

    def random(self) -> float:
        value = self.draws[self.index % len(self.draws)]
        self.index += 1
        return value


def partner(partner_id: str, industry: str = "aquatic") -> PartnerDefinition:
    return PartnerDefinition.model_validate({
        "id": partner_id,
        "name": partner_id,
        "rarity": 4,
        "growth_curve": "linear",
        "tendencies": [{"industry": industry, "level_1": 40, "level_60": 160}],
        "avatar_crops": [
            {"breakthrough": stage, "x": 0, "y": 0, "w": 1, "h": 1}
            for stage in range(3)
        ],
    })


def build_game(rng=None, clock: Clock | None = None):
    content = load_content().model_copy(deep=True)
    repository = InMemoryPlayerRepository(content)
    clock = clock or Clock()
    catalog = PartnerCatalog(partners=[
        partner("angler", "aquatic"),
        partner("digger", "mining"),
    ])
    service = GameService(
        content,
        repository,
        clock=clock,
        rng=rng or random.Random(7),
        partner_catalog_loader=lambda: catalog,
    )
    player = service.ensure_player("aquatic-sub", "水边居民")
    service.admin_grant_partner(player.player_id, "angler")
    service.admin_grant_partner(player.player_id, "digger")
    return service, repository, clock, player


def set_level(repository, player_id: str, level: int):
    content = repository.content
    total_xp = next(entry.total_xp for entry in content.levels if entry.level == level)
    repository.update(player_id, lambda state: setattr(state, "experience", total_xp))


def fill_stamina(repository, player_id: str, amount: int = 60):
    repository.update(player_id, lambda state: setattr(state, "stamina", amount))


@pytest.fixture
def game():
    return build_game()


# --------------------------------------------------------------------- 内容


def test_aquatic_content_loads_with_industry_rules_and_feed_config():
    content = load_content()
    assert "aquatic" in content.industries
    assert [spot.id for spot in content.fishing_spots if not spot.season_id] == ["town_creek", "maple_lake"]
    assert [spot.min_level for spot in content.fishing_spots if not spot.season_id] == [2, 10]
    # 钓鱼是不限流的体力出口，所以定在最低档 6 XP/体力。
    assert all(spot.cast_xp == spot.stamina_cost * 6 for spot in content.fishing_spots)
    assert content.feed_slot is not None and content.feed_slot.capacity == 500
    assert content.feed_slot.quality_multipliers == [1.0, 1.1, 1.25, 1.45, 1.8]
    assert content.item_map["carrot"].feed.score == 25
    assert content.item_map["fish_fry"].feed is None


def test_every_fish_in_the_codex_carries_a_size_range():
    content = load_content()
    for spot in content.fishing_spots:
        fish = [entry for entry in spot.outputs if entry.codex]
        # 常驻钓点 3~5 种鱼，活动钓点允许 2 种；杂物不进图鉴。
        assert (2 if spot.season_id else 3) <= len(fish) <= 5
        assert all(entry.size_max > entry.size_min > 0 for entry in fish)
        assert all(not entry.has_size for entry in spot.outputs if not entry.codex)


def test_fishing_expectation_per_draw_stays_where_it_was():
    """改鱼种是为了图鉴，不是为了偷偷调收益，所以每次抽取的期望金币要对齐。"""
    content = load_content()
    prices = {item.id: item.sell_price for item in content.items}
    expected = {"town_creek": 18.3, "maple_lake": 31.3}
    for spot in content.fishing_spots:
        if spot.id not in expected:
            continue
        total = sum(entry.weight for entry in spot.outputs) + (spot.big_catch.weight if spot.big_catch else 0)
        value = sum(
            prices[entry.item_id] * (entry.quantity_min + entry.quantity_max) / 2 * entry.weight
            for entry in spot.outputs
        )
        if spot.big_catch:
            value += prices[spot.big_catch.item_id] * spot.big_catch.weight
        assert value / total == pytest.approx(expected[spot.id], rel=0.02)


def test_level_table_is_progressive_and_mining_rewards_follow_stamina():
    content = load_content()
    assert all(b.level == a.level + 1 and b.total_xp > a.total_xp and b.stamina_cap >= a.stamina_cap
               for a, b in zip(content.levels, content.levels[1:]))
    assert len({task.collect_xp / task.stamina_cost for task in content.mining_tasks}) == 1
    assert len({recipe.collect_xp / recipe.stamina_cost for recipe in content.recipes}) == 1


def test_slow_mining_vein_is_allowed_but_it_cannot_manufacture_stamina():
    payload = load_content().model_dump()
    payload["mining_tasks"][0]["duration_seconds"] = 4 * HOUR
    assert GameContent.model_validate(payload).mining_tasks[0].duration_seconds == 4 * HOUR

    payload["mining_tasks"][0]["duration_seconds"] = 10
    with pytest.raises(Exception, match="stamina recovery time"):
        GameContent.model_validate(payload)


def test_unknown_talent_modifier_is_rejected():
    payload = load_content().model_dump()
    payload["talents"][0]["modifiers"] = {"fishing_combo_capp": 5}
    with pytest.raises(Exception, match="unknown modifiers"):
        GameContent.model_validate(payload)


def test_schema_nineteen_migration_starts_from_empty_aquatic_state():
    started_at = 1_700_000_000
    player = PlayerState.model_validate({
        "schema_version": 18,
        "player_id": "legacy-aquatic",
        "oauth_sub": "legacy-aquatic-sub",
        "display_name": "旧存档居民",
        "stamina_updated_at": started_at,
        "created_at": started_at,
        "updated_at": started_at,
    })
    assert player.schema_version == PlayerState.model_fields["schema_version"].default
    assert player.ponds == []
    assert player.feed_slot.units == 0 and player.feed_slot.quality_score == 0
    assert player.fishing.combo == 0 and player.fishing.pending_big_catch is None
    assert player.fish_codex.entries == []


def test_schema_twenty_one_adds_neutral_pond_trait_parameters():
    started_at = 1_700_000_000
    player = PlayerState.model_validate({
        "schema_version": 20,
        "player_id": "legacy-pond-traits",
        "oauth_sub": "legacy-pond-traits-sub",
        "display_name": "旧鱼塘居民",
        "stamina_updated_at": started_at,
        "created_at": started_at,
        "updated_at": started_at,
        "ponds": [{"pond_id": "pond_1", "last_settled_at": started_at}],
    })
    pond = player.ponds[0]
    assert player.schema_version == PlayerState.model_fields["schema_version"].default
    assert (pond.cycle_multiplier, pond.feed_multiplier) == (1, 1)
    assert pond.quality_bonus == pond.generation_gain_bonus == 0
    assert pond.trait_effects == []


# --------------------------------------------------------------------- 钓鱼


def test_fishing_requires_the_unlock_level(game):
    service, repository, _, player = game
    with pytest.raises(GameError, match="解锁"):
        service.cast_line("aquatic-sub", "town_creek")


def test_casting_spends_stamina_and_fills_the_bag(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 2)
    fill_stamina(repository, player.player_id, 20)

    result = service.cast_line("aquatic-sub", "town_creek")["result"]
    assert result["stamina_cost"] == 3
    assert result["experience"] == 18
    assert result["draws"] >= 6
    assert sum(drop["quantity"] for drop in result["drops"]) >= result["draws"] - 1
    saved = repository.get(player.player_id)
    assert saved.stamina == 17
    assert sum(sum(bucket.values()) for bucket in saved.inventory.values()) >= result["draws"] - 1


def test_every_fish_records_a_size_in_the_codex(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 2)
    fill_stamina(repository, player.player_id, 60)
    for _ in range(6):
        clock.advance(5)
        fill_stamina(repository, player.player_id, 60)
        service.cast_line("aquatic-sub", "town_creek")

    saved = repository.get(player.player_id)
    fish = {"trash_fish", "stream_fish", "whitebait", "stone_loach"}
    recorded = [entry for entry in saved.fish_codex.entries if entry.item_id in fish]
    assert recorded, "六竿下去总该记到点什么"
    # 体型不再是大物的专利：普通鱼一样摇尺寸、一样记最大值。
    assert all(entry.max_size > 0 for entry in recorded)
    weed = saved.fish_codex.entry("river_weed")
    assert weed is None


def test_the_companion_earns_experience_per_cast(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 2)
    fill_stamina(repository, player.player_id, 30)
    service.assign_fishing_companion("aquatic-sub", "angler")

    result = service.cast_line("aquatic-sub", "town_creek")["result"]
    # 3 体力 × 每体力 2 点，与其他产业的体力分量同一口径。
    assert result["partner_experience"][0]["experience_gained"] == 6
    owned = next(entry for entry in repository.get(player.player_id).owned_partners if entry.partner_id == "angler")
    assert owned.experience == 6


def test_casting_without_a_companion_grants_nobody_experience(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 2)
    fill_stamina(repository, player.player_id, 30)
    assert service.cast_line("aquatic-sub", "town_creek")["result"]["partner_experience"] == []


def test_combo_builds_on_the_same_spot_and_resets_when_the_spot_changes(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 10)
    fill_stamina(repository, player.player_id, 60)

    for expected in (1, 2, 3):
        clock.advance(5)
        assert service.cast_line("aquatic-sub", "town_creek")["result"]["combo"] == expected

    clock.advance(5)
    assert service.cast_line("aquatic-sub", "maple_lake")["result"]["combo"] == 1


def test_combo_decays_after_the_grace_window():
    rules = load_content().fishing_combo
    now = 1_700_000_000
    assert decayed_combo(6, now, now + rules.idle_grace_seconds, rules.idle_grace_seconds, rules.decay_seconds) == 6
    assert decayed_combo(6, now, now + rules.idle_grace_seconds + rules.decay_seconds, rules.idle_grace_seconds, rules.decay_seconds) == 5
    assert decayed_combo(6, now, now + rules.idle_grace_seconds + 20 * rules.decay_seconds, rules.idle_grace_seconds, rules.decay_seconds) == 0


def test_combo_raises_draw_count(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 2)
    spot = service.content.fishing_spot_map["town_creek"]
    assert service._fishing_draw_count(spot, 0, 10) > service._fishing_draw_count(spot, 0, 0)


def test_repeated_request_id_does_not_roll_twice(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 2)
    fill_stamina(repository, player.player_id, 30)

    first = service.cast_line("aquatic-sub", "town_creek", "cast-request-1")["result"]
    stamina_after_first = repository.get(player.player_id).stamina
    clock.advance(5)
    second = service.cast_line("aquatic-sub", "town_creek", "cast-request-1")["result"]
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert repository.get(player.player_id).stamina == stamina_after_first


def test_fishing_companion_must_have_the_aquatic_tendency(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 2)
    with pytest.raises(GameError, match="水产倾向"):
        service.assign_fishing_companion("aquatic-sub", "digger")
    assert service.assign_fishing_companion("aquatic-sub", "angler")["result"]["partner_id"] == "angler"


def test_real_fishing_trait_is_reported_by_the_cast(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 2)
    fill_stamina(repository, player.player_id, 30)
    service.partner_catalog_loader().partner_map["angler"].trait_codes = ["flow_treasure"]
    service.assign_fishing_companion("aquatic-sub", "angler")

    result = service.cast_line("aquatic-sub", "town_creek")["result"]

    assert any(
        entry.get("trait_code") == "flow_treasure"
        and entry.get("effect") == "rare_weight_multiplier"
        for entry in result["applied_effects"]
    )


def test_a_partner_stationed_at_a_pond_cannot_also_come_fishing():
    service, repository, _, player = stocked_pond_game()
    service.assign_pond_partner("aquatic-sub", "pond_1", "angler")
    service.assign_fishing_companion("aquatic-sub", "angler")
    saved = repository.get(player.player_id)
    # 跨产业唯一派驻：陪钓会把它从鱼塘撤下来，而不是两头都占着。
    assert saved.ponds[0].assigned_partner_ids == []
    assert saved.fishing.companion_partner_id == "angler"


# ------------------------------------------------------------------- 大物


def big_catch_game():
    # 第一次 random() 决定抽到哪一项：给一个几乎为 1 的值稳定命中池子最后一项（大物）。
    service, repository, clock, player = build_game(rng=SequenceRandom([0.999999]))
    set_level(repository, player.player_id, 2)
    fill_stamina(repository, player.player_id, 40)
    return service, repository, clock, player


def test_hooking_a_big_catch_blocks_the_next_cast_until_it_is_resolved():
    service, repository, clock, player = big_catch_game()
    result = service.cast_line("aquatic-sub", "town_creek")["result"]
    assert result["big_catch"] is not None
    assert result["big_catch"]["item_id"] == "giant_stream_carp"
    clock.advance(5)
    with pytest.raises(GameError, match="大物"):
        service.cast_line("aquatic-sub", "town_creek")


def test_fighting_a_big_catch_spends_stamina_and_lands_a_recorded_giant():
    service, repository, clock, player = big_catch_game()
    service.cast_line("aquatic-sub", "town_creek")
    before = repository.get(player.player_id).stamina
    # random() < chance 判定成功，所以喂一个 0。
    service.rng = SequenceRandom([0.0])
    result = service.resolve_big_catch("aquatic-sub", "fight")["result"]
    assert result["success"] is True
    assert result["stamina_cost"] == 3
    assert result["size"] >= 42
    assert "first_big_catch" in {entry["achievement_id"] for entry in result["achievements"]}
    saved = repository.get(player.player_id)
    assert saved.stamina == before - 3
    assert saved.inventory["giant_stream_carp"]
    assert min(saved.inventory["giant_stream_carp"]) >= 3
    codex = saved.fish_codex.entry("giant_stream_carp")
    assert codex is not None and codex.max_size == result["size"]
    assert saved.fishing.pending_big_catch is None


def test_releasing_a_big_catch_costs_nothing_and_returns_an_ordinary_fish():
    service, repository, clock, player = big_catch_game()
    service.cast_line("aquatic-sub", "town_creek")
    before = repository.get(player.player_id).stamina
    result = service.resolve_big_catch("aquatic-sub", "release")["result"]
    assert result["success"] is False
    assert result["stamina_cost"] == 0
    assert result["drops"][0]["item_id"] == "stream_fish"
    assert repository.get(player.player_id).stamina == before


def test_codex_milestone_pays_out_once():
    service, repository, clock, player = build_game()
    set_level(repository, player.player_id, 2)
    fill_stamina(repository, player.player_id, 60)
    granted = []
    for _ in range(8):
        clock.advance(5)
        fill_stamina(repository, player.player_id, 20)
        granted.extend(service.cast_line("aquatic-sub", "town_creek")["result"]["codex_milestones"])
    saved = repository.get(player.player_id)
    if len(saved.fish_codex.entries) >= 3:
        assert [entry["id"] for entry in granted] == ["codex_3"]
        assert saved.maple_flame >= 200
    assert len(saved.fish_codex.claimed_milestones) == len(set(saved.fish_codex.claimed_milestones))


# --------------------------------------------------------------------- 饲料槽


def test_feed_deposit_is_a_weighted_average_and_quality_only_moves_the_score(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 10)
    repository.update(player.player_id, lambda state: add_item(state, "carrot", 10, 1))
    repository.update(player.player_id, lambda state: add_item(state, "meadow_hay", 10, 0))

    service.deposit_feed("aquatic-sub", "meadow_hay", 0, 10)
    slot = repository.get(player.player_id).feed_slot
    assert slot.units == 40 and slot.quality_score == pytest.approx(5)

    service.deposit_feed("aquatic-sub", "carrot", 1, 10)
    slot = repository.get(player.player_id).feed_slot
    # 40 份 5 分 + 30 份 25 分 → (200 + 750) / 70
    assert slot.units == 70
    assert slot.quality_score == pytest.approx(950 / 70)


def test_high_quality_input_raises_the_score_but_not_the_units(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 10)
    repository.update(player.player_id, lambda state: add_item(state, "carrot", 5, 4))
    service.deposit_feed("aquatic-sub", "carrot", 4, 5)
    slot = repository.get(player.player_id).feed_slot
    assert slot.units == 15
    assert slot.quality_score == pytest.approx(25 * 1.45)


def test_feed_slot_rejects_the_whole_batch_when_it_does_not_fit(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 10)
    repository.update(player.player_id, lambda state: add_item(state, "meadow_hay", 200, 0))
    with pytest.raises(GameError, match="装不下"):
        service.deposit_feed("aquatic-sub", "meadow_hay", 0, 130)
    saved = repository.get(player.player_id)
    assert saved.feed_slot.units == 0
    assert sum(saved.inventory["meadow_hay"].values()) == 200


def test_dumping_the_slot_returns_nothing(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 10)
    repository.update(player.player_id, lambda state: add_item(state, "meadow_hay", 10, 0))
    service.deposit_feed("aquatic-sub", "meadow_hay", 0, 10)
    service.dump_feed("aquatic-sub")
    saved = repository.get(player.player_id)
    assert saved.feed_slot.units == 0 and saved.feed_slot.quality_score == 0
    assert "meadow_hay" not in saved.inventory


def test_items_without_a_feed_block_cannot_be_thrown_into_the_slot(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 10)
    repository.update(player.player_id, lambda state: add_item(state, "fish_fry", 3, 0))
    with pytest.raises(GameError, match="不能当饲料"):
        service.deposit_feed("aquatic-sub", "fish_fry", 0, 1)


# --------------------------------------------------------------------- 鱼塘


def stocked_pond_game(fry: int = 10, hay: int = 100):
    """牧草一份 4 units，饲料槽容量 500，所以 hay 最多 125 —— 满槽够跑 500 个周期。"""
    service, repository, clock, player = build_game()
    set_level(repository, player.player_id, 10)
    repository.update(player.player_id, lambda state: setattr(state, "coins", 3000))
    service.build_pond("aquatic-sub", "pond_1")
    repository.update(player.player_id, lambda state: add_item(state, "fish_fry", fry, 0))
    repository.update(player.player_id, lambda state: add_item(state, "meadow_hay", hay, 0))
    service.deposit_feed("aquatic-sub", "meadow_hay", 0, hay)
    return service, repository, clock, player


def test_ponds_are_built_with_coins_not_handed_out_by_level(game):
    service, repository, _, player = game
    repository.update(player.player_id, lambda state: setattr(state, "coins", 10000))
    set_level(repository, player.player_id, 9)
    with pytest.raises(GameError, match="10 级"):
        service.build_pond("aquatic-sub", "pond_1")

    set_level(repository, player.player_id, 10)
    service.snapshot_by_sub("aquatic-sub")
    # 到等级只是解锁资格，不到账。
    assert repository.get(player.player_id).ponds == []

    repository.update(player.player_id, lambda state: setattr(state, "coins", 2999))
    with pytest.raises(GameError, match="3000 红叶币"):
        service.build_pond("aquatic-sub", "pond_1")

    repository.update(player.player_id, lambda state: setattr(state, "coins", 3000))
    result = service.build_pond("aquatic-sub", "pond_1")["result"]
    assert result["build_cost"] == 3000
    saved = repository.get(player.player_id)
    assert saved.coins == 0
    assert [pond.pond_id for pond in saved.ponds] == ["pond_1"]
    with pytest.raises(GameError, match="已经挖好"):
        service.build_pond("aquatic-sub", "pond_1")


def test_buildable_ponds_show_up_in_the_snapshot(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 10)
    repository.update(player.player_id, lambda state: setattr(state, "coins", 3000))
    aquatic = service.snapshot_by_sub("aquatic-sub")["aquatic"]
    assert [entry["id"] for entry in aquatic["buildable_ponds"] if entry["unlocked"]] == ["pond_1"]
    assert aquatic["buildable_ponds"][0]["build_cost"] == 3000
    assert aquatic["buildable_ponds"][0]["unlocked"] is True
    assert aquatic["buildable_ponds"][0]["affordable"] is True
    service.build_pond("aquatic-sub", "pond_1")
    assert not any(entry["id"] == "pond_1" for entry in service.snapshot_by_sub("aquatic-sub")["aquatic"]["buildable_ponds"])


def test_stocking_consumes_fry_and_opens_the_pond():
    service, repository, clock, player = stocked_pond_game()
    result = service.stock_pond("aquatic-sub", "pond_1", "crucian", 4)["result"]
    # 投下去的是鱼苗，不是成鱼：不能立刻捞走，否则鱼苗就成了品质洗白的通道。
    assert result["stock"] == 0
    assert result["fry"] == 4
    assert result["maturation_seconds"] == 3 * 7200
    saved = repository.get(player.player_id)
    assert sum(saved.inventory["fish_fry"].values()) == 6
    assert saved.ponds[0].species_id == "crucian"
    assert saved.ponds[0].cycle_seconds == pond_cycle_seconds(7200, 0, 150)
    with pytest.raises(GameError, match="还没有能捞的成鱼"):
        service.harvest_pond("aquatic-sub", "pond_1", 1)


def test_fry_grow_up_after_three_cycles():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 4)
    clock.advance(6 * HOUR - 1)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].stock == 0
    clock.advance(1)
    service.snapshot_by_sub("aquatic-sub")
    pond = repository.get(player.player_id).ponds[0]
    assert pond.stock == 4
    assert pond.fry == []


def test_settling_over_and_over_does_not_age_the_fry_faster():
    """回归：_settle 每个请求都跑，同一个周期里结算多少次，鱼苗都只该老那么多。"""
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 4)
    cycle = repository.get(player.player_id).ponds[0].cycle_seconds

    for _ in range(60):
        clock.advance(10)
        service.snapshot_by_sub("aquatic-sub")
    pond = repository.get(player.player_id).ponds[0]
    assert pond.settle_remainder == 600
    assert pond.fry[0].cycles_left == pytest.approx(3 - 600 / cycle)
    assert pond.stock == 0

    clock.advance(3 * cycle - 600)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].stock == 4


def test_each_batch_of_fry_matures_on_its_own_clock():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 5)
    clock.advance(2 * HOUR)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 5)
    clock.advance(4 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    pond = repository.get(player.player_id).ponds[0]
    # 第一批第 3 个周期成，第二批第 4 个周期成，两批互不影响。
    assert (pond.stock, pond.fry_total) == (5, 5)
    clock.advance(2 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].stock == 10


def test_a_stronger_partner_speeds_up_the_fry_already_in_the_pond():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 5)
    clock.advance(4 * HOUR)
    service.assign_pond_partner("aquatic-sub", "pond_1", "angler")
    pond = repository.get(player.player_id).ponds[0]
    # 鱼苗记的是"还差几个周期"，所以周期一缩短，在长的这批也跟着提前。
    assert pond.fry[0].cycles_left == pytest.approx(1.0)
    assert pond.cycle_seconds < 7200
    clock.advance(pond.cycle_seconds)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].stock == 5


def test_a_pond_partner_can_be_swapped_freely_in_the_first_tenth_of_the_cycle():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 4)
    cycle = repository.get(player.player_id).ponds[0].cycle_seconds

    clock.advance(int(cycle * 0.09))
    result = service.assign_pond_partner("aquatic-sub", "pond_1", "angler")["result"]

    assert result["queued"] is False
    pond = repository.get(player.player_id).ponds[0]
    assert pond.assigned_partner_ids == ["angler"]
    assert pond.cycle_seconds < 7200


def test_a_pond_swap_after_the_window_queues_to_the_next_cycle():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 4)
    cycle = repository.get(player.player_id).ponds[0].cycle_seconds

    clock.advance(int(cycle * 0.5))
    result = service.assign_pond_partner("aquatic-sub", "pond_1", "angler")["result"]

    assert result["queued"] is True
    pond = repository.get(player.player_id).ponds[0]
    # 这个周期还是原来的安排：周期长度也不能提前缩短，否则等于追认了已经跑掉的一半。
    assert pond.assigned_partner_ids == []
    assert pond.pending_partner_ids == ["angler"]
    assert pond.cycle_seconds == cycle

    clock.advance(int(cycle * 0.5))
    service.snapshot_by_sub("aquatic-sub")
    pond = repository.get(player.player_id).ponds[0]
    assert pond.assigned_partner_ids == ["angler"]
    assert pond.pending_partner_ids is None
    assert pond.cycle_seconds < cycle


def test_an_empty_pond_can_always_swap():
    service, repository, clock, player = stocked_pond_game()
    clock.advance(5 * HOUR)
    assert service.assign_pond_partner("aquatic-sub", "pond_1", "angler")["result"]["queued"] is False


def test_pond_trait_parameters_are_frozen_after_old_time_is_settled():
    service, repository, _, player = stocked_pond_game()
    code = "test_pond_parameter_snapshot"

    @register_partner_trait(code, "鱼塘参数测试", "验证资产分段快照", phases=("asset_prepare",))
    def apply(context):
        context["cycle_multiplier"] *= 0.9
        context["feed_multiplier"] *= 0.75
        context["quality_bonus"] += 12
        context["generation_gain_bonus"] += 0.5
        record_partner_trait_effect(context, "pond_parameter_snapshot")

    service.partner_catalog_loader().partner_map["angler"].trait_codes = [code]
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 4)
    assigned = service.assign_pond_partner("aquatic-sub", "pond_1", "angler")
    saved = repository.get(player.player_id).ponds[0]
    state = assigned["state"]["aquatic"]["ponds"][0]

    assert saved.cycle_multiplier == pytest.approx(0.9)
    assert saved.feed_multiplier == pytest.approx(0.75)
    assert saved.quality_bonus == pytest.approx(12)
    assert saved.generation_gain_bonus == pytest.approx(0.5)
    assert saved.trait_effects[0]["source_partner_id"] == "angler"
    assert state["feed_per_cycle"] == pytest.approx(service._pond_tier(saved).feed_per_cycle * 0.75)
    assert state["generation_gain"] == pytest.approx(
        service.content.pond_species_map["crucian"].generation_gain + 0.5
    )
    assert state["quality_ability"] >= saved.ability + 12


def test_newborn_fish_are_fry_as_well():
    service, repository, clock, player = stocked_pond_game(fry=20)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 20)
    clock.advance(6 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].stock == 20
    clock.advance(2 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    pond = repository.get(player.player_id).ponds[0]
    # 20 × 0.08 = 1.6，出一尾，但它进的是鱼苗池：自繁的鱼即时成年的话，买苗就没有理由要等。
    assert (pond.stock, pond.fry_total) == (20, 1)
    assert pond.growth_remainder == pytest.approx(0.6)
    clock.advance(6 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].stock == 21


def test_stocking_cannot_exceed_the_pond_capacity():
    service, repository, clock, player = stocked_pond_game(fry=100)
    with pytest.raises(GameError, match="最多容纳"):
        service.stock_pond("aquatic-sub", "pond_1", "crucian", 60)
    # 鱼苗也占容量，否则鱼苗池会变成一个不占地方的仓库。
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 30)
    with pytest.raises(GameError, match="最多容纳"):
        service.stock_pond("aquatic-sub", "pond_1", "crucian", 15)


def test_breeding_advances_by_whole_cycles_and_keeps_the_remainder():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    clock.advance(10 * HOUR + 900)
    service.snapshot_by_sub("aquatic-sub")
    pond = repository.get(player.player_id).ponds[0]
    # 前三个周期鱼苗在长，第 3 个周期末成鱼；之后每周期 10×0.08 = 0.8，两个周期攒到 1.6 出一尾。
    assert pond.stock == 10
    assert pond.fry_total == 1
    assert pond.growth_remainder == pytest.approx(0.6)
    assert pond.settle_remainder == 900


def test_small_stocks_still_grow_because_the_remainder_carries_over():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 1)
    # 3 个周期长成 + 13 个周期攒够 1.04 尾 + 3 个周期新鱼长成，一共 19 个周期。
    clock.advance(19 * 2 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    # ⌊1 × 0.08⌋ = 0，如果不留余数这口塘会永远卡在一尾。
    assert repository.get(player.player_id).ponds[0].stock == 2


def test_feed_is_charged_per_cycle_not_per_hour():
    service, repository, clock, player = stocked_pond_game(hay=2)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    clock.advance(4 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    # 一份牧草 4 units，两个周期扣两次共 4 份，跟这两个周期一共走了几个小时无关。
    assert repository.get(player.player_id).feed_slot.units == 4


def test_a_faster_cycle_burns_feed_faster():
    service, repository, clock, player = stocked_pond_game(hay=3)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    service.assign_pond_partner("aquatic-sub", "pond_1", "angler")
    cycle = repository.get(player.player_id).ponds[0].cycle_seconds
    clock.advance(4 * cycle)
    service.snapshot_by_sub("aquatic-sub")
    # 伙伴把周期压短，同样的墙上时间里跑了更多周期，料也就烧得更快 —— 能力换的是速度，不是省料。
    assert repository.get(player.player_id).feed_slot.units == 4


def test_pond_stops_when_the_feed_slot_runs_dry_and_resumes_after_refilling():
    service, repository, clock, player = stocked_pond_game(hay=1)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    # 槽里只有 4 份，一个周期两份 → 第 3 个周期开始买不起，停在第 2 个周期末，鱼苗也停在那里。
    clock.advance(20 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    pond = repository.get(player.player_id).ponds[0]
    assert pond.stalled is True
    assert (pond.stock, pond.fry_total) == (0, 10)
    assert pond.fry[0].cycles_left == pytest.approx(1.0)
    assert pond.settle_remainder == 0
    assert repository.get(player.player_id).feed_slot.units == 0

    repository.update(player.player_id, lambda state: add_item(state, "meadow_hay", 50, 0))
    service.deposit_feed("aquatic-sub", "meadow_hay", 0, 50)
    clock.advance(10 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    resumed = repository.get(player.player_id).ponds[0]
    assert resumed.stalled is False
    assert resumed.stock == 10


def test_pond_growth_is_capped_so_a_month_offline_matches_a_day_offline():
    service, repository, clock, player = stocked_pond_game(fry=20, hay=125)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 20)
    # 满槽只够跑 250 小时，剩下的时间鱼塘停摆 —— 但两个封顶早就在那之前到顶了。
    clock.advance(30 * 24 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    pond = repository.get(player.player_id).ponds[0]
    assert pond.stock == 40
    assert pond.fry_total == 0
    assert pond.generation_score == 60


def steady_pond_game():
    """把一口塘直接推到满塘：40 尾成鱼，稳态门槛 24 尾，世代加值开始累积。"""
    service, repository, clock, player = stocked_pond_game(fry=40, hay=125)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 40)
    clock.advance(6 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    return service, repository, clock, player


def test_generation_score_only_grows_while_the_stock_holds_the_steady_line():
    service, repository, clock, player = steady_pond_game()
    base = repository.get(player.player_id).ponds[0].generation_score
    assert repository.get(player.player_id).ponds[0].stock == 40
    clock.advance(10 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].generation_score == pytest.approx(base + 3.0)

    # 捞到门槛线上（24 尾）仍然继续攒。
    service.harvest_pond("aquatic-sub", "pond_1", 16)
    clock.advance(4 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].generation_score == pytest.approx(base + 4.2)


def test_holding_exactly_half_no_longer_protects_the_generation_score():
    service, repository, clock, player = steady_pond_game()
    clock.advance(10 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    before = repository.get(player.player_id).ponds[0].generation_score

    # 正好捞一半：捞鱼这一刻不扣分，但鱼群跌破门槛，之后每个周期自己往回掉。
    result = service.harvest_pond("aquatic-sub", "pond_1", 20)["result"]
    assert result["generation_score"] == pytest.approx(before)
    assert result["steady_stock"] == 24
    assert [entry["achievement_id"] for entry in result["achievements"]] == ["pond_harvest_20"]
    clock.advance(6 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].generation_score == pytest.approx(before - 1.8)


def test_the_generation_score_never_falls_below_zero():
    service, repository, clock, player = steady_pond_game()
    service.harvest_pond("aquatic-sub", "pond_1", 39)
    clock.advance(20 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].generation_score == 0


def test_harvesting_everything_clears_the_pond_and_the_generation_score():
    service, repository, clock, player = stocked_pond_game(hay=125)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    clock.advance(6 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    stock = repository.get(player.player_id).ponds[0].stock

    result = service.harvest_pond("aquatic-sub", "pond_1", stock)["result"]
    assert result["stock"] == 0
    saved = repository.get(player.player_id)
    assert saved.ponds[0].species_id == ""
    assert saved.ponds[0].generation_score == 0
    assert sum(saved.inventory["pond_crucian"].values()) == stock


def test_harvest_costs_no_stamina_and_reads_the_feed_score_into_quality():
    service, repository, clock, player = stocked_pond_game(hay=100)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    fill_stamina(repository, player.player_id, 30)
    clock.advance(6 * HOUR)
    service.snapshot_by_sub("aquatic-sub")

    before = service.snapshot_by_sub("aquatic-sub")["player"]["stamina"]
    result = service.harvest_pond("aquatic-sub", "pond_1", 3)["result"]
    saved = repository.get(player.player_id)
    assert saved.stamina == before
    assert result["quality_ability"] == pytest.approx(
        saved.feed_slot.quality_score + saved.ponds[0].generation_score,
        abs=0.01,
    )


def test_pond_partner_shortens_the_cycle_and_can_be_pulled_out_at_any_time():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    solo_cycle = repository.get(player.player_id).ponds[0].cycle_seconds

    assigned = service.assign_pond_partner("aquatic-sub", "pond_1", "angler")["result"]
    assert assigned["cycle_seconds"] < solo_cycle
    assert service.assign_pond_partner("aquatic-sub", "pond_1", "")["result"]["cycle_seconds"] == solo_cycle


def test_changing_the_partner_settles_the_old_segment_first():
    service, repository, clock, player = stocked_pond_game(hay=125)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    clock.advance(4 * HOUR)
    service.assign_pond_partner("aquatic-sub", "pond_1", "angler")
    pond = repository.get(player.player_id).ponds[0]
    # 换伙伴之前那四个小时按旧能力（周期 7200 秒）结算，不是按新周期回溯。
    assert pond.fry[0].cycles_left == pytest.approx(1.0)
    assert pond.last_settled_at == clock.now
    assert pond.settle_remainder == 0


def test_the_pond_partner_earns_experience_per_settled_cycle():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    service.assign_pond_partner("aquatic-sub", "pond_1", "angler")
    before = next(entry for entry in repository.get(player.player_id).owned_partners if entry.partner_id == "angler")
    baseline = before.experience + sum(
        service.content.partner_growth.experience_for_next_level(level)
        for level in range(1, before.level)
    )
    cycle = repository.get(player.player_id).ponds[0].cycle_seconds

    clock.advance(5 * cycle)
    service.snapshot_by_sub("aquatic-sub")
    owned = next(entry for entry in repository.get(player.player_id).owned_partners if entry.partner_id == "angler")
    total = owned.experience + sum(
        service.content.partner_growth.experience_for_next_level(level)
        for level in range(1, owned.level)
    )
    # 五个周期 × 每周期 3 点。看塘不花体力，经验按结算掉的周期算。
    assert total - baseline == 15


def test_a_stalled_pond_pays_the_partner_nothing():
    service, repository, clock, player = stocked_pond_game(hay=1)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    service.assign_pond_partner("aquatic-sub", "pond_1", "angler")
    clock.advance(40 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    stalled = next(entry for entry in repository.get(player.player_id).owned_partners if entry.partner_id == "angler")
    clock.advance(40 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    after = next(entry for entry in repository.get(player.player_id).owned_partners if entry.partner_id == "angler")
    # 槽早就空了，停摆的这段时间既不推进也不给经验。
    assert (after.level, after.experience) == (stalled.level, stalled.experience)


def test_the_pond_partner_can_be_pulled_out_and_put_back():
    service, repository, _, player = stocked_pond_game()
    service.assign_pond_partner("aquatic-sub", "pond_1", "angler")
    assert repository.get(player.player_id).ponds[0].assigned_partner_ids == ["angler"]
    service.assign_pond_partner("aquatic-sub", "pond_1", "")
    assert repository.get(player.player_id).ponds[0].assigned_partner_ids == []


# --------------------------------------------------------------------- 天赋


def test_talent_modifiers_raise_the_combo_cap_and_the_generation_cap():
    service, repository, clock, player = stocked_pond_game(fry=20, hay=125)
    set_level(repository, player.player_id, 16)
    service.snapshot_by_sub("aquatic-sub")
    for node_id in (
        "aquatic_ability_1",
        "aquatic_combo_1",
        "aquatic_ability_2",
        "aquatic_roster_1",
        "aquatic_generation_1",
    ):
        service.unlock_talent("aquatic-sub", node_id)

    saved = repository.get(player.player_id)
    assert service._combo_cap(saved) == 15
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 20)
    clock.advance(30 * 24 * HOUR)
    service.snapshot_by_sub("aquatic-sub")
    assert repository.get(player.player_id).ponds[0].generation_score == 80


def test_harvest_quality_floor_talent_guarantees_one_good_fish():
    service, repository, clock, player = stocked_pond_game(hay=125)
    set_level(repository, player.player_id, 16)
    service.snapshot_by_sub("aquatic-sub")
    for node_id in (
        "aquatic_ability_1",
        "aquatic_combo_1",
        "aquatic_ability_2",
        "aquatic_roster_1",
        "aquatic_generation_1",
        "aquatic_harvest_floor_1",
    ):
        service.unlock_talent("aquatic-sub", node_id)
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 10)
    clock.advance(6 * HOUR)
    service.snapshot_by_sub("aquatic-sub")

    result = service.harvest_pond("aquatic-sub", "pond_1", 5)["result"]
    assert max(drop["quality"] for drop in result["drops"]) >= 3


# --------------------------------------------------------------------- 快照


def test_snapshot_exposes_the_aquatic_block():
    service, repository, clock, player = stocked_pond_game()
    service.stock_pond("aquatic-sub", "pond_1", "crucian", 5)
    state = service.snapshot_by_sub("aquatic-sub")
    aquatic = state["aquatic"]
    assert aquatic["unlocked"] is True
    assert [spot["id"] for spot in aquatic["spots"]] == ["town_creek", "maple_lake"]
    assert aquatic["spots"][1]["unlocked"] is True
    assert aquatic["next_spot_level"] is None
    # 钓点不下发产出表和大物，谜底只在图鉴里。
    assert "outputs" not in aquatic["spots"][0]
    assert "big_catch" not in aquatic["spots"][0]
    assert aquatic["ponds"][0]["stock"] == 0
    assert aquatic["ponds"][0]["fry_total"] == 5
    assert aquatic["ponds"][0]["steady_stock"] == 24
    assert aquatic["ponds"][0]["next_maturation_seconds"] == 3 * 7200
    assert aquatic["ponds"][0]["capacity"] == 40
    assert aquatic["feed_slot"]["capacity"] == 500
    # 一个周期两份，2 小时一个周期 → 折算成每小时 1 份。
    assert aquatic["feed_slot"]["hourly_rate"] == 1
    assert aquatic["feed_slot"]["runtime_seconds"] > 0
    assert aquatic["codex"]["total"] == 9
    assert all(entry["item_id"] is None for entry in aquatic["codex"]["pool"] if not entry["recorded"])
    assert any(entry["item_id"] == "meadow_hay" for entry in aquatic["feed_slot"]["inputs"]) is False
