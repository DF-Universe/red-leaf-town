from __future__ import annotations

import json

import pytest

from red_leaf_town.partner_content import load_partner_catalog
from red_leaf_town.partner_traits import execute_partner_traits, partner_trait_catalog


PARTNER_TRAITS: dict[str, str | tuple[str, ...]] = {
    "ai_xinyu": "flow_treasure",
    "aiweier": "fodder_fields",
    "aorui_jin": "golden_line",
    "babi": "steady_craft",
    "banniang": "dream_forage",
    "bufeng_zhuoying": "ore_chaser",
    "daian_zeer": "rain_garden",
    "fein": "varied_forage",
    "guidengdeng": "ghostfire_refinement",
    "gujian_bu": "deep_soil_edge",
    "guqi": "listen_to_rock",
    "guyu_yu": "ancient_formula",
    "hei_yuchuan": "overcast_worker",
    "lengyue": "fine_combing",
    "luo_nali": ("herb_lore", "wildland_bounty"),
    "manlong": "unhurried",
    "manlong_2": "full_larder",
    "mami": ("meticulous_farming", "seasoned_husbandry"),
    "nuanyu": "warm_soil",
    "sunfeng_liya": "swift_wind_work",
    "xiang_hanyang": "ore_heart",
    "xiang_hanyuan": "herding_heart",
    "xiao_xingyun": "falling_star",
    "xixi": "matchmaker",
    "xiyue_kanna": "moonlit_selection",
    "xuanyuan": "generous_keep",
    "ye_huanan": "peaceful_cooking",
    "ye_lvsu": "seasonal_rhythm",
    "ze_feiyang": "fodder_artisan",
    "aishen": "thunderwing_vanguard",
    "gujian_miao": ("snowtrace_trick", "silent_snowstep"),
    "hongkai": "wilderness_veteran",
    "leilei": ("royal_rider_command", "bountiful_stable"),
    "wujian": "iceflame_breach",
    "xiaoha": "head_on",
    "zhuoyan": ("wildfire_instinct", "flame_chain"),
    "wei_jiang": "windborne_sowing",
    "bai_tiantian": "primordial_return",
    "ya_ziweier": "azure_flame_undying",
    "ai_liqi": "attendant_at_hand",
    "bai_lingxue": "frost_grooming",
    "fumeng": "warm_broth_ready",
    "jia_gula": "veinbreak_stroke",
    "zhake": "appraising_scythe",
    "hongzhijian": "rainbow_pickings",
    "shuiling": "ripple_play",
    "nuanyu_2": "azure_smelt",
    "yuan_huiqin": "pinpoint_shot",
    "bai_li": "orchard_tending",
    "bai_lin": ("full_health_advantage", "strength_weapon_tradeoff"),
}

PENDING_PARTNERS: set[str] = set()


def trait_context(phase: str, **overrides):
    context = {
        "phase": phase,
        "source_partner_id": "test_partner",
        "industry": "farming",
        "action": "",
        "content_tags": [],
        "output_items": [],
        "world": {"weather": {"id": "sunny"}},
        "duration_multiplier": 1.0,
        "quality_ability_bonus": 0.0,
        "quality_ability_multiplier": 1.0,
        "yield_multiplier": 1.0,
        "yield_bonus": 0,
        "draw_bonus": 0,
        "rare_weight_multiplier": 1.0,
        "feed_multiplier": 1.0,
        "quality_bonus": 0.0,
        "check_bonus": 0,
        "ordinary_failure_stamina_reduction": 0,
        "critical_failure_stamina_reduction": 0,
        "critical_success_min": 20,
        "overflow_bonus": 0,
        "special_chance_bonus": 0.0,
        "affection_quality_bonus": 0.0,
        "affection_per_care_bonus": 0,
        "gene_rerolls": 0,
        "reward_quantity_multiplier": 1.0,
        "current_hp": 100,
        "max_hp": 100,
        "weapon_item_id": "test_strength_weapon",
        "attack_attribute": "strength",
        "dice_adjustment": 0,
        "attack_bonus": 0,
        "damage_bonus": 0,
        "applied_effects": [],
    }
    context.update(overrides)
    return context


