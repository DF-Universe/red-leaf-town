from __future__ import annotations

from collections.abc import Callable, Iterable, MutableMapping
from dataclasses import dataclass
from typing import Any


TraitHandler = Callable[[MutableMapping[str, Any]], None]

TRAIT_PHASES = frozenset({
    "task_prepare",
    "instant_action",
    "asset_prepare",
    "output_draw",
    "quality_roll",
    "result_finalize",
    "exploration_event",
    "delve_attack",
    "livestock_segment",
    "sailing_prepare",
})


@dataclass(frozen=True)
class PartnerTraitDefinition:
    code: str
    name: str
    description: str
    handler: TraitHandler | None = None
    phases: frozenset[str] = frozenset()
    min_breakthrough: int = 0

    @property
    def implemented(self) -> bool:
        return self.handler is not None


_TRAITS: dict[str, PartnerTraitDefinition] = {}


def register_partner_trait(
    code: str,
    name: str,
    description: str,
    *,
    phases: Iterable[str] = ("task_prepare",),
    replace: bool = False,
    min_breakthrough: int = 0,
):
    normalized = str(code).strip()
    if not normalized:
        raise ValueError("trait code is required")
    normalized_phases = frozenset(str(phase).strip() for phase in phases)
    unknown_phases = normalized_phases - TRAIT_PHASES
    if unknown_phases:
        raise ValueError(f"unknown partner trait phases: {', '.join(sorted(unknown_phases))}")
    if not normalized_phases:
        raise ValueError("implemented partner traits require at least one phase")

    def decorator(handler: TraitHandler) -> TraitHandler:
        if normalized in _TRAITS and not replace:
            raise ValueError(f"partner trait {normalized} is already registered")
        _TRAITS[normalized] = PartnerTraitDefinition(
            normalized,
            name,
            description,
            handler,
            normalized_phases,
            min_breakthrough,
        )
        return handler

    return decorator


def declare_partner_trait(code: str, name: str, description: str) -> None:
    normalized = str(code).strip()
    if not normalized:
        raise ValueError("trait code is required")
    if normalized in _TRAITS:
        raise ValueError(f"partner trait {normalized} is already registered")
    _TRAITS[normalized] = PartnerTraitDefinition(normalized, name, description)


def partner_trait_catalog() -> list[PartnerTraitDefinition]:
    return [_TRAITS[code] for code in sorted(_TRAITS, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))]


def partner_trait_codes() -> set[str]:
    return set(_TRAITS)


def active_partner_trait_codes(codes: list[str], breakthrough: int = 0) -> list[str]:
    return [code for code in codes if _TRAITS[code].min_breakthrough <= breakthrough]


def execute_partner_traits(codes: list[str], context: MutableMapping[str, Any]) -> list[str]:
    executed: list[str] = []
    for code in codes:
        definition = _TRAITS.get(str(code))
        if definition is None:
            raise KeyError(f"unknown partner trait: {code}")
        if definition.handler is None:
            continue
        phase = str(context.get("phase") or "").strip()
        if phase and phase not in definition.phases:
            continue
        previous_code = context.get("trait_code")
        context["trait_code"] = definition.code
        try:
            definition.handler(context)
        finally:
            if previous_code is None:
                context.pop("trait_code", None)
            else:
                context["trait_code"] = previous_code
        executed.append(definition.code)
    return executed


def record_partner_trait_effect(
    context: MutableMapping[str, Any],
    effect: str,
    *,
    value: int | float | str | bool | None = None,
    params: dict[str, Any] | None = None,
    stacking_group: str = "",
) -> dict[str, Any]:
    """把已经展开的伙伴效果写入当前任务或资产快照。"""

    trait_code = str(context.get("trait_code") or "").strip()
    source_partner_id = str(context.get("source_partner_id") or "").strip()
    if not trait_code or not source_partner_id:
        raise ValueError("trait effect context requires trait_code and source_partner_id")
    entry: dict[str, Any] = {
        "source_type": "partner_trait",
        "trait_code": trait_code,
        "source_partner_id": source_partner_id,
        "phase": str(context.get("phase") or ""),
        "effect": str(effect).strip(),
    }
    if value is not None:
        entry["value"] = value
    if params:
        entry["params"] = dict(params)
    if stacking_group:
        entry["stacking_group"] = stacking_group
    context.setdefault("applied_effects", []).append(entry)
    return entry


