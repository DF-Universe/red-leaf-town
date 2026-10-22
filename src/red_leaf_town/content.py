from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field, model_validator


CONTENT_TAGS = frozenset({
    "animal_product",
    "crop",
    "crop_seed",
    "exploration_equipment",
    "fish",
    "fodder",
    "food",
    "forage",
    "hide",
    "medicine",
    "mineral",
    "poultry_product",
    "tree_fruit",
    "usable",
    "wood",
    "wood_product",
    "wool",
})


def _validate_content_tags(tags: list[str], label: str) -> list[str]:
    if len(tags) != len(set(tags)):
        raise ValueError(f"{label} tags must be unique")
    unknown = set(tags) - CONTENT_TAGS
    if unknown:
        raise ValueError(f"{label} uses unknown tags: {', '.join(sorted(unknown))}")
    return tags


class GameMeta(BaseModel):
    title: str
    starting_coins: int = Field(ge=0)
    starting_maple_flame: int = Field(default=0, ge=0)
    starting_guide_leaves: int = Field(default=0, ge=0)
    initial_inventory: dict[str, int] = Field(default_factory=dict)


class WeatherDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    accent: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class WorldDefinition(BaseModel):
    utc_offset_seconds: int = Field(default=8 * 3600, ge=-12 * 3600, le=14 * 3600)
    season_id: str = Field(default="autumn", min_length=1)
    season_name: str = Field(default="秋季", min_length=1)
    weather_cycle: list[WeatherDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_weather_cycle(self):
        weather_ids = [entry.id for entry in self.weather_cycle]
        if len(weather_ids) != len(set(weather_ids)):
            raise ValueError("weather ids must be unique")
        return self


class StaminaDefinition(BaseModel):
    restore_seconds: int = Field(gt=0)
    # 体力药和枫火买体力都能把体力顶到上限之上，溢出期间自然回复暂停。
    potion_item_id: str = Field(default="feien_tonic", min_length=1)
    potion_restore: int = Field(default=40, gt=0)
    purchase_restore: int = Field(default=40, gt=0)
    # 一天之内第 n 次购买的枫火价，列表长度就是每日次数上限。
    purchase_prices: list[int] = Field(default_factory=lambda: [50, 100, 150, 200], min_length=1)

    @model_validator(mode="after")
    def validate_prices(self):
        if any(price <= 0 for price in self.purchase_prices):
            raise ValueError("stamina purchase prices must be positive")
        if self.purchase_prices != sorted(self.purchase_prices):
            raise ValueError("stamina purchase prices must not get cheaper as the day goes on")
        return self

    @property
    def purchase_daily_limit(self) -> int:
        return len(self.purchase_prices)

    def purchase_price(self, used_today: int) -> int | None:
        """今天已经买过 used_today 次时，下一次的价钱；买满了就是 None。"""
        if used_today < 0 or used_today >= len(self.purchase_prices):
            return None
        return self.purchase_prices[used_today]


class MonthlyCardDefinition(BaseModel):
    """月卡。只靠激活码发放，商店里买不到。"""

    duration_days: int = Field(default=30, gt=0, le=365)
    max_days: int = Field(default=180, gt=0, le=3650)
    activation_maple_flame: int = Field(default=300, ge=0)
    daily_maple_flame: int = Field(default=50, ge=0)
    daily_item_id: str = Field(default="feien_tonic", min_length=1)
    daily_item_amount: int = Field(default=2, ge=0)

    @model_validator(mode="after")
    def validate_duration(self):
        if self.duration_days > self.max_days:
            raise ValueError("monthly card duration cannot exceed the maximum stacked days")
        return self


class IndustryRulesDefinition(BaseModel):
    character_base_ability: int = Field(default=0, ge=0)
    partner_capacity: int = Field(default=1, ge=0)
    partner_level_cap: int = Field(default=20, ge=1, le=60)
    collaborator_slots: int = Field(default=1, ge=1, le=2)


class LevelDefinition(BaseModel):
    beta: bool = False
    level: int = Field(ge=1)
    total_xp: int = Field(ge=0)
    plot_slots: int = Field(ge=1)
    stamina_cap: int = Field(ge=1)
    unlocks: list[str] = Field(default_factory=list)


class SlotInputDefinition(BaseModel):
    """物品作为槽投入物的两个维度：units 是量，score 是单位品质分，都与售价解耦。"""

    units: int = Field(gt=0)
    score: float = Field(ge=0)


# 探秘副本的骰子写法，例如 1d10、2d6。骰面只开这几种，避免内容里写出没人认的骰子。
DELVE_DICE_PATTERN = r"^[1-9]\d?d(4|6|8|10|12)$"


class EquipmentDefinition(BaseModel):
    """探秘副本的装备数值。装备不进五档品质，一件装备就是一组固定数值。"""

    slot: Literal["weapon", "accessory"]
    # 武器吃哪一维：大剑吃力量、短剑吃敏捷、法书吃智力。饰品不吃属性。
    attribute: Literal["strength", "agility", "intelligence"] | None = None
    damage_dice: str = ""
    attack_bonus: int = Field(default=0, ge=-5, le=10)
    proficiency_bonus: int = Field(default=0, ge=0, le=5)
    armor_bonus: int = Field(default=0, ge=0, le=8)
    initiative_bonus: int = Field(default=0, ge=0, le=8)
    max_hp_bonus: int = Field(default=0, ge=0, le=60)
    # 每场战斗可以主动取得优势骰的次数。
    advantage_uses: int = Field(default=0, ge=0, le=3)
    description: str = ""

    @model_validator(mode="after")
    def validate_equipment(self):
        if self.slot == "weapon":
            if self.attribute is None:
                raise ValueError("weapon equipment must name the attribute it scales with")
            if not re.match(DELVE_DICE_PATTERN, self.damage_dice):
                raise ValueError("weapon equipment must define a valid damage dice, e.g. 1d10")
        else:
            if self.attribute is not None:
                raise ValueError("accessory equipment cannot scale with an attribute")
            if self.damage_dice:
                raise ValueError("accessory equipment cannot define damage dice")
        return self


class DelveUseDefinition(BaseModel):
    """物品在探秘副本战斗中的用法。没写这个块的物品带不进副本。"""

    effect: Literal["heal"]
    dice: str = ""
    flat: int = Field(default=0, ge=0, le=200)
    # 每高一档品质额外多回这么多，给高品质产物一个去处。
    quality_bonus: int = Field(default=0, ge=0, le=50)

    @model_validator(mode="after")
    def validate_use(self):
        if self.dice and not re.match(DELVE_DICE_PATTERN, self.dice):
            raise ValueError("delve item dice must look like 2d6")
        if not self.dice and not self.flat:
            raise ValueError("delve item must restore something")
        return self


class ItemDefinition(BaseModel):
    beta: bool = False
    id: str
    name: str
    icon: str
    kind: Literal["seed", "produce", "material", "product", "consumable", "equipment"]
    sell_price: int = Field(ge=0)
    has_quality: bool = False
    feed: SlotInputDefinition | None = None
    equipment: EquipmentDefinition | None = None
    delve_use: DelveUseDefinition | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tags(self):
        self.tags = _validate_content_tags(self.tags, f"item {self.id}")
        return self

    @model_validator(mode="after")
    def validate_equipment_block(self):
        if (self.kind == "equipment") != (self.equipment is not None):
            raise ValueError(f"item {self.id} must carry an equipment block if and only if it is equipment")
        if self.kind == "equipment":
            if self.has_quality:
                raise ValueError(f"equipment {self.id} cannot use the five-tier quality system")
            if self.delve_use is not None:
                raise ValueError(f"equipment {self.id} cannot also be a usable delve item")
        return self

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags


class QualityGradeDefinition(BaseModel):
    level: int = Field(ge=1, le=5)
    name: str = Field(min_length=1)
    sale_multiplier: float = Field(gt=0)


class QualitySystemDefinition(BaseModel):
    grades: list[QualityGradeDefinition] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_grades(self):
        if [grade.level for grade in self.grades] != [1, 2, 3, 4, 5]:
            raise ValueError("quality grades must define levels 1 through 5 in order")
        return self

    @property
    def grade_map(self) -> dict[int, QualityGradeDefinition]:
        return {grade.level: grade for grade in self.grades}


class QualityCurveDefinition(BaseModel):
    thresholds: list[float] = Field(min_length=4, max_length=4)
    width: float = Field(gt=0)
    miracle_probability_cap: float = Field(default=0, ge=0, le=0.01)
    miracle_eligible: bool = False

    @model_validator(mode="after")
    def validate_thresholds(self):
        if self.thresholds != sorted(self.thresholds) or len(set(self.thresholds)) != 4:
            raise ValueError("quality thresholds must be strictly increasing")
        return self


class CropDefinition(BaseModel):
    beta: bool = False
    id: str
    name: str
    icon: str
    seed_item_id: str
    produce_item_id: str
    growth_seconds: int = Field(gt=0)
    minimum_duration_seconds: int = Field(default=1, gt=0)
    time_difficulty: int = Field(gt=0)
    yield_min: int = Field(ge=1)
    yield_max: int = Field(ge=1)
    stamina_cost: int = Field(ge=0)
    plant_xp: int = Field(ge=0)
    harvest_xp: int = Field(ge=0)
    min_level: int = Field(ge=1)
    accent: str
    quality: QualityCurveDefinition

    @model_validator(mode="after")
    def validate_yield(self):
        if self.yield_max < self.yield_min:
            raise ValueError(f"crop {self.id}: yield_max must be >= yield_min")
        if self.minimum_duration_seconds > self.growth_seconds:
            raise ValueError(f"crop {self.id}: minimum_duration_seconds must be <= growth_seconds")
        return self


class GatheringSiteDefinition(BaseModel):
    season_id: str = ""
    beta: bool = False
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    accent: str
    min_level: int = Field(default=1, ge=1)


class GatheringOutputDefinition(BaseModel):
    """探索产出池的一项。weight 是抽取权重，抽中后产出 quantity_min—quantity_max 件。"""

    item_id: str = Field(min_length=1)
    weight: float = Field(gt=0)
    quantity_min: int = Field(ge=1)
    quantity_max: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_quantity(self):
        if self.quantity_max < self.quantity_min:
            raise ValueError("gathering output quantity_max must be >= quantity_min")
        return self


class GatheringDrawDefinition(BaseModel):
    """抽取次数曲线：base_draws × (1 + ability_bonus × A/(A + difficulty))。"""

    base_draws: int = Field(ge=1, le=30)
    ability_bonus: float = Field(default=0, ge=0, le=5)
    difficulty: int = Field(gt=0)


class GatheringTaskDefinition(BaseModel):
    season_id: str = ""
    beta: bool = False
    id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    minimum_duration_seconds: int = Field(gt=0)
    time_difficulty: int = Field(gt=0)
    outputs: list[GatheringOutputDefinition] = Field(min_length=2, max_length=5)
    draws: GatheringDrawDefinition
    stamina_cost: int = Field(ge=0)
    collect_xp: int = Field(ge=0)
    min_level: int = Field(ge=1)
    quality: QualityCurveDefinition

    @model_validator(mode="after")
    def validate_outputs(self):
        if self.minimum_duration_seconds > self.duration_seconds:
            raise ValueError(f"gathering task {self.id}: minimum duration cannot exceed base duration")
        item_ids = [entry.item_id for entry in self.outputs]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError(f"gathering task {self.id}: output items must be unique")
        return self

    @property
    def headline_output(self) -> GatheringOutputDefinition:
        """权重最高的一项，用于列表和任务快照里的代表产物。"""

        return max(self.outputs, key=lambda entry: entry.weight)


class ExplorationRewardDefinition(BaseModel):
    item_id: str = Field(min_length=1)
    quantity_min: int = Field(ge=1)
    quantity_max: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_quantity(self):
        if self.quantity_max < self.quantity_min:
            raise ValueError("exploration reward quantity_max must be >= quantity_min")
        return self


ExplorationCheckAttribute = Literal["strength", "agility", "intelligence", "luck"]
ExplorationCheckMode = Literal["best", "sum"]
ExplorationDiceMode = Literal["normal", "advantage", "disadvantage"]


class ExplorationCheckDefinition(BaseModel):
    attribute: ExplorationCheckAttribute
    mode: ExplorationCheckMode = "best"
    dice: ExplorationDiceMode = "normal"
    dc: int = Field(ge=2, le=40)

    @staticmethod
    def success_probability(modifier: int, dc: int, dice: ExplorationDiceMode = "normal") -> float:
        successful_faces = sum(
            1
            for face in range(1, 21)
            if face == 20 or (face != 1 and face + modifier >= dc)
        )
        normal = successful_faces / 20
        if dice == "advantage":
            return 1 - (1 - normal) ** 2
        if dice == "disadvantage":
            return normal**2
        return normal


class RewardItemDefinition(BaseModel):
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    quality: int = Field(default=0, ge=0, le=5)


class DelveAttackDefinition(BaseModel):
    name: str = Field(min_length=1)
    to_hit: int = Field(ge=-5, le=20)
    damage_dice: str = Field(pattern=DELVE_DICE_PATTERN)
    damage_bonus: int = Field(default=0, ge=0, le=20)


class DelveEnemyDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    icon: str = ""
    description: str = ""
    max_hp: int = Field(gt=0, le=500)
    armor_class: int = Field(ge=5, le=25)
    initiative_bonus: int = Field(default=0, ge=-5, le=10)
    attacks: list[DelveAttackDefinition] = Field(min_length=1, max_length=4)
    # 一回合出手几次。敌人在先攻序列里只占一格，而我方三人各占一格，所以单动的首领在
    # 行动经济上是 1:3，怎么调数值都构不成威胁；首领靠这个字段把出手次数补回来。
    attacks_per_turn: int = Field(default=1, ge=1, le=4)
    boss: bool = False


class DelveBattleDefinition(BaseModel):
    """一个探秘节点上的遭遇战。奖励仍然写在选择的 success 里，胜利后才结算。"""

    enemy_ids: list[str] = Field(min_length=1, max_length=4)
    can_flee: bool = True
    flee_dc: int = Field(default=12, ge=2, le=30)


class ExplorationOutcomeDefinition(BaseModel):
    text: str = Field(min_length=1)
    rewards: list[ExplorationRewardDefinition] = Field(default_factory=list, max_length=4)
    # 不进品质系统的固定发放（装备），和上面的品质产物分开走。
    fixed_rewards: list[RewardItemDefinition] = Field(default_factory=list, max_length=4)
    stamina_surcharge: int = Field(default=0, ge=0, le=10)
    next_route_discount: int = Field(default=0, ge=0, le=5)
    quality_ability_bonus: int = Field(default=0, ge=-200, le=200)


class ExplorationChoiceDefinition(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    route_stamina: int = Field(ge=0, le=20)
    action_stamina: int = Field(ge=0, le=20)
    check: ExplorationCheckDefinition | None = None
    battle: DelveBattleDefinition | None = None
    success: ExplorationOutcomeDefinition
    failure: ExplorationOutcomeDefinition | None = None
    critical_success: ExplorationOutcomeDefinition | None = None
    critical_failure: ExplorationOutcomeDefinition | None = None

    @model_validator(mode="after")
    def validate_choice(self):
        if self.route_stamina + self.action_stamina <= 0:
            raise ValueError("exploration choices must cost stamina")
        if self.check is not None and self.failure is None:
            raise ValueError("checked exploration choices require a failure outcome")
        if self.check is None and (self.failure or self.critical_success or self.critical_failure):
            raise ValueError("automatic exploration choices cannot define checked outcomes")
        if self.battle is not None and self.check is not None:
            raise ValueError(f"exploration choice {self.id} cannot roll a check and start a battle")
        return self


class ExplorationEventDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    min_depth: int = Field(default=1, ge=1)
    max_depth: int = Field(default=99, ge=1)
    weight: float = Field(default=1, gt=0)
    max_occurrences: int = Field(default=99, ge=1)
    choices: list[ExplorationChoiceDefinition] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_event(self):
        if self.max_depth < self.min_depth:
            raise ValueError(f"exploration event {self.id} has an inverted depth range")
        choice_ids = [choice.id for choice in self.choices]
        if len(choice_ids) != len(set(choice_ids)):
            raise ValueError(f"exploration event {self.id} has duplicate choice ids")
        return self

    @property
    def choice_map(self) -> dict[str, ExplorationChoiceDefinition]:
        return {choice.id: choice for choice in self.choices}


class ExplorationExpeditionDefinition(BaseModel):
    season_id: str = ""
    id: str = Field(min_length=1)
    kind: Literal["transport", "survey", "delve"]
    # 内测路线只对白名单玩家可见可进，验收之后摘掉这个标记就是正式内容。
    beta: bool = False
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    accent: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    min_level: int = Field(ge=1)
    one_time: bool = False
    first_clear_guide_leaves: int = Field(default=0, ge=0)
    entry_fee: int = Field(ge=0)
    max_depth: int = Field(ge=1, le=20)
    final_event_id: str = Field(min_length=1)
    quality: QualityCurveDefinition
    events: list[ExplorationEventDefinition] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def validate_expedition(self):
        event_ids = [event.id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError(f"exploration expedition {self.id} has duplicate event ids")
        if self.final_event_id not in set(event_ids):
            raise ValueError(f"exploration expedition {self.id} references an unknown final event")
        final = self.event_map[self.final_event_id]
        if final.min_depth > self.max_depth or final.max_depth < self.max_depth:
            raise ValueError(f"exploration expedition {self.id} final event cannot appear at max depth")
        return self

    @property
    def event_map(self) -> dict[str, ExplorationEventDefinition]:
        return {event.id: event for event in self.events}


# 特殊效果节点认得的修正键。没登记的键会在内容加载时被拦下，避免写错字静默失效。
TALENT_MODIFIER_KEYS: dict[str, str] = {
    "crafting_duration_reduction": "加工减时",
    "crafting_stamina_refund_chance": "加工返还体力概率",
    "crafting_quality_bonus": "加工品质能力",
    "exploration_check_bonus": "探索事件检定加成",
    "delve_max_hp_bonus": "探秘生命加成",
    "delve_first_miss_reroll": "探秘首次未命中重掷",
    "fishing_combo_cap": "聚鱼度层数上限",
    "pond_generation_cap": "鱼塘世代加值上限",
    "pond_harvest_quality_floor": "捞鱼保底品质",
    "livestock_overflow_cycles": "畜牧产出溢出上限",
    "livestock_mutation_chance": "孵化与配种突变概率",
}


class TalentNodeDefinition(BaseModel):
    beta: bool = False
    id: str = Field(min_length=1)
    industry: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    cost: int = Field(default=1, ge=1)
    min_level: int = Field(default=1, ge=1)
    prerequisites: list[str] = Field(default_factory=list)
    partner_capacity_bonus: int = Field(default=0, ge=0)
    global_ability_bonus: int = Field(default=0, ge=0)
    modifiers: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_modifiers(self):
        unknown = sorted(set(self.modifiers) - set(TALENT_MODIFIER_KEYS))
        if unknown:
            raise ValueError(f"talent {self.id} declares unknown modifiers: {', '.join(unknown)}")
        return self


class RecipeUnlockCondition(BaseModel):
    hook: str = Field(min_length=1)
    params: dict[str, int | str | bool]


class RecipeInputDefinition(BaseModel):
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)


class CraftingStationDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    accent: str
    min_level: int = Field(default=1, ge=1)


class RecipeDefinition(BaseModel):
    beta: bool = False
    id: str = Field(min_length=1)
    station_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    inputs: list[RecipeInputDefinition] = Field(min_length=1)
    produce_item_id: str = Field(min_length=1)
    produce_quantity: int = Field(default=1, ge=1)
    duration_seconds: int = Field(gt=0)
    time_difficulty: int = Field(gt=0)
    stamina_cost: int = Field(ge=0)
    collect_xp: int = Field(ge=0)
    quality: QualityCurveDefinition
    unlock_condition: RecipeUnlockCondition
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tags(self):
        self.tags = _validate_content_tags(self.tags, f"recipe {self.id}")
        return self

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags


class MiningSiteDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    accent: str
    min_level: int = Field(default=1, ge=1)


class MiningTaskDefinition(BaseModel):
    id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    produce_item_id: str = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    yield_bonus: float = Field(default=1, ge=0, le=5)
    yield_difficulty: int = Field(gt=0)
    yield_min: int = Field(ge=1)
    yield_max: int = Field(ge=1)
    stamina_cost: int = Field(ge=0)
    collect_xp: int = Field(ge=0)
    min_level: int = Field(ge=1)
    quality: QualityCurveDefinition

    @model_validator(mode="after")
    def validate_yield(self):
        if self.yield_max < self.yield_min:
            raise ValueError(f"mining task {self.id}: yield_max must be >= yield_min")
        return self


class FishingOutputDefinition(BaseModel):
    """钓点产出池的一项。rare 的条目会吃聚鱼度加权，codex 的条目会录入鱼类图鉴。

    带 size 区间的条目每次上钩都会摇一个体型并记进图鉴的最大值 —— 鱼有体型，水草和鱼苗没有。
    """

    item_id: str = Field(min_length=1)
    weight: float = Field(gt=0)
    quantity_min: int = Field(default=1, ge=1)
    quantity_max: int = Field(default=1, ge=1)
    rare: bool = False
    codex: bool = False
    size_min: float = Field(default=0, ge=0)
    size_max: float = Field(default=0, ge=0)

    @property
    def has_size(self) -> bool:
        return self.size_max > 0

    @model_validator(mode="after")
    def validate_quantity(self):
        if self.quantity_max < self.quantity_min:
            raise ValueError("fishing output quantity_max must be >= quantity_min")
        if self.size_max < self.size_min:
            raise ValueError("fishing output size_max must be >= size_min")
        if self.has_size and self.size_min <= 0:
            raise ValueError("fishing output size_min must be positive when a size range is given")
        if self.codex and not self.has_size:
            # 图鉴里的都是鱼，鱼一定有体型；杂物不进图鉴也就不需要体型。
            raise ValueError("fishing output in the codex must carry a size range")
        return self


class BigCatchDefinition(BaseModel):
    """大物条目。抽中之后不直接给鱼，而是进入一次搏鱼：追加体力搏一把，或者放弃拿回一条普通鱼。"""

    item_id: str = Field(min_length=1)
    fallback_item_id: str = Field(min_length=1)
    weight: float = Field(gt=0)
    stamina_cost: int = Field(default=3, ge=0)
    base_chance: float = Field(ge=0, le=1)
    ability_bonus: float = Field(default=0, ge=0, le=1)
    difficulty: int = Field(gt=0)
    min_quality: int = Field(default=3, ge=1, le=5)
    size_min: float = Field(gt=0)
    size_max: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_big_catch(self):
        if self.size_max < self.size_min:
            raise ValueError(f"big catch {self.item_id}: size_max must be >= size_min")
        if self.base_chance + self.ability_bonus > 1:
            raise ValueError(f"big catch {self.item_id}: success chance can exceed one")
        return self

    def success_chance(self, ability: int | float) -> float:
        ability = max(0.0, float(ability))
        return self.base_chance + self.ability_bonus * ability / (ability + self.difficulty)


class FishingSpotDefinition(BaseModel):
    season_id: str = ""
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    accent: str
    min_level: int = Field(ge=1)
    stamina_cost: int = Field(gt=0)
    cast_xp: int = Field(ge=0)
    time_difficulty: int = Field(gt=0)
    draws: GatheringDrawDefinition
    outputs: list[FishingOutputDefinition] = Field(min_length=2, max_length=8)
    big_catch: BigCatchDefinition | None = None
    quality: QualityCurveDefinition

    @model_validator(mode="after")
    def validate_outputs(self):
        item_ids = [entry.item_id for entry in self.outputs]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError(f"fishing spot {self.id}: output items must be unique")
        return self


class FishingComboDefinition(BaseModel):
    """聚鱼度：同一钓点连续抛竿累层，换钓点清零，停手一段时间后逐层衰减。"""

    max_layers: int = Field(default=10, ge=1, le=50)
    draw_bonus_per_layer: float = Field(default=0.1, ge=0, le=1)
    rare_weight_per_layer: float = Field(default=0.05, ge=0, le=1)
    idle_grace_seconds: int = Field(default=1800, gt=0)
    decay_seconds: int = Field(default=600, gt=0)


class PondTierDefinition(BaseModel):
    """塘等级。本批只用 Lv1，但扩建要改的只是数据，模型不动。"""

    level: int = Field(ge=1)
    capacity: int = Field(gt=0)
    quality_bonus: float = Field(default=0)
    feed_per_cycle: float = Field(ge=0)


class PondSlotDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    accent: str
    min_level: int = Field(ge=1)
    tier: int = Field(default=1, ge=1)
    build_cost: int = Field(default=0, ge=0)
    species_id: str = ""
    build_materials: list[RecipeInputDefinition] = Field(default_factory=list)


class PondSpeciesDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    icon: str = Field(min_length=1)
    fry_item_id: str = Field(min_length=1)
    produce_item_id: str = Field(min_length=1)
    base_cycle_seconds: int = Field(gt=0)
    time_difficulty: int = Field(gt=0)
    growth_rate: float = Field(gt=0, le=1)
    maturation_cycles: float = Field(gt=0)
    steady_ratio: float = Field(gt=0, le=1)
    generation_gain: float = Field(ge=0)
    generation_decay: float = Field(ge=0)
    generation_cap: float = Field(ge=0)
    min_level: int = Field(ge=1)
    quality: QualityCurveDefinition


class FeedSlotDefinition(BaseModel):
    name: str = Field(min_length=1)
    capacity: int = Field(gt=0)
    quality_multipliers: list[float] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_multipliers(self):
        if any(entry <= 0 for entry in self.quality_multipliers):
            raise ValueError("feed slot quality multipliers must be positive")
        return self

    def multiplier(self, quality: int) -> float:
        """品质影响 score，不影响 units。无品质物品按普通档计。"""

        index = min(5, max(1, int(quality) or 1)) - 1
        return self.quality_multipliers[index]


class LivestockTierDefinition(BaseModel):
    beta: bool = False
    """设施的一级。本批每种设施只有 Lv1，扩建时只补数据，模型不动。"""

    level: int = Field(ge=1)
    min_level: int = Field(default=1, ge=1)
    capacity: int = Field(gt=0)
    quality_multiplier: float = Field(default=1, gt=0)
    overflow_cycles: int = Field(gt=0)
    gene_cap: int = Field(gt=0, le=100)
    feed_slot_capacity_bonus: int = Field(default=0, ge=0)
    build_coins: int = Field(default=0, ge=0)
    build_materials: list[RecipeInputDefinition] = Field(default_factory=list, max_length=6)


class LivestockFacilityDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    accent: str
    category: Literal["poultry", "mammal"]
    min_level: int = Field(ge=1)
    # 散养地免费自动拥有，鸡舍畜栏要建。
    granted: bool = False
    # 建成本设施时回收的过渡设施，栏中的动物在同一次原子更新里迁入。
    replaces: str = ""
    tiers: list[LivestockTierDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_tiers(self):
        levels = [tier.level for tier in self.tiers]
        if levels != sorted(levels) or len(set(levels)) != len(levels) or levels[0] != 1:
            raise ValueError(f"livestock facility {self.id} tiers must start at 1 and be unique")
        if self.granted and self.tiers[0].build_coins:
            raise ValueError(f"livestock facility {self.id} is granted and cannot cost coins")
        return self

    def tier(self, level: int) -> LivestockTierDefinition:
        for entry in self.tiers:
            if entry.level == level:
                return entry
        return self.tiers[0]


class LivestockBreedingDefinition(BaseModel):
    """两条繁殖线：孵蛋走物品品质，配种走双亲均值。"""

    mode: Literal["incubate", "pair"]
    feed_units: float = Field(ge=0)
    min_feed_score: float = Field(default=0, ge=0)
    gene_sigma: float = Field(gt=0)
    mutation_chance: float = Field(ge=0, le=1)
    mutation_bonus: int = Field(ge=0, le=100)
    # incubate 专用
    incubate_item_id: str = ""
    incubate_cycles: int = Field(default=0, ge=0)
    quality_gene_base: list[float] = Field(default_factory=list)
    # pair 专用
    cooldown_cycles: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "incubate":
            if not self.incubate_item_id:
                raise ValueError("incubate breeding requires an item to incubate")
            if self.incubate_cycles <= 0:
                raise ValueError("incubate breeding requires a positive incubation length")
            if len(self.quality_gene_base) != 5:
                raise ValueError("incubate breeding needs one gene base per quality grade")
            if any(entry < 0 or entry > 100 for entry in self.quality_gene_base):
                raise ValueError("incubate gene bases must be between 0 and 100")
        elif self.cooldown_cycles <= 0:
            raise ValueError("pair breeding requires a positive cooldown")
        return self


class LivestockSpeciesDefinition(BaseModel):
    beta: bool = False
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    icon: str = Field(min_length=1)
    category: Literal["poultry", "mammal"]
    min_level: int = Field(ge=1)
    required_facility_id: str = ""
    required_facility_tier: int = Field(default=1, ge=1)
    purchase_price: int = Field(ge=0)
    refund_base: int = Field(ge=0)
    growth_cycles: int = Field(gt=0)
    produce_item_id: str = Field(min_length=1)
    base_yield: float = Field(gt=0)
    feed_per_cycle: float = Field(ge=0)
    special_item_id: str = ""
    special_chance: float = Field(default=0, ge=0, le=1)
    breeding: LivestockBreedingDefinition
    quality: QualityCurveDefinition


class LivestockRulesDefinition(BaseModel):
    """畜牧的全局常量。周期是硬常量，任何能力都压不动它。"""

    cycle_seconds: int = Field(gt=0)
    quality_gene_coefficient: float = Field(ge=0)
    yield_gene_coefficient: float = Field(ge=0)
    purchase_gene_min: int = Field(ge=0, le=100)
    purchase_gene_max: int = Field(ge=0, le=100)
    affection_cap: int = Field(gt=0, le=100)
    affection_per_care: int = Field(gt=0)
    affection_quality_base: float = Field(gt=0)
    affection_quality_per_point: float = Field(ge=0)
    care_stamina_cost: int = Field(ge=0)
    care_experience: int = Field(ge=0)
    care_daily_limit: int = Field(gt=0)
    refund_gene_coefficient: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_genes(self):
        if self.purchase_gene_min > self.purchase_gene_max:
            raise ValueError("purchase gene range is inverted")
        return self

    def affection_multiplier(self, affection: int) -> float:
        capped = max(0, min(self.affection_cap, int(affection)))
        return self.affection_quality_base + self.affection_quality_per_point * capped


class TaskItemDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    icon: str = Field(min_length=1)
    effect: Literal[
        "quality_boost",
        "duration_multiplier",
        "yield_bonus",
        "release_partner",
        "instant_finish",
        "unlock_miracle",
    ]
    value: float = Field(default=1, gt=0)
    timing: Literal["start", "active"] = "start"
    eligible_industries: list[str] = Field(default_factory=list)


class GachaEconomyDefinition(BaseModel):
    """所有招募池共用的经济参数：兑换汇率、重复伙伴印记、升星消耗。跟具体池子无关。"""

    maple_flame_per_leaf: int = Field(gt=0)
    duplicate_marks: dict[int, int]
    star_up_costs: dict[int, int]

    @model_validator(mode="after")
    def validate_economy(self):
        if set(self.duplicate_marks) != {3, 4, 5}:
            raise ValueError("gacha duplicate marks must define 3, 4 and 5 stars")
        if set(self.star_up_costs) != {3, 4}:
            raise ValueError("star-up costs must define the 3-to-4 and 4-to-5 steps")
        return self


class PartnerLevelCostSegment(BaseModel):
    start_level: int = Field(ge=2, le=59)
    base: int = Field(gt=0)
    growth: int = Field(ge=0)


class PartnerGrowthDefinition(BaseModel):
    experience_interval_seconds: int = Field(gt=0)
    experience_per_stamina: int = Field(gt=0)
    # 鱼塘是资产轴，没有时长也没有体力，所以驻场伙伴按结算掉的周期数拿经验。
    pond_experience_per_cycle: int = Field(default=0, ge=0)
    # 畜牧周期是 8 小时，给的点数更高，两个资产轴产业的伙伴日均经验因此对齐。
    livestock_experience_per_cycle: int = Field(default=0, ge=0)
    level_cost_base: int = Field(gt=0)
    level_cost_growth: int = Field(ge=0)
    level_cost_segments: list[PartnerLevelCostSegment] = Field(default_factory=list)
    experience_books: dict[str, int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cost_segments(self):
        starts = [segment.start_level for segment in self.level_cost_segments]
        if starts != sorted(set(starts)):
            raise ValueError("partner experience segments must have unique increasing start levels")
        for segment in self.level_cost_segments:
            if segment.base < self.experience_for_next_level(segment.start_level - 1):
                raise ValueError("partner experience costs cannot decrease at a segment boundary")
        return self

    def experience_for_next_level(self, level: int) -> int:
        for segment in reversed(self.level_cost_segments):
            if level >= segment.start_level:
                return segment.base + segment.growth * (level - segment.start_level)
        return self.level_cost_base + self.level_cost_growth * max(0, level - 1)


class RewardDefinition(BaseModel):
    """一次性发放的奖励，贡品和传送门全完成共用同一个结构。"""

    coins: int = Field(default=0, ge=0)
    experience: int = Field(default=0, ge=0)
    talent_points: int = Field(default=0, ge=0)
    maple_flame: int = Field(default=0, ge=0)
    guide_leaves: int = Field(default=0, ge=0)
    items: list[RewardItemDefinition] = Field(default_factory=list, max_length=8)
    partner_ids: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_reward(self):
        if len(self.partner_ids) != len(set(self.partner_ids)):
            raise ValueError("reward cannot grant the same partner twice")
        return self

    @property
    def empty(self) -> bool:
        return not (
            self.coins
            or self.experience
            or self.talent_points
            or self.maple_flame
            or self.guide_leaves
            or self.items
            or self.partner_ids
        )


class FishCodexMilestoneDefinition(BaseModel):
    """鱼类图鉴的完成度档位。收齐若干种鱼给一次性奖励，是钓鱼的长线目标。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    required: int = Field(ge=1)
    reward: RewardDefinition = Field(default_factory=RewardDefinition)


class PortalTributeDefinition(BaseModel):
    """传送门要求的一项贡品。玩家可以分次交，交满结算这一项的奖励。"""

    id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    min_quality: int = Field(default=0, ge=0, le=5)
    reward: RewardDefinition = Field(default_factory=RewardDefinition)


class PortalDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    accent: str
    min_level: int = Field(default=1, ge=1)
    prerequisites: list[str] = Field(default_factory=list, max_length=4)
    tributes: list[PortalTributeDefinition] = Field(min_length=1, max_length=12)
    completion_reward: RewardDefinition = Field(default_factory=RewardDefinition)

    @model_validator(mode="after")
    def validate_portal(self):
        if self.id in self.prerequisites:
            raise ValueError(f"portal {self.id} cannot require itself")
        if len(self.prerequisites) != len(set(self.prerequisites)):
            raise ValueError(f"portal {self.id} repeats a prerequisite")
        tribute_ids = [tribute.id for tribute in self.tributes]
        if len(tribute_ids) != len(set(tribute_ids)):
            raise ValueError(f"portal {self.id} repeats a tribute id")
        return self


COMMISSIONABLE_KINDS = ("produce", "material", "product")


class CommissionTierDefinition(BaseModel):
    """一个难度档：平日和幸运日各自的抽取权重，以及这一档要求的件数区间。"""

    tier: int = Field(ge=1, le=9)
    name: str = Field(min_length=1)
    weight: float = Field(ge=0)
    lucky_weight: float = Field(ge=0)
    quantity_min: int = Field(ge=1)
    quantity_max: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_tier(self):
        if self.quantity_max < self.quantity_min:
            raise ValueError(f"commission tier {self.tier}: quantity_max must be >= quantity_min")
        return self


class CommissionItemDefinition(BaseModel):
    """把一个物品挂到某个难度档上。件数默认跟随档位，个别物品可以单独覆盖。"""

    item_id: str = Field(min_length=1)
    tier: int = Field(ge=1, le=9)
    quantity_min: int | None = Field(default=None, ge=1)
    quantity_max: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_override(self):
        if (self.quantity_min is None) != (self.quantity_max is None):
            raise ValueError(f"commission entry {self.item_id}: quantity override needs both bounds")
        if self.quantity_min is not None and self.quantity_max < self.quantity_min:
            raise ValueError(f"commission entry {self.item_id}: quantity_max must be >= quantity_min")
        return self

    def quantity_range(self, tier: CommissionTierDefinition) -> tuple[int, int]:
        if self.quantity_min is None:
            return tier.quantity_min, tier.quantity_max
        return self.quantity_min, self.quantity_max


class CommissionNpcDefinition(BaseModel):
    """委托人只是包装，line 里必须留出物品和件数的占位。"""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    line: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_line(self):
        for placeholder in ("{item}", "{quantity}"):
            if placeholder not in self.line:
                raise ValueError(f"commission npc {self.id}: line must contain {placeholder}")
        return self

    def render(self, item_name: str, quantity: int) -> str:
        return self.line.replace("{item}", item_name).replace("{quantity}", str(quantity))


class CommissionsDefinition(BaseModel):
    reset_hour: int = Field(default=4, ge=0, le=23)
    timezone: str = Field(default="Asia/Shanghai", min_length=1)
    min_level: int = Field(default=1, ge=1)
    reward_maple_flame: int = Field(gt=0)
    lucky_reward_maple_flame: int = Field(gt=0)
    forward_owner_share: float = Field(gt=0, le=1)
    forward_taker_share: float = Field(gt=0, le=1)
    daily_take_limit: int = Field(default=1, ge=1, le=10)
    board_limit: int = Field(default=30, ge=1, le=200)
    excluded_item_ids: list[str] = Field(default_factory=list)
    tiers: list[CommissionTierDefinition] = Field(min_length=1, max_length=9)
    entries: list[CommissionItemDefinition] = Field(min_length=1)
    npcs: list[CommissionNpcDefinition] = Field(min_length=4)

    @model_validator(mode="after")
    def validate_commissions(self):
        tiers = [entry.tier for entry in self.tiers]
        if len(tiers) != len(set(tiers)):
            raise ValueError("commission tiers must be unique")
        if not any(entry.weight > 0 for entry in self.tiers):
            raise ValueError("commissions need at least one tier with a positive weekday weight")
        if not any(entry.lucky_weight > 0 for entry in self.tiers):
            raise ValueError("commissions need at least one tier with a positive lucky-day weight")
        item_ids = [entry.item_id for entry in self.entries]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("commission entries must not repeat an item")
        unknown_tiers = {entry.tier for entry in self.entries} - set(tiers)
        if unknown_tiers:
            raise ValueError(f"commission entries reference unknown tiers: {sorted(unknown_tiers)}")
        npc_ids = [entry.id for entry in self.npcs]
        if len(npc_ids) != len(set(npc_ids)):
            raise ValueError("commission npc ids must be unique")
        return self

    @property
    def tier_map(self) -> dict[int, CommissionTierDefinition]:
        return {entry.tier: entry for entry in self.tiers}

    @property
    def entry_map(self) -> dict[str, CommissionItemDefinition]:
        return {entry.item_id: entry for entry in self.entries}

    @property
    def npc_map(self) -> dict[str, CommissionNpcDefinition]:
        return {entry.id: entry for entry in self.npcs}

    def reward_for(self, lucky: bool) -> int:
        return self.lucky_reward_maple_flame if lucky else self.reward_maple_flame

    def owner_share(self, reward: int) -> int:
        return max(1, round(reward * self.forward_owner_share))

    def taker_share(self, reward: int) -> int:
        return max(1, round(reward * self.forward_taker_share))


class ShopEntry(BaseModel):
    beta: bool = False
    id: str
    item_id: str
    price: int = Field(gt=0)
    currency: Literal["coins"] = "coins"
    min_level: int = Field(ge=1)


AchievementTier = Literal["blue", "purple", "gold"]
AchievementHook = Literal[
    "facility_stage", "pond_built", "mining_tasks", "refining_batches",
    "gathering_tasks", "gathering_items", "livestock_items", "animals_bred_species",
    "facilities_tier", "sailing_ship", "sailing_voyages", "sailing_routes",
    "sailing_upgrades", "sailing_items", "exploration_completed", "delve_completed", "delve_wins",
    "story_seen",
    "production_collections",
    "partners_owned",
    "player_level",
    "crops_harvested",
    "recipes_crafted",
    "commissions_completed",
    "talents_unlocked",
    "pond_harvested",
    "fish_codex_entries",
    "big_catch_caught",
    "max_production_quality",
    "animals_bred",
    "animals_cared",
    "livestock_specials",
    "animals_at_max_affection",
    "livestock_gene",
    "portal_completed",
]


class AchievementConditionDefinition(BaseModel):
    hook: AchievementHook
    params: dict[str, object] = Field(default_factory=dict)


class AchievementDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tier: AchievementTier
    reward_maple_flame: int = Field(gt=0)
    condition: AchievementConditionDefinition

    @model_validator(mode="after")
    def validate_tier_reward(self):
        expected = {"blue": 50, "purple": 100, "gold": 200}[self.tier]
        if self.reward_maple_flame != expected:
            raise ValueError(f"{self.tier} achievement reward must be {expected} maple flame")
        return self


class FacilityUpgradeDefinition(BaseModel):
    id: str
    name: str
    kind: Literal["farm", "feed", "refining"]
    stage: int = Field(ge=1)
    min_level: int = Field(ge=1)
    coins: int = Field(ge=0)
    inputs: list[RecipeInputDefinition] = Field(default_factory=list)
    capacity_bonus: int = Field(default=0, ge=0)
    feed_multiplier: float = Field(default=1, gt=0, le=1)


class RefiningRecipeDefinition(BaseModel):
    id: str
    name: str
    min_level: int = Field(ge=1)
    input_item_id: str
    input_quantity: int = Field(gt=0)
    produce_item_id: str
    top_hours: float = Field(gt=0)


class GameContent(BaseModel):
    schema_version: int = Field(ge=1)
    game: GameMeta
    world: WorldDefinition
    stamina: StaminaDefinition
    quality: QualitySystemDefinition
    industries: dict[str, IndustryRulesDefinition]
    levels: list[LevelDefinition]
    items: list[ItemDefinition]
    crops: list[CropDefinition]
    gathering_sites: list[GatheringSiteDefinition] = Field(default_factory=list)
    gathering_tasks: list[GatheringTaskDefinition] = Field(default_factory=list)
    exploration_expeditions: list[ExplorationExpeditionDefinition] = Field(default_factory=list)
    delve_enemies: list[DelveEnemyDefinition] = Field(default_factory=list)
    talents: list[TalentNodeDefinition] = Field(default_factory=list)
    crafting_stations: list[CraftingStationDefinition] = Field(default_factory=list)
    recipes: list[RecipeDefinition] = Field(default_factory=list)
    mining_sites: list[MiningSiteDefinition] = Field(default_factory=list)
    mining_tasks: list[MiningTaskDefinition] = Field(default_factory=list)
    fishing_spots: list[FishingSpotDefinition] = Field(default_factory=list)
    fishing_combo: FishingComboDefinition = Field(default_factory=FishingComboDefinition)
    fish_codex_milestones: list[FishCodexMilestoneDefinition] = Field(default_factory=list)
    # 资产格（鱼塘、畜栏）换伙伴的自由窗口：周期开头这个比例之内随时换，过了就得排队
    # 到下个周期开始。否则在周期最后一秒换上强力伙伴，整个周期都会按新伙伴结算。
    asset_partner_free_window: float = Field(default=0.1, ge=0, lt=1)
    pond_tiers: list[PondTierDefinition] = Field(default_factory=list)
    ponds: list[PondSlotDefinition] = Field(default_factory=list)
    pond_species: list[PondSpeciesDefinition] = Field(default_factory=list)
    feed_slot: FeedSlotDefinition | None = None
    livestock: LivestockRulesDefinition | None = None
    livestock_facilities: list[LivestockFacilityDefinition] = Field(default_factory=list)
    livestock_species: list[LivestockSpeciesDefinition] = Field(default_factory=list)
    task_items: list[TaskItemDefinition] = Field(default_factory=list)
    gacha_economy: GachaEconomyDefinition
    partner_growth: PartnerGrowthDefinition
    portals: list[PortalDefinition] = Field(default_factory=list)
    commissions: CommissionsDefinition
    monthly_card: MonthlyCardDefinition = Field(default_factory=MonthlyCardDefinition)
    achievements: list[AchievementDefinition] = Field(default_factory=list)
    shop: list[ShopEntry]
    facility_upgrades: list[FacilityUpgradeDefinition] = Field(default_factory=list)
    refining_recipes: list[RefiningRecipeDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self):
        def unique(values: list[str], label: str):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} id")

        unique([item.id for item in self.items], "item")
        unique([entry.id for entry in self.facility_upgrades], "facility upgrade")
        unique([entry.id for entry in self.refining_recipes], "refining recipe")
        for upgrade in self.facility_upgrades:
            for ingredient in upgrade.inputs:
                if ingredient.item_id not in self.item_map:
                    raise ValueError("unknown facility material")
        for recipe in self.refining_recipes:
            if recipe.input_item_id not in self.item_map or recipe.produce_item_id not in self.item_map:
                raise ValueError("unknown refining item")
            raw, product = self.item_map[recipe.input_item_id], self.item_map[recipe.produce_item_id]
            if not raw.has_quality or not product.has_quality or product.sell_price <= raw.sell_price * recipe.input_quantity:
                raise ValueError("refining requires quality items and positive ordinary added value")
        for pond in self.ponds:
            if pond.species_id and pond.species_id not in {s.id for s in self.pond_species}:
                raise ValueError("unknown pond species")
            if any(i.item_id not in self.item_map for i in pond.build_materials):
                raise ValueError("unknown pond build material")
        unique([crop.id for crop in self.crops], "crop")
        unique([entry.id for entry in self.shop], "shop")
        unique([entry.id for entry in self.gathering_sites], "gathering site")
        unique([entry.id for entry in self.gathering_tasks], "gathering task")
        unique([entry.id for entry in self.exploration_expeditions], "exploration expedition")
        unique([entry.id for entry in self.delve_enemies], "delve enemy")
        unique([entry.id for entry in self.talents], "talent")
        unique([entry.id for entry in self.crafting_stations], "crafting station")
        unique([entry.id for entry in self.recipes], "recipe")
        unique([entry.id for entry in self.mining_sites], "mining site")
        unique([entry.id for entry in self.mining_tasks], "mining task")
        unique([entry.id for entry in self.fishing_spots], "fishing spot")
        unique([entry.id for entry in self.fish_codex_milestones], "fish codex milestone")
        unique([str(entry.level) for entry in self.pond_tiers], "pond tier")
        unique([entry.id for entry in self.ponds], "pond")
        unique([entry.id for entry in self.pond_species], "pond species")
        unique([entry.id for entry in self.livestock_facilities], "livestock facility")
        unique([entry.id for entry in self.livestock_species], "livestock species")
        unique([entry.id for entry in self.task_items], "task item")
        unique([entry.id for entry in self.portals], "portal")
        unique([entry.id for entry in self.achievements], "achievement")
        unique([tribute.id for portal in self.portals for tribute in portal.tributes], "portal tribute")
        levels = [entry.level for entry in self.levels]
        unique([str(level) for level in levels], "level")
        if levels != sorted(levels) or not levels or levels[0] != 1:
            raise ValueError("levels must be sorted and start at 1")
        xp = [entry.total_xp for entry in self.levels]
        if xp != sorted(xp) or xp[0] != 0:
            raise ValueError("level total_xp must be sorted and start at 0")
        if "farming" not in self.industries:
            raise ValueError("farming industry rules are required")
        if "gathering" not in self.industries:
            raise ValueError("gathering industry rules are required")
        if "crafting" not in self.industries:
            raise ValueError("crafting industry rules are required")
        if "mining" not in self.industries:
            raise ValueError("mining industry rules are required")
        if self.fishing_spots or self.ponds:
            if "aquatic" not in self.industries:
                raise ValueError("aquatic industry rules are required once aquatic content exists")
        if self.livestock_facilities and "livestock" not in self.industries:
            raise ValueError("livestock industry rules are required once livestock content exists")
        if self.exploration_expeditions and "exploration" not in self.industries:
            raise ValueError("exploration industry rules are required once exploration content exists")

        items = {item.id for item in self.items}
        if self.stamina.potion_item_id not in items:
            raise ValueError("stamina potion references an unknown item")
        if self.monthly_card.daily_item_amount and self.monthly_card.daily_item_id not in items:
            raise ValueError("monthly card daily reward references an unknown item")
        for crop in self.crops:
            if crop.seed_item_id not in items or crop.produce_item_id not in items:
                raise ValueError(f"crop {crop.id} references an unknown item")
            if not self.item_map[crop.produce_item_id].has_quality:
                raise ValueError(f"crop {crop.id} output must support quality")
        gathering_site_ids = {site.id for site in self.gathering_sites}
        for task in self.gathering_tasks:
            if task.site_id not in gathering_site_ids:
                raise ValueError(f"gathering task {task.id} references an unknown site")
            if task.season_id != self.gathering_site_map[task.site_id].season_id:
                raise ValueError(f"gathering task {task.id} must use its site's season")
            if any(output.item_id not in items for output in task.outputs):
                raise ValueError(f"gathering task {task.id} references an unknown item")
            if any(not self.item_map[output.item_id].has_quality for output in task.outputs):
                raise ValueError(f"gathering task {task.id} output must support quality")
        enemy_ids = {enemy.id for enemy in self.delve_enemies}
        for expedition in self.exploration_expeditions:
            for event in expedition.events:
                for choice in event.choices:
                    if choice.battle is not None:
                        if expedition.kind != "delve":
                            raise ValueError(
                                f"exploration choice {choice.id} can only start a battle on a delve expedition"
                            )
                        unknown = [entry for entry in choice.battle.enemy_ids if entry not in enemy_ids]
                        if unknown:
                            raise ValueError(
                                f"exploration choice {choice.id} references unknown enemies: {', '.join(unknown)}"
                            )
                    outcomes = [
                        choice.success,
                        choice.failure,
                        choice.critical_success,
                        choice.critical_failure,
                    ]
                    for outcome in (entry for entry in outcomes if entry is not None):
                        for reward in outcome.rewards:
                            if reward.item_id not in items:
                                raise ValueError(
                                    f"exploration event {event.id} references an unknown item"
                                )
                            if not self.item_map[reward.item_id].has_quality:
                                raise ValueError(
                                    f"exploration event {event.id} reward must support quality"
                                )
                        for reward in outcome.fixed_rewards:
                            if reward.item_id not in items:
                                raise ValueError(
                                    f"exploration event {event.id} references an unknown item"
                                )
                            if self.item_map[reward.item_id].has_quality:
                                raise ValueError(
                                    f"exploration event {event.id} fixed reward must be a quality-free item"
                                )
        talent_ids = {talent.id for talent in self.talents}
        for talent in self.talents:
            if talent.industry not in self.industries:
                raise ValueError(f"talent {talent.id} references an unknown industry")
            if any(prerequisite not in talent_ids for prerequisite in talent.prerequisites):
                raise ValueError(f"talent {talent.id} references an unknown prerequisite")
        crafting_station_ids = {station.id for station in self.crafting_stations}
        from red_leaf_town.recipe_unlocks import recipe_unlock_hook_exists, validate_recipe_unlock
        for recipe in self.recipes:
            if recipe.station_id not in crafting_station_ids:
                raise ValueError(f"recipe {recipe.id} references an unknown crafting station")
            if recipe.produce_item_id not in items:
                raise ValueError(f"recipe {recipe.id} references an unknown output item")
            # 加工是唯一允许产出无品质物品的产业：装备一件就是一组固定数值，不进五档品质。
            output = self.item_map[recipe.produce_item_id]
            if not output.has_quality and output.kind != "equipment":
                raise ValueError(f"recipe {recipe.id} output must support quality")
            if any(requirement.item_id not in items for requirement in recipe.inputs):
                raise ValueError(f"recipe {recipe.id} references an unknown input item")
            if not recipe_unlock_hook_exists(recipe.unlock_condition.hook):
                raise ValueError(f"recipe {recipe.id} references an unknown unlock hook")
            validate_recipe_unlock(recipe.unlock_condition.hook, recipe.unlock_condition.params)
        mining_site_ids = {site.id for site in self.mining_sites}
        for task in self.mining_tasks:
            if task.site_id not in mining_site_ids:
                raise ValueError(f"mining task {task.id} references an unknown site")
            if task.produce_item_id not in items:
                raise ValueError(f"mining task {task.id} references an unknown item")
            if not self.item_map[task.produce_item_id].has_quality:
                raise ValueError(f"mining task {task.id} output must support quality")
            if task.duration_seconds < task.stamina_cost * self.stamina.restore_seconds:
                # 采矿是预支这段时间内恢复的体力，可以更慢，但不能凭空造出体力。
                raise ValueError(f"mining task {task.id} duration cannot undercut stamina recovery time")
        for entry in self.task_items:
            if entry.effect == "instant_finish" and entry.timing != "active":
                raise ValueError(f"task item {entry.id} must be used on an active task")
            if entry.effect != "instant_finish" and entry.timing != "start":
                raise ValueError(f"task item {entry.id} must be used when a task starts")
            if any(industry not in self.industries for industry in entry.eligible_industries):
                raise ValueError(f"task item {entry.id} references an unknown industry")
        if any(item_id not in items for item_id in self.partner_growth.experience_books):
            raise ValueError("partner growth references an unknown experience book")
        self._validate_aquatic(items)
        self._validate_livestock(items)
        self._validate_portals(items)
        self._validate_commissions()
        self._validate_achievements()
        for entry in self.shop:
            if entry.item_id not in items:
                raise ValueError(f"shop {entry.id} references an unknown item")
        return self

    def _validate_aquatic(self, items: set[str]) -> None:
        for spot in self.fishing_spots:
            for output in spot.outputs:
                if output.item_id not in items:
                    raise ValueError(f"fishing spot {spot.id} references an unknown item")
            if spot.big_catch is None:
                continue
            for item_id in (spot.big_catch.item_id, spot.big_catch.fallback_item_id):
                if item_id not in items:
                    raise ValueError(f"fishing spot {spot.id} big catch references an unknown item")
            if not self.item_map[spot.big_catch.item_id].has_quality:
                raise ValueError(f"fishing spot {spot.id} big catch item must support quality")
        for milestone in self.fish_codex_milestones:
            self._validate_reward(milestone.reward, items, f"fish codex milestone {milestone.id}")
        tiers = {tier.level for tier in self.pond_tiers}
        for pond in self.ponds:
            if pond.tier not in tiers:
                raise ValueError(f"pond {pond.id} references an unknown tier")
        for species in self.pond_species:
            if species.fry_item_id not in items or species.produce_item_id not in items:
                raise ValueError(f"pond species {species.id} references an unknown item")
            if self.item_map[species.fry_item_id].has_quality:
                raise ValueError(f"pond species {species.id} fry must be a quality-free item")
            if not self.item_map[species.produce_item_id].has_quality:
                raise ValueError(f"pond species {species.id} output must support quality")
        if self.ponds and not self.pond_species:
            raise ValueError("ponds require at least one species to stock")
        if (self.ponds or self.fishing_spots) and self.feed_slot is None:
            raise ValueError("aquatic content requires a feed slot definition")

    def _validate_livestock(self, items: set[str]) -> None:
        facility_ids = {facility.id for facility in self.livestock_facilities}
        for facility in self.livestock_facilities:
            if facility.replaces and facility.replaces not in facility_ids:
                raise ValueError(f"livestock facility {facility.id} replaces an unknown facility")
            if facility.replaces == facility.id:
                raise ValueError(f"livestock facility {facility.id} cannot replace itself")
            for tier in facility.tiers:
                for material in tier.build_materials:
                    if material.item_id not in items:
                        raise ValueError(f"livestock facility {facility.id} needs an unknown material")
        replaced = [facility.replaces for facility in self.livestock_facilities if facility.replaces]
        if len(replaced) != len(set(replaced)):
            raise ValueError("a livestock facility cannot be replaced by two different buildings")
        for facility in self.livestock_facilities:
            successor = next(
                (entry for entry in self.livestock_facilities if entry.replaces == facility.id),
                None,
            )
            if successor is None:
                continue
            # 回收时动物要整栏迁入，装不下就会丢东西。
            if successor.tier(1).capacity < facility.tier(1).capacity:
                raise ValueError(f"livestock facility {successor.id} is too small to absorb {facility.id}")
            if successor.category != facility.category:
                raise ValueError(f"livestock facility {successor.id} cannot absorb another category")
        for species in self.livestock_species:
            if species.required_facility_id:
                facility = self.livestock_facility_map.get(species.required_facility_id)
                if (facility is None or facility.category != species.category
                        or not any(t.level == species.required_facility_tier for t in facility.tiers)):
                    raise ValueError(f"livestock species {species.id} has an invalid required facility tier")
            if species.produce_item_id not in items:
                raise ValueError(f"livestock species {species.id} references an unknown item")
            if not self.item_map[species.produce_item_id].has_quality:
                raise ValueError(f"livestock species {species.id} output must support quality")
            if species.special_item_id:
                if species.special_item_id not in items:
                    raise ValueError(f"livestock species {species.id} references an unknown special item")
                if self.item_map[species.special_item_id].has_quality:
                    raise ValueError(f"livestock species {species.id} special output must be quality-free")
            breeding = species.breeding
            if breeding.mode == "incubate":
                if breeding.incubate_item_id not in items:
                    raise ValueError(f"livestock species {species.id} incubates an unknown item")
                if not self.item_map[breeding.incubate_item_id].has_quality:
                    raise ValueError(f"livestock species {species.id} incubation item must support quality")
            if not any(facility.category == species.category for facility in self.livestock_facilities):
                raise ValueError(f"livestock species {species.id} has nowhere to live")
        if self.livestock_facilities and not self.livestock_species:
            raise ValueError("livestock facilities require at least one species")
        if self.livestock_facilities and self.livestock is None:
            raise ValueError("livestock content requires livestock rules")
        if self.livestock_facilities and self.feed_slot is None:
            raise ValueError("livestock content requires a feed slot definition")

    def _validate_portals(self, items: set[str]) -> None:
        portal_ids = {portal.id for portal in self.portals}
        for portal in self.portals:
            unknown = [entry for entry in portal.prerequisites if entry not in portal_ids]
            if unknown:
                raise ValueError(f"portal {portal.id} references unknown prerequisites: {', '.join(unknown)}")
            for tribute in portal.tributes:
                if tribute.item_id not in items:
                    raise ValueError(f"portal tribute {tribute.id} references an unknown item")
                if tribute.min_quality and not self.item_map[tribute.item_id].has_quality:
                    raise ValueError(f"portal tribute {tribute.id} demands a quality the item cannot have")
                self._validate_reward(tribute.reward, items, f"portal tribute {tribute.id}")
            self._validate_reward(portal.completion_reward, items, f"portal {portal.id}")
        self._validate_portal_graph()

    def _validate_commissions(self) -> None:
        """新加一个可生产的物品就必须给它定难度档，否则它永远不会出现在委托里。"""
        commissions = self.commissions
        item_map = self.item_map
        unknown = {entry.item_id for entry in commissions.entries} - set(item_map)
        if unknown:
            raise ValueError(f"commissions reference unknown items: {', '.join(sorted(unknown))}")
        excluded = set(commissions.excluded_item_ids)
        unknown_excluded = excluded - set(item_map)
        if unknown_excluded:
            raise ValueError(f"commissions exclude unknown items: {', '.join(sorted(unknown_excluded))}")
        listed = {entry.item_id for entry in commissions.entries}
        overlap = listed & excluded
        if overlap:
            raise ValueError(f"commissions both list and exclude: {', '.join(sorted(overlap))}")
        wrong_kind = [entry.item_id for entry in commissions.entries if item_map[entry.item_id].kind == "seed"]
        if wrong_kind:
            raise ValueError(f"commissions cannot ask for seeds: {', '.join(sorted(wrong_kind))}")
        missing = sorted(
            item.id
            for item in self.items
            if item.kind in COMMISSIONABLE_KINDS and item.id not in listed and item.id not in excluded
        )
        if missing:
            raise ValueError(f"commissions do not grade these items: {', '.join(missing)}")

    def _validate_achievements(self) -> None:
        crop_ids = set(self.crop_map)
        recipe_ids = set(self.recipe_map)
        portal_ids = set(self.portal_map)
        species_ids = set(self.pond_species_map)
        for achievement in self.achievements:
            hook = achievement.condition.hook
            params = achievement.condition.params
            list_references = {
                "mining_tasks": ("task_ids", set(self.mining_task_map)),
                "gathering_tasks": ("task_ids", set(self.gathering_task_map)),
                "gathering_items": ("item_ids", set(self.item_map)),
                "livestock_items": ("item_ids", set(self.item_map)),
                "animals_bred_species": ("species_ids", set(self.livestock_species_map)),
                "facilities_tier": ("facility_ids", set(self.livestock_facility_map)),
                "sailing_items": ("item_ids", set(self.item_map)),
            }
            if hook == "sailing_routes":
                from .sailing_content import load_sailing_content
                list_references[hook] = ("route_ids", {r.id for r in load_sailing_content().routes})
            if hook in list_references:
                key, allowed = list_references[hook]
                values = params.get(key)
                if (not isinstance(values, list) or not values
                        or any(not isinstance(value, str) for value in values)
                        or len(set(values)) != len(values) or not set(values) <= allowed):
                    raise ValueError(f"achievement {achievement.id} has invalid {key}")
            if hook == "facility_stage":
                kind, stage = params.get("kind"), params.get("stage")
                if type(stage) is not int or not any(u.kind == kind and u.stage == stage for u in self.facility_upgrades):
                    raise ValueError(f"achievement {achievement.id} has invalid facility stage")
            if hook == "pond_built" and params.get("pond_id") not in self.pond_map:
                raise ValueError(f"achievement {achievement.id} has invalid pond")
            if hook == "refining_batches":
                minimum = params.get("min_quality", 1)
                if type(minimum) is not int or minimum not in range(1, 6):
                    raise ValueError(f"achievement {achievement.id} has invalid refining quality")
            if hook == "gathering_items":
                task = self.gathering_task_map.get(str(params.get("task_id")))
                if task is None or not set(params["item_ids"]) <= {o.item_id for o in task.outputs}:
                    raise ValueError(f"achievement {achievement.id} has invalid gathering task or outputs")
            if hook in {"exploration_completed", "delve_completed", "delve_wins"}:
                expedition = self.exploration_expedition_map.get(str(params.get("expedition_id")))
                if expedition is None or (hook.startswith("delve_") and expedition.kind != "delve"):
                    raise ValueError(f"achievement {achievement.id} has invalid expedition")
            numeric_keys = {
                "refining_batches": "count",
                "facilities_tier": "tier", "sailing_voyages": "count", "sailing_upgrades": "level",
                "sailing_items": "count", "delve_wins": "count",
            }
            if hook in numeric_keys:
                value = params.get(numeric_keys[hook])
                if type(value) is not int or value <= 0:
                    raise ValueError(f"achievement {achievement.id} requires a positive integer target")
                if hook == "sailing_items" and value > len(params["item_ids"]):
                    raise ValueError(f"achievement {achievement.id} item target exceeds its fixed list")
                if hook == "sailing_upgrades" and value > 3:
                    raise ValueError(f"achievement {achievement.id} exceeds sailing upgrade limit")
                if hook == "facilities_tier" and any(
                    value not in {tier.level for tier in self.livestock_facility_map[fid].tiers}
                    for fid in params["facility_ids"]
                ):
                    raise ValueError(f"achievement {achievement.id} has invalid facility tier")
            if hook == "production_collections" and str(params.get("industry")) not in self.industries:
                raise ValueError(f"achievement {achievement.id} references an unknown industry")
            if hook == "crops_harvested" and not {str(entry) for entry in params.get("crop_ids", [])} <= crop_ids:
                raise ValueError(f"achievement {achievement.id} references an unknown crop")
            if hook == "recipes_crafted" and not {str(entry) for entry in params.get("recipe_ids", [])} <= recipe_ids:
                raise ValueError(f"achievement {achievement.id} references an unknown recipe")
            if hook == "portal_completed" and str(params.get("portal_id")) not in portal_ids:
                raise ValueError(f"achievement {achievement.id} references an unknown portal")
            if hook == "pond_harvested" and str(params.get("species_id")) not in species_ids:
                raise ValueError(f"achievement {achievement.id} references an unknown pond species")

    def _validate_reward(self, reward: RewardDefinition, items: set[str], label: str) -> None:
        for entry in reward.items:
            if entry.item_id not in items:
                raise ValueError(f"{label} rewards an unknown item")
            if entry.quality and not self.item_map[entry.item_id].has_quality:
                raise ValueError(f"{label} rewards a quality the item cannot have")

    def _validate_portal_graph(self) -> None:
        """解锁是树状的，成环会让整棵子树永远打不开，所以加载时就拦下来。"""
        prerequisites = {portal.id: list(portal.prerequisites) for portal in self.portals}
        resolved: set[str] = set()
        pending = set(prerequisites)
        while pending:
            ready = {
                portal_id
                for portal_id in pending
                if all(entry in resolved for entry in prerequisites[portal_id])
            }
            if not ready:
                raise ValueError(f"portal prerequisites form a cycle: {', '.join(sorted(pending))}")
            resolved |= ready
            pending -= ready

    @property
    def item_map(self) -> dict[str, ItemDefinition]:
        return {item.id: item for item in self.items}

    @property
    def crop_map(self) -> dict[str, CropDefinition]:
        return {crop.id: crop for crop in self.crops}

    @property
    def shop_map(self) -> dict[str, ShopEntry]:
        return {entry.id: entry for entry in self.shop}

    @property
    def gathering_site_map(self) -> dict[str, GatheringSiteDefinition]:
        return {entry.id: entry for entry in self.gathering_sites}

    @property
    def gathering_task_map(self) -> dict[str, GatheringTaskDefinition]:
        return {entry.id: entry for entry in self.gathering_tasks}

    @property
    def exploration_expedition_map(self) -> dict[str, ExplorationExpeditionDefinition]:
        return {entry.id: entry for entry in self.exploration_expeditions}

    @property
    def delve_enemy_map(self) -> dict[str, DelveEnemyDefinition]:
        return {entry.id: entry for entry in self.delve_enemies}

    @property
    def talent_map(self) -> dict[str, TalentNodeDefinition]:
        return {entry.id: entry for entry in self.talents}

    @property
    def crafting_station_map(self) -> dict[str, CraftingStationDefinition]:
        return {entry.id: entry for entry in self.crafting_stations}

    @property
    def recipe_map(self) -> dict[str, RecipeDefinition]:
        return {entry.id: entry for entry in self.recipes}

    @property
    def mining_site_map(self) -> dict[str, MiningSiteDefinition]:
        return {entry.id: entry for entry in self.mining_sites}

    @property
    def mining_task_map(self) -> dict[str, MiningTaskDefinition]:
        return {entry.id: entry for entry in self.mining_tasks}

    @property
    def task_item_map(self) -> dict[str, TaskItemDefinition]:
        return {entry.id: entry for entry in self.task_items}

    @property
    def fishing_spot_map(self) -> dict[str, FishingSpotDefinition]:
        return {entry.id: entry for entry in self.fishing_spots}

    @property
    def pond_map(self) -> dict[str, PondSlotDefinition]:
        return {entry.id: entry for entry in self.ponds}

    @property
    def pond_tier_map(self) -> dict[int, PondTierDefinition]:
        return {entry.level: entry for entry in self.pond_tiers}

    @property
    def pond_species_map(self) -> dict[str, PondSpeciesDefinition]:
        return {entry.id: entry for entry in self.pond_species}

    @property
    def livestock_facility_map(self) -> dict[str, LivestockFacilityDefinition]:
        return {entry.id: entry for entry in self.livestock_facilities}

    @property
    def livestock_species_map(self) -> dict[str, LivestockSpeciesDefinition]:
        return {entry.id: entry for entry in self.livestock_species}

    @property
    def portal_map(self) -> dict[str, PortalDefinition]:
        return {entry.id: entry for entry in self.portals}

    @property
    def achievement_map(self) -> dict[str, AchievementDefinition]:
        return {entry.id: entry for entry in self.achievements}

    @property
    def portal_tribute_map(self) -> dict[str, tuple[PortalDefinition, PortalTributeDefinition]]:
        return {
            tribute.id: (portal, tribute)
            for portal in self.portals
            for tribute in portal.tributes
        }

    def level_for_xp(self, experience: int, max_level: int | None = None) -> LevelDefinition:
        current = self.levels[0]
        for entry in self.levels:
            if experience < entry.total_xp or (max_level is not None and entry.level > max_level):
                break
            current = entry
        return current

    def level_definition(self, level: int) -> LevelDefinition:
        eligible = [entry for entry in self.levels if entry.level <= level]
        return eligible[-1] if eligible else self.levels[0]


DEFAULT_CONTENT_PATH = Path(__file__).resolve().parents[2] / "data" / "game.json"
_SAVE_LOCK = Lock()


@lru_cache(maxsize=4)
def load_content(path: str | Path = DEFAULT_CONTENT_PATH) -> GameContent:
    content_path = Path(path)
    return GameContent.model_validate(json.loads(content_path.read_text(encoding="utf-8")))


def save_content(content: GameContent, path: str | Path = DEFAULT_CONTENT_PATH) -> None:
    content_path = Path(path)
    payload = content.model_dump_json(indent=2)
    with _SAVE_LOCK:
        content_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = content_path.with_suffix(f"{content_path.suffix}.tmp")
        temporary.write_text(f"{payload}\n", encoding="utf-8")
        temporary.replace(content_path)
        load_content.cache_clear()