TRAIT_CASES = [
    ("flow_treasure", "instant_action", {"industry": "aquatic", "action": "fishing_cast"}, "rare_weight_multiplier", "increase"),
    ("fodder_fields", "task_prepare", {"industry": "farming", "content_tags": ["fodder"]}, "yield_bonus", "increase"),
    ("golden_line", "instant_action", {"industry": "aquatic", "action": "fishing_cast"}, "rare_weight_multiplier", "increase"),
    ("steady_craft", "task_prepare", {"industry": "crafting"}, "quality_ability_bonus", "increase"),
    ("dream_forage", "output_draw", {"industry": "gathering"}, "draw_bonus", "increase"),
    ("ore_chaser", "task_prepare", {"industry": "mining"}, "yield_bonus", "increase"),
    ("rain_garden", "task_prepare", {"industry": "farming", "world": {"weather": {"id": "rain"}}}, "quality_ability_bonus", "increase"),
    ("varied_forage", "output_draw", {"industry": "gathering"}, None, "effect"),
    ("ghostfire_refinement", "result_finalize", {"industry": "crafting"}, None, "effect"),
    ("deep_soil_edge", "task_prepare", {"industry": "farming"}, "quality_ability_multiplier", "increase"),
    ("listen_to_rock", "task_prepare", {"industry": "mining"}, "quality_ability_multiplier", "increase"),
    ("ancient_formula", "quality_roll", {"industry": "crafting", "content_tags": ["medicine"]}, None, "effect"),
    ("overcast_worker", "task_prepare", {"industry": "gathering", "world": {"weather": {"id": "cloudy"}}}, "duration_multiplier", "decrease"),
    ("herb_lore", "output_draw", {"industry": "gathering", "output_items": [{"item_id": "autumn_herb", "tags": ["forage", "medicine"]}]}, None, "effect"),
    ("meticulous_farming", "quality_roll", {"industry": "farming"}, None, "effect"),
    ("warm_soil", "task_prepare", {"industry": "farming", "world": {"weather": {"id": "sunny"}}}, "duration_multiplier", "decrease"),
    ("swift_wind_work", "task_prepare", {"industry": "mining"}, "duration_multiplier", "decrease"),
    ("ore_heart", "task_prepare", {"industry": "mining"}, "quality_ability_bonus", "increase"),
    ("falling_star", "result_finalize", {"industry": "gathering"}, None, "effect"),
    ("moonlit_selection", "quality_roll", {"industry": "crafting"}, None, "effect"),
    ("peaceful_cooking", "result_finalize", {"industry": "crafting", "content_tags": ["food"]}, None, "effect"),
    ("seasonal_rhythm", "task_prepare", {"industry": "gathering", "world": {"weather": {"id": "rain"}}}, "quality_ability_bonus", "increase"),
    ("fodder_artisan", "task_prepare", {"industry": "crafting", "content_tags": ["fodder"]}, "yield_bonus", "increase"),
    ("herding_heart", "livestock_segment", {"industry": "livestock", "action": "livestock_segment"}, "quality_bonus", "increase"),
    ("generous_keep", "livestock_segment", {"industry": "livestock", "action": "livestock_segment"}, "special_chance_bonus", "increase"),
    ("full_larder", "livestock_segment", {"industry": "livestock", "action": "livestock_segment"}, "feed_multiplier", "decrease"),
    ("unhurried", "livestock_segment", {"industry": "livestock", "action": "livestock_segment"}, "overflow_bonus", "increase"),
    ("matchmaker", "instant_action", {"industry": "livestock", "action": "livestock_breed"}, "gene_rerolls", "increase"),
    ("fine_combing", "instant_action", {"industry": "livestock", "action": "livestock_care"}, "affection_per_care_bonus", "increase"),
    ("thunderwing_vanguard", "exploration_event", {"industry": "exploration", "check_attribute": "agility", "base_dice_mode": "normal", "trait_usage": {}, "dice_adjustment": 0}, "dice_adjustment", "increase"),
    ("snowtrace_trick", "exploration_event", {"industry": "exploration", "check_attribute": "agility", "check_actor_partner_ids": ["gujian_miao"], "source_partner_id": "gujian_miao", "trait_usage": {}}, None, "effect"),
    ("wilderness_veteran", "exploration_event", {"industry": "exploration", "check_attribute": "strength", "check_actor_partner_ids": ["hongkai"], "source_partner_id": "hongkai"}, "check_bonus", "increase"),
    ("royal_rider_command", "exploration_event", {"industry": "exploration", "check_attribute": "strength", "check_mode": "sum", "check_base_modifiers": {"leilei": 2}, "check_actor_partner_ids": ["leilei"], "source_partner_id": "leilei", "leader_partner_id": "leilei", "base_dice_mode": "normal", "trait_usage": {}, "dice_adjustment": 0}, "check_bonus", "increase"),
    ("iceflame_breach", "exploration_event", {"industry": "exploration", "check_attribute": "strength", "check_actor_partner_ids": ["wujian"], "source_partner_id": "wujian"}, "check_bonus", "increase"),
    ("head_on", "exploration_event", {"industry": "exploration", "check_attribute": "strength", "check_actor_partner_ids": ["xiaoha"], "source_partner_id": "xiaoha"}, "ordinary_failure_stamina_reduction", "increase"),
    ("wildfire_instinct", "exploration_event", {"industry": "exploration", "check_attribute": "agility", "check_actor_partner_ids": ["zhuoyan"], "source_partner_id": "zhuoyan"}, "critical_success_min", "decrease"),
    ("windborne_sowing", "task_prepare", {"industry": "farming", "world": {"weather": {"id": "windy"}}}, "yield_bonus", "increase"),
    ("primordial_return", "result_finalize", {"industry": "crafting"}, None, "effect"),
    ("attendant_at_hand", "asset_prepare", {"industry": "aquatic", "action": "pond_segment"}, "quality_bonus", "increase"),
    ("frost_grooming", "livestock_segment", {"industry": "livestock", "action": "livestock_segment"}, "quality_bonus", "increase"),
    ("warm_broth_ready", "task_prepare", {"industry": "crafting", "content_tags": ["food"]}, "yield_bonus", "increase"),
    ("veinbreak_stroke", "quality_roll", {"industry": "mining"}, None, "effect"),
    ("appraising_scythe", "result_finalize", {"industry": "mining"}, None, "effect"),
    ("rainbow_pickings", "task_prepare", {"industry": "gathering"}, "quality_ability_bonus", "increase"),
    ("ripple_play", "instant_action", {"industry": "aquatic", "action": "fishing_cast"}, "draw_bonus", "increase"),
    ("azure_smelt", "task_prepare", {"industry": "crafting"}, "quality_ability_multiplier", "increase"),
    ("azure_flame_undying", "exploration_event", {"industry": "exploration", "depth": 3}, "reward_quantity_multiplier", "increase"),
    ("pinpoint_shot", "exploration_event", {"industry": "exploration", "check_attribute": "intelligence", "check_actor_partner_ids": ["yuan_huiqin"], "source_partner_id": "yuan_huiqin", "trait_usage": {}}, None, "effect"),
    ("orchard_tending", "task_prepare", {"industry": "farming", "content_tags": ["crop", "food", "tree_fruit"]}, "duration_multiplier", "decrease"),
    ("full_health_advantage", "delve_attack", {}, "dice_adjustment", "increase"),
    ("strength_weapon_tradeoff", "delve_attack", {}, "attack_bonus", "decrease"),
]

