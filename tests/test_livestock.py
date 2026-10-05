from __future__ import annotations

import random
from math import ceil

import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain import PlayerState
from red_leaf_town.domain.economy import add_item
from red_leaf_town.domain.livestock import refund_value, roll_gene
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_content import PartnerCatalog, PartnerDefinition


HOUR = 3600
CYCLE = 8 * HOUR


class FixedRandom(random.Random):
    """每次掷点都返回同一个值。品质和特殊产出共用 random()，一个常数就能卡住阈值。"""

    def __init__(self, value: float, seed: int = 11):
        super().__init__(seed)
        self.value = value

    def random(self) -> float:
        return self.value


class GaussRandom(random.Random):
    """按顺序吐出预设的高斯值，用来验证基因重投取的是较高的那个。"""

    def __init__(self, values: list[float]):
        super().__init__(11)
        self.values = list(values)
        self.index = 0

    def gauss(self, mu: float, sigma: float) -> float:
        value = self.values[self.index % len(self.values)]
        self.index += 1
        return value


class Clock:
    def __init__(self, now: int = 1_700_000_000):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds: int):
        self.now += seconds


def partner(partner_id: str, industry: str = "livestock", traits: list[str] | None = None) -> PartnerDefinition:
    return PartnerDefinition.model_validate({
        "id": partner_id,
        "name": partner_id,
        "rarity": 4,
        "growth_curve": "linear",
        "tendencies": [{"industry": industry, "level_1": 40, "level_60": 160}],
        "trait_codes": list(traits or []),
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
        partner("herder", "livestock"),
        partner("angler", "aquatic"),
        partner("keeper", "livestock", ["herding_heart"]),
        partner("hoarder", "livestock", ["full_larder"]),
        partner("slowpoke", "livestock", ["unhurried"]),
        partner("groomer", "livestock", ["fine_combing"]),
        partner("indulger", "livestock", ["generous_keep"]),
        partner("matchmaker_partner", "livestock", ["matchmaker"]),
    ])
    service = GameService(
        content,
        repository,
        clock=clock,
        rng=rng or random.Random(11),
        partner_catalog_loader=lambda: catalog,
    )
    player = service.ensure_player("stock-sub", "牧场居民")
    for entry in catalog.partners:
        service.admin_grant_partner(player.player_id, entry.id)
    return service, repository, clock, player


def set_level(repository, player_id: str, level: int):
    content = repository.content
    total_xp = next(entry.total_xp for entry in content.levels if entry.level == level)
    repository.update(player_id, lambda state: setattr(state, "experience", total_xp))


def give(repository, player_id: str, item_id: str, quantity: int, quality: int = 0):
    repository.update(player_id, lambda state: add_item(state, item_id, quantity, quality))


def set_coins(repository, player_id: str, coins: int):
    repository.update(player_id, lambda state: setattr(state, "coins", coins))


def fill_feed(repository, player_id: str, units: float = 4000, score: float = 60):
    def mutation(state: PlayerState):
        state.feed_slot.units = units
        state.feed_slot.quality_score = score
    repository.update(player_id, mutation)


def fill_stamina(repository, player_id: str, amount: int = 60):
    repository.update(player_id, lambda state: setattr(state, "stamina", amount))


@pytest.fixture
def game():
    return build_game()


@pytest.fixture
def ranch():
    """一个到 12 级、鸡舍和畜栏都建好、饲料管够的牧场。"""

    service, repository, clock, player = build_game()
    set_level(repository, player.player_id, 12)
    set_coins(repository, player.player_id, 200_000)
    give(repository, player.player_id, "maple_wood", 30)
    give(repository, player.player_id, "maple_plank", 30)
    service.build_livestock_facility("stock-sub", "coop_1")
    service.build_livestock_facility("stock-sub", "barn_1")
    fill_feed(repository, player.player_id)
    return service, repository, clock, player


def facility_of(state: dict, facility_id: str) -> dict:
    return next(entry for entry in state["livestock"]["facilities"] if entry["facility_id"] == facility_id)


# --------------------------------------------------------------------- 内容


def test_livestock_content_loads_with_rules_facilities_and_species():
    content = load_content()
    assert "livestock" in content.industries
    rules = content.livestock
    assert rules is not None and rules.cycle_seconds == CYCLE
    # 周期是硬常量，任何能力都压不动它。
    assert [entry.id for entry in content.livestock_facilities] == ["free_range", "coop_1", "barn_1"]
    assert [entry.id for entry in content.livestock_species] == ["chicken", "cow", "duck", "sheep"]
    coop = content.livestock_facility_map["coop_1"]
    assert coop.replaces == "free_range" and coop.tier(1).build_coins == 3000
    assert [(entry.item_id, entry.quantity) for entry in coop.tier(1).build_materials] == [("maple_wood", 30)]
    barn = content.livestock_facility_map["barn_1"]
    assert barn.tier(1).build_coins == 10000
    assert [(entry.item_id, entry.quantity) for entry in barn.tier(1).build_materials] == [("maple_plank", 30)]
    # 散养地是过渡设施：容量小、品质打折、溢出窗口更窄。
    free_range = content.livestock_facility_map["free_range"]
    assert free_range.granted and free_range.tier(1).capacity == 2
    assert free_range.tier(1).quality_multiplier == 0.8
    assert free_range.tier(1).gene_cap == 40 and coop.tier(1).gene_cap == 70