for _code in ("1", "2", "3", "4"):
    declare_partner_trait(_code, f"{_code}号特性", "效果将在 Python 规则中定义")


def _industry_is(context: MutableMapping[str, Any], *industries: str) -> bool:
    return str(context.get("industry") or "") in industries


def _action_is(context: MutableMapping[str, Any], action: str) -> bool:
    return str(context.get("action") or "") == action


def _has_content_tag(context: MutableMapping[str, Any], *tags: str) -> bool:
    return bool(set(context.get("content_tags") or ()) & set(tags))


def _weather_is(context: MutableMapping[str, Any], *weather_ids: str) -> bool:
    world = context.get("world") or {}
    weather = world.get("weather") or {}
    return str(weather.get("id") or "") in weather_ids


def _multiply(
    context: MutableMapping[str, Any],
    field: str,
    multiplier: float,
    *,
    effect: str | None = None,
    stacking_group: str = "",
) -> None:
    context[field] = float(context.get(field, 1.0)) * multiplier
    record_partner_trait_effect(
        context,
        effect or field,
        value=multiplier,
        stacking_group=stacking_group,
    )


def _add(
    context: MutableMapping[str, Any],
    field: str,
    value: int | float,
    *,
    effect: str | None = None,
    stacking_group: str = "",
) -> None:
    context[field] = context.get(field, 0) + value
    record_partner_trait_effect(
        context,
        effect or field,
        value=value,
        stacking_group=stacking_group,
    )


def _reroll_one_quality(context: MutableMapping[str, Any]) -> None:
    record_partner_trait_effect(
        context,
        "reroll_one_quality_take_higher",
        params={"count": 1},
        stacking_group="quality_reroll",
    )


def _promote_lowest_quality(context: MutableMapping[str, Any], chance: float) -> None:
    record_partner_trait_effect(
        context,
        "promote_lowest_quality",
        params={"chance": chance, "levels": 1},
        stacking_group="quality_promotion",
    )


@register_partner_trait(
    "flow_treasure",
    "随流拾珍",
    "陪钓时，稀有鱼和大物的抽取权重提高12%。",
    phases=("instant_action",),
)
def _flow_treasure(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "aquatic") and _action_is(context, "fishing_cast"):
        _multiply(context, "rare_weight_multiplier", 1.12, stacking_group="fishing_rare_weight")