SECONDARY_TRAIT_CASES = [
    ("moonlit_selection", "instant_action", {"industry": "aquatic", "action": "fishing_cast"}, None, "effect"),
    ("matchmaker", "instant_action", {"industry": "livestock", "action": "livestock_incubate"}, None, "effect"),
    ("fine_combing", "livestock_segment", {"industry": "livestock", "action": "livestock_segment"}, None, "effect"),
    ("full_larder", "livestock_segment", {"industry": "livestock", "action": "livestock_segment"}, "overflow_bonus", "increase"),
    ("attendant_at_hand", "task_prepare", {"industry": "crafting"}, "yield_multiplier", "increase"),
    ("frost_grooming", "instant_action", {"industry": "livestock", "action": "livestock_care"}, "affection_per_care_bonus", "increase"),
    ("azure_smelt", "result_finalize", {"industry": "crafting"}, None, "effect"),
    ("orchard_tending", "task_prepare", {"industry": "farming", "content_tags": ["crop", "food", "tree_fruit"]}, "yield_bonus", "increase"),
    ("strength_weapon_tradeoff", "delve_attack", {}, "damage_bonus", "increase"),
]


def test_only_partners_with_an_implemented_primary_industry_have_traits():
    catalog = load_partner_catalog()
    definitions = {entry.code: entry for entry in partner_trait_catalog()}

    for partner_id, trait_codes in PARTNER_TRAITS.items():
        expected_codes = [trait_codes] if isinstance(trait_codes, str) else list(trait_codes)
        assert catalog.partner_map[partner_id].trait_codes == expected_codes
        assert all(definitions[code].implemented is True for code in expected_codes)
    assert {partner_id for partner_id, partner in catalog.partner_map.items() if not partner.trait_codes} == PENDING_PARTNERS