def test_livestock_species_numbers_hold_the_balance_line():
    content = load_content()
    chicken = content.livestock_species_map["chicken"]
    cow = content.livestock_species_map["cow"]
    assert (chicken.purchase_price, chicken.growth_cycles, chicken.feed_per_cycle) == (500, 3, 8)
    assert (cow.purchase_price, cow.growth_cycles, cow.feed_per_cycle) == (4000, 9, 24)
    assert content.item_map["egg"].sell_price == 45
    assert content.item_map["milk"].sell_price == 100
    # 特殊产出没有品质，靠售价本身撑起价值。
    assert content.item_map["golden_egg"].has_quality is False
    assert content.item_map["rich_milk"].has_quality is False
    # 满编一天 4 鸡 + 4 牛 = 384 份，槽扩容后才撑得过两天。
    daily = (4 * chicken.feed_per_cycle + 4 * cow.feed_per_cycle) * 3
    assert daily == 384


def test_feed_slot_capacity_grows_with_the_facilities(ranch):
    service, repository, _, player = ranch
    state = service.snapshot_by_sub("stock-sub")
    assert state["aquatic"]["feed_slot"]["base_capacity"] == 500
    assert state["aquatic"]["feed_slot"]["capacity"] == 1100


# ----------------------------------------------------------------- 设施建造


def test_free_range_arrives_with_the_level_and_is_absorbed_by_the_coop(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 4)
    state = service.snapshot_by_sub("stock-sub")
    assert [entry["facility_id"] for entry in state["livestock"]["facilities"]] == ["free_range"]

    set_coins(repository, player.player_id, 5000)
    give(repository, player.player_id, "maple_wood", 30)
    set_level(repository, player.player_id, 8)
    service.buy_animal("stock-sub", "free_range", "chicken")
    service.buy_animal("stock-sub", "free_range", "chicken")

    result = service.build_livestock_facility("stock-sub", "coop_1")
    assert result["result"]["migrated_animals"] == 2
    assert result["result"]["replaced"] == "free_range"
    facilities = [entry["facility_id"] for entry in result["state"]["livestock"]["facilities"]]
    # 散养地在同一次更新里被回收，鸡整批迁入鸡舍。
    assert facilities == ["coop_1"]
    coop = facility_of(result["state"], "coop_1")
    assert coop["used"] == 2 and coop["capacity"] == 4


def test_building_the_coop_costs_coins_and_materials(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 8)
    set_coins(repository, player.player_id, 3000)
    with pytest.raises(GameError, match="枫木"):
        service.build_livestock_facility("stock-sub", "coop_1")
    give(repository, player.player_id, "maple_wood", 30)
    result = service.build_livestock_facility("stock-sub", "coop_1")
    assert result["result"]["coins"] == 0
    assert sum(result["state"]["inventory"] and [0]) == 0
    stored = repository.get(player.player_id)
    assert sum(stored.inventory.get("maple_wood", {}).values()) == 0


def test_the_barn_needs_level_twelve(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 11)
    set_coins(repository, player.player_id, 20000)
    give(repository, player.player_id, "maple_plank", 30)
    with pytest.raises(GameError, match="12 级"):
        service.build_livestock_facility("stock-sub", "barn_1")


def test_the_free_range_cannot_be_built_by_hand(game):
    service, repository, _, player = game
    set_level(repository, player.player_id, 8)
    with pytest.raises(GameError, match="自动开放"):
        service.build_livestock_facility("stock-sub", "free_range")


# --------------------------------------------------------------------- 饲养


def test_bought_animals_start_as_juveniles_with_low_genes(ranch):
    service, repository, _, player = ranch
    result = service.buy_animal("stock-sub", "coop_1", "chicken")
    animal = result["result"]["animal"]
    assert animal["stage"] == "juvenile"
    assert 10 <= animal["quality_gene"] <= 30
    assert 10 <= animal["yield_gene"] <= 30
    assert animal["remaining_stage_cycles"] == 3
    assert animal["remaining_stage_seconds"] == 3 * CYCLE