@register_partner_trait(
    "fodder_fields",
    "丰饲田",
    "农作产物可作饲料时，固定额外产出1件。",
    phases=("task_prepare",),
)
def _fodder_fields(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming") and _has_content_tag(context, "fodder"):
        _add(context, "yield_bonus", 1, stacking_group="flat_yield")


@register_partner_trait(
    "golden_line",
    "金色鱼线",
    "陪钓时，稀有鱼和大物的抽取权重提高15%。",
    phases=("instant_action",),
)
def _golden_line(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "aquatic") and _action_is(context, "fishing_cast"):
        _multiply(context, "rare_weight_multiplier", 1.15, stacking_group="fishing_rare_weight")


@register_partner_trait(
    "steady_craft",
    "熟练手作",
    "加工任务的品质能力增加15。",
    phases=("task_prepare",),
)
def _steady_craft(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "crafting"):
        _add(context, "quality_ability_bonus", 15, stacking_group="quality_ability")


@register_partner_trait(
    "dream_forage",
    "梦游拾遗",
    "执行采集任务时，额外抽取1次产出。",
    phases=("output_draw",),
)
def _dream_forage(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "gathering"):
        _add(context, "draw_bonus", 1, stacking_group="draw_count")


@register_partner_trait(
    "ore_chaser",
    "追脉",
    "采矿任务固定额外产出1件矿物。",
    phases=("task_prepare",),
)
def _ore_chaser(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "mining"):
        _add(context, "yield_bonus", 1, stacking_group="flat_yield")


@register_partner_trait(
    "rain_garden",
    "雨庭",
    "小雨天气开始农作时，品质能力增加20。",
    phases=("task_prepare",),
)
def _rain_garden(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming") and _weather_is(context, "rain"):
        _add(context, "quality_ability_bonus", 20, stacking_group="quality_ability")


@register_partner_trait(
    "varied_forage",
    "各有所获",
    "采集首次抽到已经出现的物品时，改从尚未出现的物品中重抽一次。",
    phases=("output_draw",),
)
def _varied_forage(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "gathering"):
        record_partner_trait_effect(
            context,
            "reroll_first_duplicate",
            stacking_group="output_variety",
        )


@register_partner_trait(
    "ghostfire_refinement",
    "鬼火精制",
    "农作或加工结算时，有30%概率将最低品质的一件产物提升一级。",
    phases=("result_finalize",),
)
def _ghostfire_refinement(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming", "crafting"):
        _promote_lowest_quality(context, 0.30)


@register_partner_trait(
    "deep_soil_edge",
    "厚土藏锋",
    "农作任务的品质能力提高10%。",
    phases=("task_prepare",),
)
def _deep_soil_edge(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming"):
        _multiply(context, "quality_ability_multiplier", 1.10, stacking_group="quality_ability")


@register_partner_trait(
    "listen_to_rock",
    "静听岩层",
    "采矿任务的品质能力提高12%。",
    phases=("task_prepare",),
)
def _listen_to_rock(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "mining"):
        _multiply(context, "quality_ability_multiplier", 1.12, stacking_group="quality_ability")


@register_partner_trait(
    "ancient_formula",
    "古方",
    "加工药品时，随机一件成品的品质重投一次并取较高值。",
    phases=("quality_roll",),
)
def _ancient_formula(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "crafting") and _has_content_tag(context, "medicine"):
        _reroll_one_quality(context)


@register_partner_trait(
    "overcast_worker",
    "阴翳作业",
    "多云或小雨天气开始农作、采集时，最终耗时减少12%。",
    phases=("task_prepare",),
)
def _overcast_worker(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming", "gathering") and _weather_is(context, "cloudy", "rain"):
        _multiply(context, "duration_multiplier", 0.88, stacking_group="duration")


@register_partner_trait(
    "herb_lore",
    "药草辨识",
    "采集池中存在药品时，保证本次任务至少获得1件药品。",
    phases=("output_draw",),
)
def _herb_lore(context: MutableMapping[str, Any]) -> None:
    if not _industry_is(context, "gathering"):
        return
    eligible_item_ids = [
        str(entry.get("item_id") or "")
        for entry in context.get("output_items") or ()
        if "medicine" in set(entry.get("tags") or ()) and entry.get("item_id")
    ]
    if eligible_item_ids:
        record_partner_trait_effect(
            context,
            "guarantee_tagged_output",
            params={
                "tag": "medicine",
                "count": 1,
                "eligible_item_ids": eligible_item_ids,
            },
            stacking_group="guaranteed_output",
        )


@register_partner_trait(
    "meticulous_farming",
    "精耕细作",
    "农作结算时，随机一件产物的品质重投一次并取较高值。",
    phases=("quality_roll",),
)
def _meticulous_farming(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming"):
        _reroll_one_quality(context)


@register_partner_trait(
    "warm_soil",
    "暖土催生",
    "晴朗天气开始农作时，最终耗时减少10%。",
    phases=("task_prepare",),
)
def _warm_soil(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming") and _weather_is(context, "sunny"):
        _multiply(context, "duration_multiplier", 0.90, stacking_group="duration")


@register_partner_trait(
    "swift_wind_work",
    "疾风行",
    "采集或采矿任务的最终耗时减少12%。",
    phases=("task_prepare",),
)
def _swift_wind_work(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "gathering", "mining"):
        _multiply(context, "duration_multiplier", 0.88, stacking_group="duration")


@register_partner_trait(
    "ore_heart",
    "矿心",
    "采矿任务的品质能力增加20。",
    phases=("task_prepare",),
)
def _ore_heart(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "mining"):
        _add(context, "quality_ability_bonus", 20, stacking_group="quality_ability")


@register_partner_trait(
    "falling_star",
    "星落",
    "农作或采集结算时，有20%概率将最低品质的一件产物提升一级。",
    phases=("result_finalize",),
)
def _falling_star(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming", "gathering"):
        _promote_lowest_quality(context, 0.20)


@register_partner_trait(
    "moonlit_selection",
    "清辉筛选",
    "采集、加工或普通钓获时，第一件可品质化产物重投品质并取较高值。",
    phases=("quality_roll", "instant_action"),
)
def _moonlit_selection(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "gathering", "crafting") or (
        _industry_is(context, "aquatic") and _action_is(context, "fishing_cast")
    ):
        _reroll_one_quality(context)


@register_partner_trait(
    "peaceful_cooking",
    "安作",
    "加工食物结算时，有30%概率将最低品质的一件成品提升一级。",
    phases=("result_finalize",),
)
def _peaceful_cooking(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "crafting") and _has_content_tag(context, "food"):
        _promote_lowest_quality(context, 0.30)


@register_partner_trait(
    "seasonal_rhythm",
    "循时而作",
    "晴天农作或雨天采集时，品质能力增加15。",
    phases=("task_prepare",),
)
def _seasonal_rhythm(context: MutableMapping[str, Any]) -> None:
    if (
        (_industry_is(context, "farming") and _weather_is(context, "sunny"))
        or (_industry_is(context, "gathering") and _weather_is(context, "rain"))
    ):
        _add(context, "quality_ability_bonus", 15, stacking_group="quality_ability")


@register_partner_trait(
    "fodder_artisan",
    "备粮匠",
    "加工饲料时固定额外产出1件。",
    phases=("task_prepare",),
)
def _fodder_artisan(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "crafting") and _has_content_tag(context, "fodder"):
        _add(context, "yield_bonus", 1, stacking_group="flat_yield")


def _livestock_segment(context: MutableMapping[str, Any]) -> bool:
    return _industry_is(context, "livestock") and _action_is(context, "livestock_segment")


def _livestock_birth(context: MutableMapping[str, Any]) -> bool:
    return _industry_is(context, "livestock") and str(context.get("action") or "") in {
        "livestock_breed",
        "livestock_incubate",
    }


@register_partner_trait(
    "herding_heart",
    "牧心",
    "驻场畜牧设施的品质能力增加20。",
    phases=("livestock_segment",),
)
def _herding_heart(context: MutableMapping[str, Any]) -> None:
    if _livestock_segment(context):
        _add(context, "quality_bonus", 20, stacking_group="livestock_quality")


@register_partner_trait(
    "generous_keep",
    "厚养",
    "驻场畜牧设施中亲密度已满的牲畜，特殊产出概率提高2个百分点。",
    phases=("livestock_segment",),
)
def _generous_keep(context: MutableMapping[str, Any]) -> None:
    if _livestock_segment(context):
        _add(context, "special_chance_bonus", 0.02, stacking_group="livestock_special")


@register_partner_trait(
    "full_larder",
    "囤仓",
    "驻场畜牧设施的饲料消耗减少20%，产出溢出上限提高1个周期。",
    phases=("livestock_segment",),
)
def _full_larder(context: MutableMapping[str, Any]) -> None:
    if _livestock_segment(context):
        _multiply(context, "feed_multiplier", 0.80, stacking_group="livestock_feed")
        _add(context, "overflow_bonus", 1, stacking_group="livestock_overflow")


@register_partner_trait(
    "unhurried",
    "慢条斯理",
    "驻场畜牧设施的产出溢出上限提高1个周期。",
    phases=("livestock_segment",),
)
def _unhurried(context: MutableMapping[str, Any]) -> None:
    if _livestock_segment(context):
        _add(context, "overflow_bonus", 1, stacking_group="livestock_overflow")


@register_partner_trait(
    "matchmaker",
    "相看",
    "在驻场畜牧设施配种或孵化时，子代的每条基因各重投一次并取较高值。",
    phases=("instant_action",),
)
def _matchmaker(context: MutableMapping[str, Any]) -> None:
    if _livestock_birth(context):
        _add(context, "gene_rerolls", 1, stacking_group="livestock_gene_reroll")


@register_partner_trait(
    "fine_combing",
    "细梳",
    "照料驻场畜牧设施的牲畜时亲密度额外增加4点；亲密度满时的品质系数额外提高0.1。",
    phases=("instant_action", "livestock_segment"),
)
def _fine_combing(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "livestock") and _action_is(context, "livestock_care"):
        _add(context, "affection_per_care_bonus", 4, stacking_group="livestock_affection")
    elif _livestock_segment(context):
        _add(context, "affection_quality_bonus", 0.001, stacking_group="livestock_affection_quality")


def _exploration_check(context: MutableMapping[str, Any]) -> bool:
    return _industry_is(context, "exploration") and bool(context.get("check_attribute"))


def _exploration_actor(context: MutableMapping[str, Any]) -> bool:
    return str(context.get("source_partner_id") or "") in set(context.get("check_actor_partner_ids") or ())


def _exploration_once_available(context: MutableMapping[str, Any], key: str) -> bool:
    return int((context.get("trait_usage") or {}).get(key, 0)) <= 0


def _queue_exploration_usage(context: MutableMapping[str, Any], key: str) -> None:
    pending = context.setdefault("trait_usage_consumptions", [])
    if key not in pending:
        pending.append(key)


@register_partner_trait(
    "thunderwing_vanguard",
    "雷翼先导",
    "每次探索中，队伍第一次敏捷检定获得优势。",
    phases=("exploration_event",),
)
def _thunderwing_vanguard(context: MutableMapping[str, Any]) -> None:
    key = "thunderwing_vanguard:agility_advantage"
    if not _exploration_check(context) or context.get("check_attribute") != "agility":
        return
    if context.get("base_dice_mode") == "advantage":
        return
    if not _exploration_once_available(context, key):
        return
    _add(context, "dice_adjustment", 1, effect="grant_advantage", stacking_group="exploration_dice")
    _queue_exploration_usage(context, key)


@register_partner_trait(
    "snowtrace_trick",
    "雪痕戏法",
    "每次探索限一次：古剑喵执行的普通失败检定可以重投，必须接受新结果。",
    phases=("exploration_event",),
)
def _snowtrace_trick(context: MutableMapping[str, Any]) -> None:
    key = "snowtrace_trick:failure_reroll"
    if not _exploration_check(context) or not _exploration_actor(context):
        return
    if _exploration_once_available(context, key):
        context["failure_reroll_usage_key"] = key
        record_partner_trait_effect(context, "allow_failure_reroll")


@register_partner_trait(
    "wilderness_veteran",
    "荒野老手",
    "红凯执行力量或智力检定时调整值+1；普通失败的附加体力消耗减少1。",
    phases=("exploration_event",),
)
def _wilderness_veteran(context: MutableMapping[str, Any]) -> None:
    if not _exploration_check(context) or not _exploration_actor(context):
        return
    if context.get("check_attribute") not in {"strength", "intelligence"}:
        return
    _add(context, "check_bonus", 1, stacking_group="exploration_check_bonus")
    _add(context, "ordinary_failure_stamina_reduction", 1, stacking_group="exploration_failure_stamina")


@register_partner_trait(
    "royal_rider_command",
    "王骑号令",
    "全体检定时蕾蕾的调整值计算两次；担任领队时，每次探索第一次全体检定获得优势。",
    phases=("exploration_event",),
)
def _royal_rider_command(context: MutableMapping[str, Any]) -> None:
    if not _exploration_check(context) or context.get("check_mode") != "sum":
        return
    source_partner_id = str(context.get("source_partner_id") or "")
    contribution = int((context.get("check_base_modifiers") or {}).get(source_partner_id, 0))
    _add(context, "check_bonus", contribution, effect="double_group_contribution", stacking_group="exploration_group_bonus")
    key = "royal_rider_command:group_advantage"
    if (
        source_partner_id == context.get("leader_partner_id")
        and context.get("base_dice_mode") != "advantage"
        and _exploration_once_available(context, key)
    ):
        _add(context, "dice_adjustment", 1, effect="grant_advantage", stacking_group="exploration_dice")
        _queue_exploration_usage(context, key)


@register_partner_trait(
    "iceflame_breach",
    "冰焰破障",
    "雾剑执行力量检定时调整值+1。",
    phases=("exploration_event",),
)
def _iceflame_breach(context: MutableMapping[str, Any]) -> None:
    if _exploration_check(context) and _exploration_actor(context) and context.get("check_attribute") == "strength":
        _add(context, "check_bonus", 1, stacking_group="exploration_check_bonus")


@register_partner_trait(
    "head_on",
    "硬碰硬",
    "小哈执行力量检定普通失败时免除附加体力消耗；大失败时减少2点附加消耗。",
    phases=("exploration_event",),
)
def _head_on(context: MutableMapping[str, Any]) -> None:
    if not (_exploration_check(context) and _exploration_actor(context) and context.get("check_attribute") == "strength"):
        return
    context["ordinary_failure_stamina_reduction"] = 99
    context["critical_failure_stamina_reduction"] = max(
        int(context.get("critical_failure_stamina_reduction", 0)),
        2,
    )
    record_partner_trait_effect(context, "reduce_failure_stamina", params={"ordinary": 99, "critical": 2})


@register_partner_trait(
    "wildfire_instinct",
    "燎原本能",
    "灼焱执行力量或敏捷检定时，自然19也视为大成功。",
    phases=("exploration_event",),
)
def _wildfire_instinct(context: MutableMapping[str, Any]) -> None:
    if not _exploration_check(context) or not _exploration_actor(context):
        return
    if context.get("check_attribute") not in {"strength", "agility"}:
        return
    context["critical_success_min"] = min(int(context.get("critical_success_min", 20)), 19)
    record_partner_trait_effect(context, "expand_critical_success", value=19)


# ---------------------------------------------------------------------------
# 第三批伙伴特性
# ---------------------------------------------------------------------------


@register_partner_trait(
    "windborne_sowing",
    "乘风播绒",
    "山风天气开始农作任务时，固定额外产出1件，且最终耗时减少10%。",
    phases=("task_prepare",),
)
def _windborne_sowing(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming") and _weather_is(context, "windy"):
        _add(context, "yield_bonus", 1, stacking_group="flat_yield")
        _multiply(context, "duration_multiplier", 0.90, stacking_group="duration")


@register_partner_trait(
    "primordial_return",
    "万物归元",
    "加工任务结算时，有35%概率把本次消耗的其中一种原料全额返还。",
    phases=("result_finalize",),
)
def _primordial_return(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "crafting"):
        record_partner_trait_effect(
            context,
            "refund_consumed_input",
            params={"chance": 0.35, "count": 1},
            stacking_group="input_refund",
        )


@register_partner_trait(
    "attendant_at_hand",
    "随侍在侧",
    "驻场鱼塘的品质能力增加15；农作与加工任务的产量效率提高10%。",
    phases=("asset_prepare", "task_prepare"),
)
def _attendant_at_hand(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "aquatic") and _action_is(context, "pond_segment"):
        _add(context, "quality_bonus", 15, stacking_group="pond_quality")
    elif _industry_is(context, "farming", "crafting"):
        _multiply(context, "yield_multiplier", 1.10, stacking_group="yield_efficiency")


@register_partner_trait(
    "frost_grooming",
    "凛霜细养",
    "驻场畜牧设施的品质能力增加12；照料牲畜时亲密度额外增加2点。",
    phases=("livestock_segment", "instant_action"),
)
def _frost_grooming(context: MutableMapping[str, Any]) -> None:
    if _livestock_segment(context):
        _add(context, "quality_bonus", 12, stacking_group="livestock_quality")
    elif _industry_is(context, "livestock") and _action_is(context, "livestock_care"):
        _add(context, "affection_per_care_bonus", 2, stacking_group="livestock_affection")


@register_partner_trait(
    "warm_broth_ready",
    "热汤常备",
    "加工食物时固定额外产出1件。",
    phases=("task_prepare",),
)
def _warm_broth_ready(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "crafting") and _has_content_tag(context, "food"):
        _add(context, "yield_bonus", 1, stacking_group="flat_yield")


@register_partner_trait(
    "veinbreak_stroke",
    "一刀断脉",
    "采矿结算时，随机一件产物的品质重投一次并取较高值。",
    phases=("quality_roll",),
)
def _veinbreak_stroke(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "mining"):
        _reroll_one_quality(context)


@register_partner_trait(
    "appraising_scythe",
    "甄别之镰",
    "采集、采矿或加工结算时，有25%概率将最低品质的一件产物提升一级。",
    phases=("result_finalize",),
)
def _appraising_scythe(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "gathering", "mining", "crafting"):
        _promote_lowest_quality(context, 0.25)


@register_partner_trait(
    "rainbow_pickings",
    "虹彩拾遗",
    "采集任务的品质能力增加15。",
    phases=("task_prepare",),
)
def _rainbow_pickings(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "gathering"):
        _add(context, "quality_ability_bonus", 15, stacking_group="quality_ability")


@register_partner_trait(
    "ripple_play",
    "戏水涟漪",
    "陪钓时额外抽取1次产出。",
    phases=("instant_action",),
)
def _ripple_play(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "aquatic") and _action_is(context, "fishing_cast"):
        _add(context, "draw_bonus", 1, stacking_group="draw_count")


@register_partner_trait(
    "azure_smelt",
    "蓝焰熔炼",
    "加工与采矿任务的品质能力提高15%；加工结算时，品质最低的一件成品保底为「良品」，已有良品及以上品质不变。",
    phases=("task_prepare", "result_finalize"),
)
def _azure_smelt(context: MutableMapping[str, Any]) -> None:
    if str(context.get("phase") or "") == "task_prepare":
        if _industry_is(context, "crafting", "mining"):
            _multiply(context, "quality_ability_multiplier", 1.15, stacking_group="quality_ability")
    elif _industry_is(context, "crafting"):
        record_partner_trait_effect(
            context,
            "promote_lowest_quality",
            params={"chance": 1.0, "levels": 0, "minimum": 2},
            stacking_group="quality_promotion",
        )


@register_partner_trait(
    "azure_flame_undying",
    "苍炎不灭",
    "深入越远，铠甲里的苍炎烧得越旺：探索每深入一层，本次行动的奖励数量提高5%。",
    phases=("exploration_event",),
)
def _azure_flame_undying(context: MutableMapping[str, Any]) -> None:
    if not _industry_is(context, "exploration"):
        return
    layer = max(1, int(context.get("depth", 0)) + 1)
    _multiply(
        context,
        "reward_quantity_multiplier",
        1 + 0.05 * layer,
        stacking_group="exploration_reward_quantity",
    )


@register_partner_trait(
    "pinpoint_shot",
    "定点狙击",
    "每次探索限一次：原慧琴执行的智力或敏捷检定若失败，改判为普通成功。",
    phases=("exploration_event",),
)
def _pinpoint_shot(context: MutableMapping[str, Any]) -> None:
    key = "pinpoint_shot:failure_rescue"
    if not _exploration_check(context) or not _exploration_actor(context):
        return
    if context.get("check_attribute") not in {"intelligence", "agility"}:
        return
    if _exploration_once_available(context, key):
        context["failure_rescue_usage_key"] = key
        record_partner_trait_effect(context, "allow_failure_rescue")


@register_partner_trait(
    "orchard_tending",
    "果树栽培",
    "种植树果作物时，最终耗时减少20%，且固定额外收成1件。",
    phases=("task_prepare",),
)
def _orchard_tending(context: MutableMapping[str, Any]) -> None:
    if _industry_is(context, "farming") and _has_content_tag(context, "tree_fruit"):
        _multiply(context, "duration_multiplier", 0.80, stacking_group="duration")
        _add(context, "yield_bonus", 1, stacking_group="flat_yield")


@register_partner_trait(
    "full_health_advantage",
    "全盛之势",
    "战斗中进行攻击检定时，若自身生命值高于90%，则获得优势骰。",
    phases=("delve_attack",),
)
def _full_health_advantage(context: MutableMapping[str, Any]) -> None:
    current_hp = int(context.get("current_hp", 0))
    max_hp = int(context.get("max_hp", 0))
    if max_hp > 0 and current_hp * 10 > max_hp * 9:
        _add(context, "dice_adjustment", 1, effect="grant_advantage", stacking_group="delve_attack_dice")


@register_partner_trait(
    "strength_weapon_tradeoff",
    "力破千钧",
    "自身使用力量型武器时，攻击检定加值-5，但造成的伤害+10。",
    phases=("delve_attack",),
)
def _strength_weapon_tradeoff(context: MutableMapping[str, Any]) -> None:
    if not context.get("weapon_item_id") or context.get("attack_attribute") != "strength":
        return
    _add(context, "attack_bonus", -5, stacking_group="delve_attack_bonus")
    _add(context, "damage_bonus", 10, stacking_group="delve_damage_bonus")


@register_partner_trait(
    "star_guidance", "循星引航",
    "参与出海时，全船事件检定＋2；首次失败以优势骰重掷，采用新结果，救场成功额外抽取一次物产。每趟一次。",
    phases=("sailing_prepare",),
)
def _star_guidance(context):
    if context.get("star_guidance_partner_id"):
        return
    context["event_check_bonus"] = int(context.get("event_check_bonus", 0)) + 2
    context["star_guidance_partner_id"] = context["source_partner_id"]
    record_partner_trait_effect(context, "star_guidance", value=2)


@register_partner_trait(
    "stage_presence",
    "舞台气场",
    "顾祇SP探索担任领队时，全队探索检定调整值+1；敏捷检定额外再+1。偶像站上 C 位，聚光灯就会照过来。",
    phases=("exploration_event",),
)
def _stage_presence(context: MutableMapping[str, Any]) -> None:
    """舞台气场：只有站在一号位（领队）时才点亮，敏捷再吃一档。

    「在队」这件事本身不用在这里判断：service 的特性阶段按 run.partner_ids
    逐个伙伴执行（_execute_partner_trait_phase），她不在队伍里 handler 根本不会
    被调用。这里额外管的是「队内位置」——只有 source_partner_id 恰好等于
    run.leader_partner_id（前端队伍选择器的一号位「领队」）时才生效，
    和上游「王骑号令」判断领队的写法一致。
    """

    if not _exploration_check(context):
        return
    leader_partner_id = str(context.get("leader_partner_id") or "")
    if not leader_partner_id or leader_partner_id != str(context.get("source_partner_id") or ""):
        return
    _add(context, "check_bonus", 1, stacking_group="exploration_check_bonus")
    if context.get("check_attribute") == "agility":
        _add(
            context,
            "check_bonus",
            1,
            effect="stage_presence_agility_edge",
            stacking_group="exploration_check_bonus",
        )


@register_partner_trait(
    "summer_mood",
    "夏日心情",
    "采集、垂钓与鱼塘的品质能力+25；晴朗天气下开始采集任务时，额外抽取1次产出。",
    phases=("task_prepare", "output_draw", "instant_action", "asset_prepare"),
)
def _summer_mood(context: MutableMapping[str, Any]) -> None:
    phase = str(context.get("phase") or "")
    if phase == "task_prepare" and _industry_is(context, "gathering"):
        _add(context, "quality_ability_bonus", 25, stacking_group="quality_ability")
    elif phase == "instant_action" and _industry_is(context, "aquatic"):
        _add(context, "quality_ability_bonus", 25, stacking_group="quality_ability")
    elif phase == "asset_prepare" and _industry_is(context, "aquatic") and _action_is(context, "pond_segment"):
        _add(context, "quality_bonus", 25, stacking_group="pond_quality")
    elif phase == "output_draw" and _industry_is(context, "gathering") and _weather_is(context, "sunny"):
        _add(context, "draw_bonus", 1, stacking_group="draw_count")


@register_partner_trait(
    "bountiful_stable", "丰厩之赐",
    "驻场畜牧时，普通产物数量提高25%，不足一件的部分累计至后续周期。",
    phases=("livestock_segment",), min_breakthrough=1,
)
def _bountiful_stable(context):
    if _industry_is(context, "livestock"):
        _multiply(context, "normal_yield_multiplier", 1.25, stacking_group="livestock_yield")


@register_partner_trait(
    "wildland_bounty", "丰野馈赠",
    "采集物产抽取次数增加30%，额外次数向下取整，至少增加1次。",
    phases=("output_draw",), min_breakthrough=1,
)
def _wildland_bounty(context):
    if _industry_is(context, "gathering"):
        context["bonus_draw_percent"] = int(context.get("bonus_draw_percent", 0)) + 30
        record_partner_trait_effect(context, "bonus_draw_percent", value=30)


@register_partner_trait(
    "silent_snowstep", "踏雪无痕",
    "本人执行非全体的敏捷检定时，调整值＋2。",
    phases=("exploration_event",), min_breakthrough=1,
)
def _silent_snowstep(context):
    if (_exploration_check(context) and _exploration_actor(context)
            and context.get("check_mode") != "sum" and context.get("check_attribute") == "agility"):
        _add(context, "check_bonus", 2, stacking_group="exploration_check_bonus")


@register_partner_trait(
    "seasoned_husbandry", "熟稔农时",
    "农作最终耗时减少8%，可突破最低耗时限制。",
    phases=("task_prepare",), min_breakthrough=1,
)
def _seasoned_husbandry(context):
    if _industry_is(context, "farming"):
        _multiply(context, "duration_multiplier", 0.92, stacking_group="duration")


@register_partner_trait(
    "flame_chain", "烈焰连袭",
    "每场战斗首次命中后，立即追加一次攻击；若目标已倒下，则攻击下一名存活敌人。",
    phases=("delve_attack",), min_breakthrough=1,
)
def _flame_chain(context):
    context["first_hit_followup"] = True
