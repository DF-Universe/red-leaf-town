"""畜牧的纯逻辑：分段结算、基因、品质。

与鱼塘同属资产轴，但有两点不同：

1. **周期是全局硬常量**（8 小时）。能力、伙伴、天赋都压不动它，只影响产量与品质。
2. **个体建模**。每只动物各自记成长、亲密度和待收产出，而不是像鱼塘那样只记一个存量。

成长与配种冷却都记成【累计有效周期】，不是时间戳：断粮时整栏停摆，用墙上时钟算
会把停摆的那段白送给成长。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from random import Random

from .models import AnimalState, LivestockFacilityState, SlotState
from .quality import quality_probabilities, roll_quality


MAX_SETTLE_CYCLES = 100_000


@dataclass
class SpeciesParameters:
    """一个物种在这一段时间里生效的参数。"""

    species_id: str
    growth_cycles: int
    incubate_cycles: int
    base_yield: float
    feed_per_cycle: float
    yield_coefficient: float
    special_item_id: str = ""
    special_chance: float = 0.0
    thresholds: tuple[float, float, float, float] = (1, 2, 3, 4)
    width: float = 1.0
    miracle_probability_cap: float = 0.0
    miracle_eligible: bool = False


@dataclass
class FacilityParameters:
    """一个畜栏在这一段时间里生效的参数快照。"""

    cycle_seconds: int
    capacity: int
    quality_multiplier: float
    overflow_cycles: int
    gene_cap: int
    ability: float
    feed_score: float
    quality_gene_coefficient: float
    affection_cap: int
    affection_quality_base: float
    affection_quality_per_point: float
    # 以下参数来自伙伴特性快照，没有驻场伙伴时全是中性值。
    quality_bonus: float = 0.0
    normal_yield_multiplier: float = 1.0
    feed_multiplier: float = 1.0
    special_chance_bonus: float = 0.0
    affection_quality_bonus: float = 0.0
    species: dict[str, SpeciesParameters] = field(default_factory=dict)


@dataclass
class FacilityAdvance:
    cycles: int = 0
    paid_cycles: int = 0
    consumed_units: float = 0.0
    produced: dict[str, dict[int, int]] = field(default_factory=dict)
    special: dict[str, int] = field(default_factory=dict)
    matured: list[str] = field(default_factory=list)
    hatched: list[str] = field(default_factory=list)
    stalled: bool = False
    saturated: bool = False


@dataclass
class LivestockSettlement:
    facilities: dict[str, FacilityAdvance] = field(default_factory=dict)
    consumed_units: float = 0.0
    stalled: bool = False


def affection_multiplier(animal: AnimalState, parameters: FacilityParameters) -> float:
    """亲密度的品质系数。特性只加每点的斜率，起点保持不变：养熟了才兑现。"""

    affection = max(0, min(parameters.affection_cap, animal.affection))
    per_point = parameters.affection_quality_per_point + parameters.affection_quality_bonus
    return parameters.affection_quality_base + per_point * affection


def quality_ability(animal: AnimalState, parameters: FacilityParameters) -> float:
    """畜产品的品质分。

    加算的四项（能力、饲料、基因、特性）先合并，再乘设施系数和亲密度系数 —— 亲密度是
    「放大你已经堆出来的底子」，不是一份固定加分，特性的加算同样吃这一层放大。
    """

    base = (
        parameters.ability
        + parameters.feed_score
        + parameters.quality_gene_coefficient * animal.quality_gene
        + parameters.quality_bonus
    )
    return max(0.0, base) * parameters.quality_multiplier * affection_multiplier(animal, parameters)


def yield_per_cycle(animal: AnimalState, species: SpeciesParameters) -> float:
    return species.base_yield * (1 + species.yield_coefficient * animal.yield_gene / 100)


def overflow_cap(animal: AnimalState, species: SpeciesParameters, parameters: FacilityParameters) -> int:
    """攒到大约这么多就停产。设施越好，回来看一眼的间隔越宽。"""

    return max(1, ceil(yield_per_cycle(animal, species) * parameters.normal_yield_multiplier * parameters.overflow_cycles))


def is_saturated(animal: AnimalState, species: SpeciesParameters, parameters: FacilityParameters) -> bool:
    if animal.stage != "adult":
        return False
    return animal.pending_total >= overflow_cap(animal, species, parameters)


def _species_of(animal: AnimalState, parameters: FacilityParameters) -> SpeciesParameters | None:
    return parameters.species.get(animal.species_id)


def _advance_animal(
    animal: AnimalState,
    species: SpeciesParameters,
    parameters: FacilityParameters,
    advance: FacilityAdvance,
    rng: Random | None,
) -> None:
    """把一只动物推进一个周期。rng 为 None 时是干跑，只算数量不掷品质。"""

    if animal.stage == "incubating":
        animal.stage_cycles += 1
        if animal.stage_cycles >= species.incubate_cycles:
            animal.stage = "juvenile"
            animal.stage_cycles = 0
            advance.hatched.append(animal.animal_id)
        return

    if animal.stage == "juvenile":
        animal.stage_cycles += 1
        if animal.stage_cycles >= species.growth_cycles:
            animal.stage = "adult"
            animal.stage_cycles = 0
            advance.matured.append(animal.animal_id)
        return

    if animal.breeding_cooldown > 0:
        animal.breeding_cooldown = max(0.0, animal.breeding_cooldown - 1)

    animal.yield_progress += yield_per_cycle(animal, species) * parameters.normal_yield_multiplier
    produced = int(animal.yield_progress)
    animal.yield_progress -= produced
    if produced <= 0:
        return

    if rng is None:
        # 干跑只需要知道总量，品质不参与饲料和溢出的计算。
        animal.pending_output[1] = animal.pending_output.get(1, 0) + produced
        return

    probabilities = quality_probabilities(
        quality_ability(animal, parameters),
        list(species.thresholds),
        species.width,
        species.miracle_probability_cap,
        species.miracle_eligible,
    )
    tally = advance.produced.setdefault(species.species_id, {})
    for _ in range(produced):
        quality = roll_quality(rng, probabilities)
        animal.pending_output[quality] = animal.pending_output.get(quality, 0) + 1
        tally[quality] = tally.get(quality, 0) + 1

    special_chance = species.special_chance + parameters.special_chance_bonus
    if (
        species.special_item_id
        and special_chance > 0
        and animal.affection >= parameters.affection_cap
        and rng.random() < special_chance
    ):
        # 特殊产出不占溢出上限：它是满亲密度的惊喜，不该反过来把正常产出卡住。
        animal.pending_special += 1
        advance.special[species.special_item_id] = advance.special.get(species.special_item_id, 0) + 1


def advance_facility(
    facility: LivestockFacilityState,
    animals: list[AnimalState],
    parameters: FacilityParameters,
    cycles: int,
    budget_units: float,
    rng: Random | None = None,
) -> FacilityAdvance:
    """按整周期推进一个畜栏，最多花掉 budget_units 份饲料。

    每个周期先算这一栏这一轮要吃多少：孵化中的蛋不吃（投蛋时已经付过），已经溢出的
    成年动物也不吃 —— 停产的周期还烧料是纯负面体验。整栏都没事可做时直接停下。
    """

    advance = FacilityAdvance()
    if parameters.cycle_seconds <= 0:
        return advance
    budget = max(0.0, float(budget_units))
    for _ in range(min(max(0, int(cycles)), MAX_SETTLE_CYCLES)):
        working: list[tuple[AnimalState, SpeciesParameters, bool]] = []
        demand = 0.0
        for animal in animals:
            species = _species_of(animal, parameters)
            if species is None:
                continue
            saturated = is_saturated(animal, species, parameters)
            if saturated and animal.breeding_cooldown <= 0:
                continue
            working.append((animal, species, saturated))
            if not saturated and animal.stage != "incubating":
                demand += species.feed_per_cycle * parameters.feed_multiplier
        if not working:
            advance.saturated = True
            break
        if demand > budget + 1e-9:
            advance.stalled = True
            break
        budget -= demand
        advance.consumed_units += demand
        advance.cycles += 1
        advance.paid_cycles += 1
        for animal, species, saturated in working:
            if saturated:
                # 攒满了就停产，但配种冷却照走 —— 不然忘了收产出会把冷却一起冻住。
                animal.breeding_cooldown = max(0.0, animal.breeding_cooldown - 1)
                continue
            _advance_animal(animal, species, parameters, advance, rng)
    return advance


def _pending_seconds(facility: LivestockFacilityState, cycle: int, seconds: int) -> int:
    if facility.stalled:
        # 缺料意味着已经到过结算边界；兼容旧存档留下的部分余量，只保留一个待付周期。
        return max(0, cycle)
    return facility.settle_remainder + max(0, int(seconds))


def plan_facility(
    facility: LivestockFacilityState,
    animals: list[AnimalState],
    parameters: FacilityParameters,
    seconds: int,
) -> tuple[int, float]:
    """干跑一遍，返回 (想跑的周期数, 需要的饲料份数)。

    干跑不掷品质，也不碰真实状态：它只是为了在多个畜栏和鱼塘共用一个槽时，先知道
    各自的需求量，再按比例分配。
    """

    cycle = parameters.cycle_seconds
    if cycle <= 0:
        return 0, 0.0
    elapsed = _pending_seconds(facility, cycle, seconds)
    cycles = elapsed // cycle
    if cycles <= 0:
        return 0, 0.0
    rehearsal = [animal.model_copy(deep=True) for animal in animals]
    advance = advance_facility(facility, rehearsal, parameters, cycles, float("inf"), rng=None)
    return advance.cycles, advance.consumed_units


def settle_livestock(
    facilities: list[LivestockFacilityState],
    animals: list[AnimalState],
    feed_slot: SlotState,
    parameters: dict[str, FacilityParameters],
    now: int,
    rng: Random | None = None,
    ratio: float | None = None,
) -> LivestockSettlement:
    """把所有畜栏一起推进到 now。

    ratio 由调用方给出，因为饲料槽是鱼塘和畜牧共用的：谁先结算谁就把槽喝光，这不公平，
    所以两边先各自报一遍需求，再按同一个比例拿预算。
    """

    settlement = LivestockSettlement()
    by_facility: dict[str, list[AnimalState]] = {}
    for animal in animals:
        by_facility.setdefault(animal.facility_id, []).append(animal)

    plans: dict[str, tuple[int, float]] = {}
    demand = 0.0
    for facility in facilities:
        entry = parameters.get(facility.facility_id)
        if entry is None:
            continue
        seconds = max(0, now - facility.last_settled_at)
        cycles, units = plan_facility(facility, by_facility.get(facility.facility_id, []), entry, seconds)
        plans[facility.facility_id] = (cycles, units)
        demand += units

    if ratio is None:
        ratio = 1.0 if demand <= 0 else min(1.0, feed_slot.units / demand)

    consumed = 0.0
    for facility in facilities:
        entry = parameters.get(facility.facility_id)
        if entry is None:
            facility.last_settled_at = now
            continue
        cycle = entry.cycle_seconds
        elapsed = _pending_seconds(facility, cycle, now - facility.last_settled_at)
        wanted_cycles = elapsed // cycle if cycle > 0 else 0
        _, units = plans.get(facility.facility_id, (0, 0.0))
        budget = units * max(0.0, min(1.0, ratio))
        advance = advance_facility(
            facility,
            by_facility.get(facility.facility_id, []),
            entry,
            wanted_cycles,
            budget,
            rng=rng,
        )
        consumed += advance.consumed_units
        if advance.stalled:
            # 时间已经走完，只等付料；停摆期间不积攒后续周期。
            facility.settle_remainder = cycle
        elif cycle > 0:
            facility.settle_remainder = elapsed % cycle
        facility.stalled = advance.stalled
        facility.last_settled_at = now
        settlement.facilities[facility.facility_id] = advance
        settlement.stalled = settlement.stalled or advance.stalled

    feed_slot.units = max(0.0, feed_slot.units - consumed)
    settlement.consumed_units = consumed
    feed_slot.updated_at = now
    if not feed_slot.units:
        feed_slot.quality_score = 0.0
    return settlement


def roll_gene(
    rng: Random,
    base: float,
    sigma: float,
    gene_cap: int,
    mutation_chance: float,
    mutation_bonus: int,
    rerolls: int = 0,
) -> int:
    """一条基因的遗传。

    正常范围封在品种上限里，突变可以顶破它 —— 突变是玩家追了几代之后的惊喜，
    被上限吃掉就没意义了。硬顶永远是 100。

    rerolls 来自伙伴特性：多掷几次取最高。它在均值附近有明显正偏移，越靠近品种上限
    收益越自然收敛，所以不需要另设封顶。
    """

    value = max(rng.gauss(base, sigma) for _ in range(1 + max(0, int(rerolls))))
    value = max(0.0, min(float(gene_cap), value))
    if mutation_chance > 0 and rng.random() < mutation_chance:
        value = min(100.0, value + mutation_bonus)
    return int(round(value))


def refund_value(base: int, quality_gene: int, yield_gene: int, coefficient: float) -> int:
    """出售动物的回收价。永远低于买入价，但好基因卖得贵，淘汰差的才有正反馈。"""

    ratio = 1 + coefficient * (quality_gene + yield_gene) / 200
    return int(base * ratio)