def test_naming_sticks_to_the_animal_on_every_birth(ranch):
    service, repository, clock, player = ranch
    bought = service.buy_animal("stock-sub", "coop_1", "chicken", "阿花")["result"]["animal"]
    assert (bought["nickname"], bought["name"], bought["species_name"]) == ("阿花", "阿花", "鸡")

    give(repository, player.player_id, "egg", 1, quality=3)
    hatched = service.incubate_egg("stock-sub", "coop_1", 3, " 二蛋 ")["result"]["animal"]
    # 首尾空白要修掉，否则列表里会出现看不见的名字。
    assert hatched["nickname"] == "二蛋"

    service.buy_animal("stock-sub", "barn_1", "cow")
    service.buy_animal("stock-sub", "barn_1", "cow")
    clock.advance(9 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    parents = [
        animal.animal_id
        for animal in repository.get(player.player_id).animals
        if animal.species_id == "cow"
    ]
    calf = service.breed_animals("stock-sub", "barn_1", parents, "小牛犊")["result"]["animal"]
    assert calf["nickname"] == "小牛犊"


def test_an_unnamed_animal_falls_back_to_the_species_name(ranch):
    service, *_ = ranch
    animal = service.buy_animal("stock-sub", "coop_1", "chicken")["result"]["animal"]
    assert animal["nickname"] == "" and animal["name"] == "鸡"


def test_an_overlong_name_is_refused(ranch):
    service, *_ = ranch
    with pytest.raises(GameError, match="最多"):
        service.buy_animal("stock-sub", "coop_1", "chicken", "名" * 13)


def test_a_barn_will_not_take_poultry(ranch):
    service, *_ = ranch
    with pytest.raises(GameError, match="养不了"):
        service.buy_animal("stock-sub", "barn_1", "chicken")


def test_capacity_is_a_hard_stop(ranch):
    service, *_ = ranch
    for _ in range(4):
        service.buy_animal("stock-sub", "coop_1", "chicken")
    with pytest.raises(GameError, match="住满"):
        service.buy_animal("stock-sub", "coop_1", "chicken")


def test_growth_and_production_run_on_the_eight_hour_cycle(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")

    clock.advance(2 * CYCLE)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert coop["animals"][0]["stage"] == "juvenile"
    assert coop["animals"][0]["remaining_stage_cycles"] == 1

    clock.advance(CYCLE)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert coop["animals"][0]["stage"] == "adult"
    # 成年的那个周期用来长大，产出从下一个周期开始。
    assert coop["animals"][0]["pending_total"] == 0

    clock.advance(2 * CYCLE)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert coop["animals"][0]["pending_total"] == 2


def test_collecting_moves_the_pending_output_into_the_bag(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    clock.advance(6 * CYCLE)
    result = service.collect_livestock("stock-sub", "coop_1")
    assert result["result"]["collected"] == 3
    stored = repository.get(player.player_id)
    assert sum(stored.inventory.get("egg", {}).values()) == 3
    assert all(1 <= quality <= 5 for quality in stored.inventory["egg"])
    with pytest.raises(GameError, match="收取"):
        service.collect_livestock("stock-sub", "coop_1")


def test_production_stops_at_the_overflow_cap_and_stops_eating(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    clock.advance(3 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    before = repository.get(player.player_id).feed_slot.units

    # 溢出上限是 3 个周期的量，攒满之后既不产出也不吃料。
    clock.advance(30 * CYCLE)
    state = service.snapshot_by_sub("stock-sub")
    animal = facility_of(state, "coop_1")["animals"][0]
    assert animal["overflow_cap"] == ceil(animal["yield_per_cycle"] * 3)
    assert animal["pending_total"] == animal["overflow_cap"]
    assert animal["saturated"] is True
    spent = before - repository.get(player.player_id).feed_slot.units
    # 只为攒满那几个周期买了单，剩下 20 多个周期一份没烧。
    assert spent <= animal["overflow_cap"] * 8


def test_an_empty_feed_slot_stalls_the_whole_barn(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    clock.advance(3 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    fill_feed(repository, player.player_id, units=0, score=0)

    clock.advance(5 * CYCLE)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert coop["stalled"] is True
    assert coop["animals"][0]["pending_total"] == 0

    # 断粮期间没有额外惩罚：补料之后从停摆点原样继续。
    fill_feed(repository, player.player_id, units=200, score=30)
    service.snapshot_by_sub("stock-sub")
    clock.advance(CYCLE)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert coop["stalled"] is False
    assert coop["animals"][0]["pending_total"] == 2


def test_growth_pauses_while_the_slot_is_empty(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    fill_feed(repository, player.player_id, units=0, score=0)
    clock.advance(10 * CYCLE)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    # 用墙上时钟算的话这只鸡早成年了，累计有效周期才是对的口径。
    assert coop["animals"][0]["stage"] == "juvenile"
    assert coop["animals"][0]["remaining_stage_cycles"] == 3


def test_stalling_at_a_cycle_boundary_keeps_the_completed_cycle(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")

    almost_three_cycles = 3 * CYCLE - 52 * 60
    clock.advance(almost_three_cycles)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert coop["animals"][0]["remaining_stage_cycles"] == 1
    assert coop["next_cycle_seconds"] == 52 * 60

    fill_feed(repository, player.player_id, units=0, score=0)
    clock.advance(52 * 60)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")

    assert coop["stalled"] is True
    assert coop["animals"][0]["stage"] == "juvenile"
    assert coop["animals"][0]["remaining_stage_cycles"] == 1
    assert coop["settle_remainder"] == CYCLE
    assert coop["next_cycle_seconds"] == 0


@pytest.mark.parametrize("refresh_before_deposit", [False, True])
def test_refilling_feed_immediately_finishes_only_the_waiting_cycle(ranch, refresh_before_deposit):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    clock.advance(3 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    fill_feed(repository, player.player_id, units=0, score=0)
    give(repository, player.player_id, "meadow_hay", 100)
    clock.advance(10 * CYCLE)
    if refresh_before_deposit:
        for _ in range(3):
            coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
            assert coop["stalled"] is True
            assert coop["animals"][0]["pending_total"] == 0
            clock.advance(CYCLE)

    insufficient = service.deposit_feed("stock-sub", "meadow_hay", 0, 1)
    assert facility_of(insufficient["state"], "coop_1")["stalled"] is True
    result = service.deposit_feed("stock-sub", "meadow_hay", 0, 99)
    coop = facility_of(result["state"], "coop_1")
    assert coop["stalled"] is False
    assert coop["animals"][0]["pending_total"] == 1
    assert coop["next_cycle_seconds"] == CYCLE
    assert result["state"]["aquatic"]["feed_slot"]["units"] == 392
    for _ in range(3):
        refreshed = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
        assert refreshed["animals"][0]["pending_total"] == 1
    clock.advance(CYCLE)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert coop["animals"][0]["pending_total"] == 2


def test_a_long_offline_stretch_settles_in_one_pass(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    clock.advance(30 * 24 * HOUR)
    state = service.snapshot_by_sub("stock-sub")
    animal = facility_of(state, "coop_1")["animals"][0]
    assert animal["stage"] == "adult"
    # 离线一个月和离线一天走同一条代码路径，且被溢出上限截住。
    assert animal["pending_total"] == animal["overflow_cap"]
    assert repository.get(player.player_id).feed_slot.units > 0


def test_ponds_and_barns_share_the_feed_slot_proportionally(ranch):
    service, repository, clock, player = ranch
    service.build_pond("stock-sub", "pond_1")
    give(repository, player.player_id, "fish_fry", 5)
    service.stock_pond("stock-sub", "pond_1", "crucian", 5)
    service.buy_animal("stock-sub", "coop_1", "chicken")
    fill_feed(repository, player.player_id, units=20, score=30)

    clock.advance(4 * CYCLE)
    state = service.snapshot_by_sub("stock-sub")
    # 两边一起报需求、按同一个比例拿预算，谁都不会因为结算顺序单方面被饿死。
    coop = facility_of(state, "coop_1")
    pond = state["aquatic"]["ponds"][0]
    assert coop["stalled"] is True and pond["stalled"] is True
    # 槽里最多只剩买不起下一个周期的零头。
    assert repository.get(player.player_id).feed_slot.units < 8


# --------------------------------------------------------------------- 照料


def test_caring_spends_stamina_for_affection_and_experience(ranch):
    service, repository, clock, player = ranch
    fill_stamina(repository, player.player_id, 20)
    service.buy_animal("stock-sub", "coop_1", "chicken")
    animal_id = repository.get(player.player_id).animals[0].animal_id
    before = repository.get(player.player_id).experience

    result = service.care_animal("stock-sub", animal_id)["result"]
    assert result["stamina_cost"] == 1 and result["experience"] == 10
    assert result["affection"] == 8
    stored = repository.get(player.player_id)
    assert stored.experience == before + 10
    assert stored.stamina == 19


def test_caring_is_capped_per_animal_per_day(ranch):
    service, repository, clock, player = ranch
    fill_stamina(repository, player.player_id, 20)
    service.buy_animal("stock-sub", "coop_1", "chicken")
    animal_id = repository.get(player.player_id).animals[0].animal_id
    for _ in range(3):
        service.care_animal("stock-sub", animal_id)
    with pytest.raises(GameError, match="照料过"):
        service.care_animal("stock-sub", animal_id)

    clock.advance(24 * HOUR)
    fill_stamina(repository, player.player_id, 20)
    assert service.care_animal("stock-sub", animal_id)["result"]["cared_today"] == 1


def test_affection_multiplies_quality_between_zero_point_nine_five_and_one_point_two(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    state = service.snapshot_by_sub("stock-sub")
    animal = facility_of(state, "coop_1")["animals"][0]
    assert animal["affection_multiplier"] == pytest.approx(0.95)
    bare = animal["quality_ability"]

    def max_affection(stored: PlayerState):
        stored.animals[0].affection = 100
    repository.update(player.player_id, max_affection)
    state = service.snapshot_by_sub("stock-sub")
    animal = facility_of(state, "coop_1")["animals"][0]
    assert animal["affection_multiplier"] == pytest.approx(1.2)
    # 乘算的含义就是「放大你已经堆出来的底子」。
    assert animal["quality_ability"] == pytest.approx(bare / 0.95 * 1.2, abs=0.02)


def test_the_free_range_multiplier_drags_quality_down(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 4)
    set_coins(repository, player.player_id, 5000)
    fill_feed(repository, player.player_id, units=500, score=50)
    service.buy_animal("stock-sub", "free_range", "chicken")
    state = service.snapshot_by_sub("stock-sub")
    facility = facility_of(state, "free_range")
    assert facility["quality_multiplier"] == 0.8
    animal = facility["animals"][0]
    expected = (50 + 0.6 * animal["quality_gene"]) * 0.8 * 0.95
    assert animal["quality_ability"] == pytest.approx(expected, abs=0.01)


# --------------------------------------------------------------------- 繁殖


def test_incubating_an_egg_turns_quality_into_genes(ranch):
    service, repository, clock, player = ranch
    give(repository, player.player_id, "egg", 1, quality=4)
    result = service.incubate_egg("stock-sub", "coop_1", 4)["result"]
    chick = result["animal"]
    assert chick["stage"] == "incubating"
    assert chick["remaining_stage_cycles"] == 2
    # 臻品蛋的基准是 60，正态抖动之后仍应明显高于买来的 10~30。
    assert chick["quality_gene"] >= 40
    stored = repository.get(player.player_id)
    assert sum(stored.inventory.get("egg", {}).values()) == 0

    clock.advance(2 * CYCLE)
    coop = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert coop["animals"][0]["stage"] == "juvenile"


def test_incubation_takes_a_slot_and_a_scoop_of_feed(ranch):
    service, repository, clock, player = ranch
    give(repository, player.player_id, "egg", 5, quality=1)
    before = repository.get(player.player_id).feed_slot.units
    service.incubate_egg("stock-sub", "coop_1", 1)
    assert repository.get(player.player_id).feed_slot.units == before - 20
    for _ in range(3):
        service.incubate_egg("stock-sub", "coop_1", 1)
    with pytest.raises(GameError, match="住满"):
        service.incubate_egg("stock-sub", "coop_1", 1)


def test_genes_are_capped_by_the_facility_breed_cap(game):
    service, repository, clock, player = game
    set_level(repository, player.player_id, 4)
    fill_feed(repository, player.player_id, units=500, score=50)
    give(repository, player.player_id, "egg", 1, quality=5)
    result = service.incubate_egg("stock-sub", "free_range", 5)["result"]
    # 奇迹蛋的基准是 80，但散养地的品种上限只有 40。
    assert result["animal"]["quality_gene"] <= 40 + 15
    assert result["animal"]["gene_cap"] == 40


def test_breeding_needs_two_adult_cows_and_a_decent_slot(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "barn_1", "cow")
    service.buy_animal("stock-sub", "barn_1", "cow")
    parents = [animal.animal_id for animal in repository.get(player.player_id).animals]

    with pytest.raises(GameError, match="成年"):
        service.breed_animals("stock-sub", "barn_1", parents)

    clock.advance(9 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    fill_feed(repository, player.player_id, units=500, score=20)
    with pytest.raises(GameError, match="品质分"):
        service.breed_animals("stock-sub", "barn_1", parents)

    fill_feed(repository, player.player_id, units=500, score=60)
    result = service.breed_animals("stock-sub", "barn_1", parents)["result"]
    assert result["cooldown_cycles"] == 9
    calf = result["animal"]
    assert calf["stage"] == "juvenile" and calf["remaining_stage_cycles"] == 9
    stored = repository.get(player.player_id)
    assert len(stored.animals) == 3
    assert all(animal.breeding_cooldown == 9 for animal in stored.animals if animal.animal_id in parents)

    with pytest.raises(GameError, match="冷却"):
        service.breed_animals("stock-sub", "barn_1", parents)


def test_breeding_cooldown_burns_down_with_fed_cycles(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "barn_1", "cow")
    service.buy_animal("stock-sub", "barn_1", "cow")
    clock.advance(9 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    parents = [animal.animal_id for animal in repository.get(player.player_id).animals]
    service.breed_animals("stock-sub", "barn_1", parents)

    clock.advance(9 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    stored = repository.get(player.player_id)
    assert all(animal.breeding_cooldown == 0 for animal in stored.animals if animal.animal_id in parents)


def test_breeding_will_not_mix_species_or_facilities(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.buy_animal("stock-sub", "barn_1", "cow")
    ids = [animal.animal_id for animal in repository.get(player.player_id).animals]
    with pytest.raises(GameError, match="不在这处设施"):
        service.breed_animals("stock-sub", "barn_1", ids)


def test_gene_inheritance_drifts_up_but_respects_the_cap():
    rng = random.Random(3)
    values = [roll_gene(rng, 60, 8, 70, 0.0, 15) for _ in range(200)]
    assert max(values) <= 70
    assert 55 <= sum(values) / len(values) <= 65
    # 突变可以顶破品种上限，但硬顶永远是 100。
    always = random.Random(5)
    mutated = [roll_gene(always, 70, 8, 70, 1.0, 15) for _ in range(50)]
    assert max(mutated) <= 100 and max(mutated) > 70


# --------------------------------------------------------------------- 出售


def test_selling_pays_more_for_better_genes():
    assert refund_value(2000, 0, 0, 0.5) == 2000
    assert refund_value(2000, 100, 100, 0.5) == 3000
    assert refund_value(250, 100, 100, 0.5) == 375


def test_selling_an_animal_frees_the_slot(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    animal_id = repository.get(player.player_id).animals[0].animal_id
    coins = repository.get(player.player_id).coins
    result = service.sell_animal("stock-sub", animal_id)["result"]
    assert result["price"] >= 250
    stored = repository.get(player.player_id)
    assert stored.animals == []
    assert stored.coins == coins + result["price"]


def test_selling_refuses_to_throw_away_pending_output(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    clock.advance(5 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    animal_id = repository.get(player.player_id).animals[0].animal_id
    with pytest.raises(GameError, match="收了"):
        service.sell_animal("stock-sub", animal_id)


# --------------------------------------------------------------------- 伙伴


def test_a_resident_partner_lifts_quality_and_earns_experience(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    bare = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")["ability"]
    service.assign_livestock_partner("stock-sub", "coop_1", "herder")
    staffed = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert staffed["ability"] > bare
    assert [entry["partner_id"] for entry in staffed["assigned_partners"]] == ["herder"]

    before = next(
        entry.experience for entry in repository.get(player.player_id).owned_partners
        if entry.partner_id == "herder"
    )
    clock.advance(2 * CYCLE)
    service.snapshot_by_sub("stock-sub")
    after_partner = next(
        entry for entry in repository.get(player.player_id).owned_partners
        if entry.partner_id == "herder"
    )
    assert after_partner.experience != before or after_partner.level > 1


def test_a_partner_without_the_tendency_is_refused(ranch):
    service, *_ = ranch
    with pytest.raises(GameError, match="畜牧倾向"):
        service.assign_livestock_partner("stock-sub", "coop_1", "angler")


def test_a_partner_can_only_stand_in_one_production_slot(ranch):
    service, repository, clock, player = ranch
    service.assign_livestock_partner("stock-sub", "coop_1", "herder")
    service.assign_livestock_partner("stock-sub", "barn_1", "herder")
    stored = repository.get(player.player_id)
    assigned = {
        facility.facility_id: facility.assigned_partner_ids
        for facility in stored.livestock_facilities
    }
    assert assigned["coop_1"] == [] and assigned["barn_1"] == ["herder"]


# ----------------------------------------------------------------- 换人的自由窗口


def test_a_partner_can_be_swapped_freely_in_the_first_tenth_of_the_cycle(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")

    clock.advance(int(CYCLE * 0.09))
    result = service.assign_livestock_partner("stock-sub", "coop_1", "keeper")["result"]

    assert result["queued"] is False
    facility = stored_facility(repository, player.player_id, "coop_1")
    assert facility.assigned_partner_ids == ["keeper"]
    assert facility.pending_partner_ids is None
    assert facility.quality_bonus == 20


def test_after_the_window_the_swap_queues_and_the_running_cycle_keeps_the_old_hand(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")

    clock.advance(int(CYCLE * 0.5))
    result = service.assign_livestock_partner("stock-sub", "coop_1", "keeper")["result"]

    assert result["queued"] is True
    assert result["effective_in_seconds"] == pytest.approx(CYCLE * 0.5, abs=1)
    facility = stored_facility(repository, player.player_id, "coop_1")
    # 这个周期还是原来的安排，特性快照一点没动。
    assert facility.assigned_partner_ids == []
    assert facility.pending_partner_ids == ["keeper"]
    assert facility.quality_bonus == 0


def test_the_queued_partner_takes_over_when_the_next_cycle_starts(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")

    clock.advance(int(CYCLE * 0.5))
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")
    clock.advance(int(CYCLE * 0.5))
    service.snapshot_by_sub("stock-sub")

    facility = stored_facility(repository, player.player_id, "coop_1")
    assert facility.assigned_partner_ids == ["keeper"]
    assert facility.pending_partner_ids is None
    assert facility.quality_bonus == 20


def test_going_offline_only_costs_the_queued_partner_the_cycle_that_was_running(ranch):
    """离线跨了几个周期时，排队的人在第一个边界就上岗，不是等这一整段跑完。"""

    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "barn_1", "cow")
    per_cycle = service.content.livestock_species_map["cow"].feed_per_cycle
    fill_feed(repository, player.player_id, units=1000, score=60)
    before = repository.get(player.player_id).feed_slot.units

    clock.advance(int(CYCLE * 0.5))
    service.assign_livestock_partner("stock-sub", "barn_1", "hoarder")
    clock.advance(3 * CYCLE)
    service.snapshot_by_sub("stock-sub")

    facility = stored_facility(repository, player.player_id, "barn_1")
    assert facility.assigned_partner_ids == ["hoarder"]
    # 第 1 个周期按原价，后面两个吃到囤仓的 −20%。
    spent = before - repository.get(player.player_id).feed_slot.units
    assert spent == pytest.approx(per_cycle + 2 * per_cycle * 0.8)


def test_re_queueing_the_same_facility_is_always_free(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")

    clock.advance(int(CYCLE * 0.5))
    # 预约还没生效，改主意换个人、或者改成撤下，都不该被窗口拦住。
    service.assign_livestock_partner("stock-sub", "coop_1", "herder")
    assert stored_facility(repository, player.player_id, "coop_1").pending_partner_ids == ["herder"]
    service.assign_livestock_partner("stock-sub", "coop_1", "")
    assert stored_facility(repository, player.player_id, "coop_1").pending_partner_ids == []


def test_picking_the_partner_on_duty_again_cancels_the_queued_swap(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")

    clock.advance(int(CYCLE * 0.5))
    service.assign_livestock_partner("stock-sub", "coop_1", "herder")
    assert stored_facility(repository, player.player_id, "coop_1").pending_partner_ids == ["herder"]

    # 又点回在岗的 keeper：取消换人，而不是被自己这一栏的周期挡下来。
    result = service.assign_livestock_partner("stock-sub", "coop_1", "keeper")["result"]
    assert (result["cancelled"], result["queued"]) == (True, False)
    facility = stored_facility(repository, player.player_id, "coop_1")
    assert facility.assigned_partner_ids == ["keeper"]
    assert facility.pending_partner_ids is None

    clock.advance(int(CYCLE * 0.5))
    service.snapshot_by_sub("stock-sub")
    assert stored_facility(repository, player.player_id, "coop_1").assigned_partner_ids == ["keeper"]


def test_picking_solo_again_cancels_a_queued_arrival(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")

    clock.advance(int(CYCLE * 0.5))
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")
    result = service.assign_livestock_partner("stock-sub", "coop_1", "")["result"]

    assert result["cancelled"] is True
    facility = stored_facility(repository, player.player_id, "coop_1")
    assert (facility.assigned_partner_ids, facility.pending_partner_ids) == ([], None)


def test_taking_the_partner_off_duty_queues_too(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")

    clock.advance(int(CYCLE * 0.5))
    result = service.assign_livestock_partner("stock-sub", "coop_1", "")["result"]

    assert result["queued"] is True
    facility = stored_facility(repository, player.player_id, "coop_1")
    # 撤下也排队：旧伙伴干完这个周期，加成也照给到周期末。
    assert facility.assigned_partner_ids == ["keeper"]
    assert facility.pending_partner_ids == []
    assert facility.quality_bonus == 20

    clock.advance(int(CYCLE * 0.5))
    service.snapshot_by_sub("stock-sub")
    facility = stored_facility(repository, player.player_id, "coop_1")
    assert facility.assigned_partner_ids == []
    assert facility.quality_bonus == 0


def test_a_partner_on_duty_past_the_window_cannot_be_pulled_to_another_facility(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.buy_animal("stock-sub", "barn_1", "cow")
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")

    clock.advance(int(CYCLE * 0.5))
    with pytest.raises(GameError) as excinfo:
        service.assign_livestock_partner("stock-sub", "barn_1", "keeper")

    assert excinfo.value.code == "partner_cycle_locked"
    assert stored_facility(repository, player.player_id, "coop_1").assigned_partner_ids == ["keeper"]


def test_a_queued_partner_cannot_be_sent_anywhere_else(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.buy_animal("stock-sub", "barn_1", "cow")

    clock.advance(int(CYCLE * 0.5))
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")
    # barn_1 自己还在窗口内，但被预约的人不能去干别的事。
    with pytest.raises(GameError) as excinfo:
        service.assign_livestock_partner("stock-sub", "barn_1", "keeper")

    assert excinfo.value.code == "partner_swap_reserved"


def test_an_empty_or_stalled_facility_can_always_swap(ranch):
    service, repository, clock, player = ranch

    # 空栏没有在跑的周期。
    clock.advance(int(CYCLE * 0.5))
    assert service.assign_livestock_partner("stock-sub", "coop_1", "keeper")["result"]["queued"] is False

    # 断粮停摆的栏也一样：它什么都没在产。畜牧编制只有一个位子，先把空栏腾出来。
    service.assign_livestock_partner("stock-sub", "coop_1", "")
    service.buy_animal("stock-sub", "barn_1", "cow")
    fill_feed(repository, player.player_id, units=0, score=0)
    clock.advance(int(CYCLE * 1.5))
    service.snapshot_by_sub("stock-sub")
    assert stored_facility(repository, player.player_id, "barn_1").stalled is True
    assert service.assign_livestock_partner("stock-sub", "barn_1", "hoarder")["result"]["queued"] is False


def test_a_queued_swap_still_counts_against_the_livestock_headcount(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.buy_animal("stock-sub", "barn_1", "cow")

    clock.advance(int(CYCLE * 0.5))
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")
    # 编制算的是【最终会在岗的人】，否则排队能把编制上限绕过去。
    with pytest.raises(GameError) as excinfo:
        service.assign_livestock_partner("stock-sub", "barn_1", "hoarder")

    assert excinfo.value.code == "partner_capacity_reached"


# --------------------------------------------------------------------- 伙伴特性


def stored_facility(repository, player_id: str, facility_id: str):
    return next(
        facility for facility in repository.get(player_id).livestock_facilities
        if facility.facility_id == facility_id
    )


def test_herding_heart_adds_quality_that_the_facility_and_affection_still_scale(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")

    facility = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    animal = facility["animals"][0]
    assert facility["quality_bonus"] == 20
    # 特性的加算和能力、饲料分同级，一起吃设施系数和亲密度系数。
    expected = (facility["ability"] + 60 + 0.6 * animal["quality_gene"] + 20) * 1.0 * 0.95
    assert animal["quality_ability"] == pytest.approx(expected, abs=0.01)


def test_full_larder_cuts_the_feed_the_dry_run_and_the_settlement_agree_on(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "barn_1", "cow")
    service.assign_livestock_partner("stock-sub", "barn_1", "hoarder")
    # 一头奶牛原本两个周期要吃 48 份，−20% 之后刚好 38.4 份。
    fill_feed(repository, player.player_id, units=38.4, score=60)

    clock.advance(2 * CYCLE)
    state = service.snapshot_by_sub("stock-sub")

    facility = facility_of(state, "barn_1")
    assert facility["feed_multiplier"] == pytest.approx(0.8)
    assert facility["stalled"] is False
    assert repository.get(player.player_id).feed_slot.units == pytest.approx(0, abs=0.01)
    assert facility["animals"][0]["stage_cycles"] == 2


def test_without_the_trait_the_same_feed_stalls_the_barn(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "barn_1", "cow")
    fill_feed(repository, player.player_id, units=38.4, score=60)

    clock.advance(2 * CYCLE)
    state = service.snapshot_by_sub("stock-sub")

    facility = facility_of(state, "barn_1")
    assert facility["stalled"] is True
    assert facility["animals"][0]["stage_cycles"] == 1


def test_overflow_traits_stack_with_the_talent(ranch):
    service, repository, clock, player = ranch
    service.assign_livestock_partner("stock-sub", "barn_1", "slowpoke")
    assert facility_of(service.snapshot_by_sub("stock-sub"), "barn_1")["overflow_cycles"] == 4

    def unlock(state: PlayerState):
        state.talent_nodes.extend(["livestock_ability_1", "livestock_overflow_1"])
    repository.update(player.player_id, unlock)

    # 溢出上限只减少浪费、不加快产出，所以天赋和特性允许叠加。
    assert facility_of(service.snapshot_by_sub("stock-sub"), "barn_1")["overflow_cycles"] == 5


def test_generous_keep_lifts_the_special_chance_past_a_fixed_roll(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")

    def grow_up(state: PlayerState):
        state.animals[0].stage = "adult"
        state.animals[0].affection = 100
    repository.update(player.player_id, grow_up)

    # 每次掷点都是 0.06：基础 5% 掷不出金蛋，厚养的 7% 掷得出。
    service.rng = FixedRandom(0.06)
    clock.advance(CYCLE)
    assert facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")["pending_special"] == 0

    service.assign_livestock_partner("stock-sub", "coop_1", "indulger")
    clock.advance(CYCLE)
    facility = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")
    assert facility["special_chance_bonus"] == pytest.approx(0.02)
    assert facility["pending_special"] == 1


def test_fine_combing_speeds_up_affection_and_pays_it_back_in_quality(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    service.assign_livestock_partner("stock-sub", "coop_1", "groomer")
    fill_stamina(repository, player.player_id)

    animal_id = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")["animals"][0]["animal_id"]
    result = service.care_animal("stock-sub", animal_id)["result"]
    assert result["affection_gain"] == 12
    assert result["affection"] == 12

    def max_affection(state: PlayerState):
        state.animals[0].affection = 100
    repository.update(player.player_id, max_affection)

    animal = facility_of(service.snapshot_by_sub("stock-sub"), "coop_1")["animals"][0]
    assert animal["affection_multiplier"] == pytest.approx(1.3)


def test_matchmaker_rerolls_each_gene_and_takes_the_higher_one():
    rng = GaussRandom([20.0, 55.0, 30.0])
    assert roll_gene(rng, 30, 8, 70, 0, 15) == 20
    # 第二次多掷了一个 30，取的是较高的 55。
    assert roll_gene(rng, 30, 8, 70, 0, 15, 1) == 55


def test_matchmaker_reaches_breeding_and_incubation(ranch):
    service, repository, clock, player = ranch
    service.assign_livestock_partner("stock-sub", "coop_1", "matchmaker_partner")
    give(repository, player.player_id, "egg", 1, 3)

    result = service.incubate_egg("stock-sub", "coop_1", 3)["result"]

    effects = [entry["effect"] for entry in result["trait_effects"]]
    assert effects == ["gene_rerolls"]


def test_the_elapsed_segment_keeps_the_old_snapshot_and_leaving_clears_it(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    assert stored_facility(repository, player.player_id, "coop_1").quality_bonus == 0

    service.assign_livestock_partner("stock-sub", "coop_1", "keeper")
    assert stored_facility(repository, player.player_id, "coop_1").quality_bonus == 20

    # 换去别的栏也算离开，这一栏的特性快照必须当场抹回中性。
    service.assign_livestock_partner("stock-sub", "barn_1", "keeper")
    assert stored_facility(repository, player.player_id, "coop_1").quality_bonus == 0
    assert stored_facility(repository, player.player_id, "coop_1").trait_effects == []
    assert stored_facility(repository, player.player_id, "barn_1").quality_bonus == 20

    service.assign_livestock_partner("stock-sub", "barn_1", "")
    assert stored_facility(repository, player.player_id, "barn_1").quality_bonus == 0


# --------------------------------------------------------------------- 成就


def achievement_of(state: dict, achievement_id: str) -> dict:
    return next(
        entry for entry in state["achievements"]["entries"]
        if entry["achievement_id"] == achievement_id
    )


def test_livestock_achievements_follow_collecting_caring_and_breeding(ranch):
    service, repository, clock, player = ranch
    service.buy_animal("stock-sub", "coop_1", "chicken")
    fill_stamina(repository, player.player_id)

    def grow_up(state: PlayerState):
        state.animals[0].stage = "adult"
    repository.update(player.player_id, grow_up)
    clock.advance(2 * CYCLE)
    state = service.collect_livestock("stock-sub", "coop_1")["state"]
    assert achievement_of(state, "first_livestock")["completed"] is True

    animal_id = facility_of(state, "coop_1")["animals"][0]["animal_id"]
    state = service.care_animal("stock-sub", animal_id)["state"]
    assert achievement_of(state, "care_thirty")["current"] == 1

    give(repository, player.player_id, "egg", 1, 3)
    state = service.incubate_egg("stock-sub", "coop_1", 3)["state"]
    assert achievement_of(state, "first_breeding")["completed"] is True


def test_full_affection_and_prime_genes_read_the_animals_themselves(ranch):
    service, repository, clock, player = ranch
    for _ in range(4):
        service.buy_animal("stock-sub", "coop_1", "chicken")

    def spoil(state: PlayerState):
        for animal in state.animals:
            animal.affection = 100
        state.animals[0].quality_gene = 60
        state.animals[0].yield_gene = 60
    repository.update(player.player_id, spoil)

    state = service.snapshot_by_sub("stock-sub")
    assert achievement_of(state, "four_familiar_faces")["completed"] is True
    assert achievement_of(state, "prime_bloodline")["completed"] is True


# --------------------------------------------------------------------- 迁移


def test_schema_twenty_four_migration_starts_from_an_empty_ranch():
    started_at = 1_700_000_000
    player = PlayerState.model_validate({
        "schema_version": 23,
        "player_id": "legacy-ranch",
        "oauth_sub": "legacy-ranch-sub",
        "display_name": "旧居民",
        "stamina_updated_at": started_at,
        "created_at": started_at,
        "updated_at": started_at,
    })
    assert player.schema_version == PlayerState.model_fields["schema_version"].default
    assert player.livestock_facilities == []
    assert player.animals == []