def test_no_two_bound_traits_have_identical_behavior():
    scenarios = [(phase, overrides) for _, phase, overrides, _, _ in [*TRAIT_CASES, *SECONDARY_TRAIT_CASES]]
    signatures: dict[tuple[str, ...], list[str]] = {}
    bound_codes = {
        code
        for trait_codes in PARTNER_TRAITS.values()
        for code in ([trait_codes] if isinstance(trait_codes, str) else trait_codes)
    }
    for code in bound_codes:
        observations = []
        for phase, overrides in scenarios:
            context = trait_context(phase, **overrides)
            execute_partner_traits([code], context)
            effects = [
                {
                    key: value
                    for key, value in effect.items()
                    if key not in {"trait_code", "source_partner_id"}
                }
                for effect in context["applied_effects"]
            ]
            observations.append(json.dumps(effects, ensure_ascii=False, sort_keys=True))
        signatures.setdefault(tuple(observations), []).append(code)

    assert [codes for codes in signatures.values() if len(codes) > 1] == []


@pytest.mark.parametrize(("code", "phase", "overrides", "field", "direction"), TRAIT_CASES)
def test_each_partner_trait_reaches_its_effect(
    code: str,
    phase: str,
    overrides: dict,
    field: str | None,
    direction: str,
):
    context = trait_context(phase, **overrides)
    before = context.get(field) if field else None

    assert execute_partner_traits([code], context) == [code]
    assert context["applied_effects"]
    assert context["applied_effects"][0]["trait_code"] == code
    assert context["applied_effects"][0]["phase"] == phase
    if direction == "increase":
        assert context[field] > before
    elif direction == "decrease":
        assert context[field] < before


@pytest.mark.parametrize(("code", "phase", "overrides", "field", "direction"), SECONDARY_TRAIT_CASES)
def test_multi_phase_traits_reach_their_secondary_path(
    code: str,
    phase: str,
    overrides: dict,
    field: str | None,
    direction: str,
):
    context = trait_context(phase, **overrides)
    before = context.get(field) if field else None

    execute_partner_traits([code], context)

    assert context["applied_effects"]
    if direction == "decrease":
        assert context[field] < before


ONCE_PER_EXPEDITION_TRAITS = [
    ("pinpoint_shot", "pinpoint_shot:failure_rescue", {
        "check_attribute": "intelligence",
        "check_actor_partner_ids": ["yuan_huiqin"],
        "source_partner_id": "yuan_huiqin",
    }),
    ("snowtrace_trick", "snowtrace_trick:failure_reroll", {
        "check_attribute": "agility",
        "check_actor_partner_ids": ["gujian_miao"],
        "source_partner_id": "gujian_miao",
    }),
]


@pytest.mark.parametrize(("code", "usage_key", "overrides"), ONCE_PER_EXPEDITION_TRAITS)
def test_once_per_expedition_traits_stop_arming_after_their_usage_is_recorded(
    code: str,
    usage_key: str,
    overrides: dict,
):
    armed = trait_context("exploration_event", industry="exploration", trait_usage={}, **overrides)
    execute_partner_traits([code], armed)
    assert armed["applied_effects"]

    spent = trait_context("exploration_event", industry="exploration", trait_usage={usage_key: 1}, **overrides)
    execute_partner_traits([code], spent)
    assert spent["applied_effects"] == []
