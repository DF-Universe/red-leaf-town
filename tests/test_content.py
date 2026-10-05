from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from red_leaf_town.content import GameContent, load_content


def test_default_content_is_consistent():
    content = load_content()
    assert content.game.title == "红叶镇物语"
    assert content.level_for_xp(0).level == 1
    assert content.level_for_xp(20).level == 2
    assert content.crop_map["carrot"].seed_item_id == "carrot_seed"
    assert content.crop_map["carrot"].time_difficulty > 0
    assert content.industries["farming"].collaborator_slots == 1
    assert content.item_map["carrot"].sell_price > 0
    assert [grade.name for grade in content.quality.grades] == ["普通", "良品", "上品", "臻品", "奇迹"]
    assert content.crop_map["carrot"].quality.thresholds == sorted(content.crop_map["carrot"].quality.thresholds)
    assert content.industries["gathering"].partner_capacity == 1
    assert content.gathering_task_map["collect_maple_wood"].outputs[0].item_id == "maple_wood"
    assert content.item_map["maple_wood"].has_quality is True
    assert content.talent_map["gathering_roster_1"].partner_capacity_bonus == 1
    assert content.recipe_map["saw_maple_plank"].unlock_condition.hook == "player_level"
    assert content.industries["mining"].collaborator_slots == 1
    assert content.mining_site_map["copper_foothill"].min_level == 2
    assert content.mining_task_map["mine_red_copper"].produce_item_id == "red_copper_ore"
    assert content.item_map["red_copper_ore"].has_quality is True
    assert content.world.season_id == "autumn"
    assert [entry.id for entry in content.world.weather_cycle] == ["sunny", "cloudy", "rain", "windy"]
    assert content.item_map["red_copper_ore"].has_tag("mineral")
    assert content.item_map["stream_fish"].has_tag("fish")
    assert content.recipe_map["mill_flour"].has_tag("food")


def test_unknown_and_duplicate_content_tags_are_rejected():
    payload = load_content().model_dump()
    payload["items"][0]["tags"] = ["crop_seed", "crop_seed"]
    with pytest.raises(ValidationError, match="tags must be unique"):
        GameContent.model_validate(payload)

    payload = load_content().model_dump()
    payload["recipes"][0]["tags"] = ["woodworkingg"]
    with pytest.raises(ValidationError, match="unknown tags"):
        GameContent.model_validate(payload)


def test_gathering_tasks_have_frozen_weighted_pools_and_eight_hour_floor():
    content = load_content()
    forest = content.gathering_task_map["collect_maple_wood"]
    meadow = content.gathering_task_map["collect_autumn_herb"]

    assert forest.stamina_cost == meadow.stamina_cost == 0
    assert forest.minimum_duration_seconds == meadow.minimum_duration_seconds == 8 * 3600
    assert [output.item_id for output in forest.outputs] == [
        "maple_wood",
        "woodland_mushroom",
        "maple_resin",
        "amber_beeswax",
    ]
    assert [output.item_id for output in meadow.outputs] == [
        "tough_fodder",
        "autumn_herb",
        "morning_dew_flower",
        "silver_star_moss",
    ]
    assert [(output.weight, output.quantity_min, output.quantity_max) for output in forest.outputs] == [
        (50, 1, 2),
        (30, 1, 1),
        (15, 1, 1),
        (5, 1, 1),
    ]
    assert [(output.weight, output.quantity_min, output.quantity_max) for output in meadow.outputs] == [
        (50, 1, 2),
        (30, 1, 1),
        (15, 1, 1),
        (5, 1, 1),
    ]
    assert (forest.draws.base_draws, forest.draws.ability_bonus, forest.draws.difficulty) == (6, 1.5, 120)
    assert (meadow.draws.base_draws, meadow.draws.ability_bonus, meadow.draws.difficulty) == (6, 1.5, 120)
    assert {output.item_id: content.item_map[output.item_id].sell_price for output in [*forest.outputs, *meadow.outputs]} == {
        "maple_wood": 6,
        "woodland_mushroom": 7,
        "maple_resin": 10,
        "amber_beeswax": 16,
        "tough_fodder": 5,
        "autumn_herb": 10,
        "morning_dew_flower": 14,
        "silver_star_moss": 30,
    }
    assert forest.quality.thresholds == [25, 55, 90, 210]
    assert meadow.quality.thresholds == [35, 65, 100, 230]
    assert forest.headline_output.item_id == "maple_wood"
    assert meadow.headline_output.item_id == "tough_fodder"
    assert all(content.item_map[output.item_id].has_quality for task in (forest, meadow) for output in task.outputs)


def test_gathering_pool_requires_positive_weights_and_unique_items():
    payload = load_content().model_dump()
    payload["gathering_tasks"][0]["outputs"][0]["weight"] = 0
    with pytest.raises(ValidationError, match="greater than 0"):
        GameContent.model_validate(payload)

    payload = load_content().model_dump()
    payload["gathering_tasks"][0]["outputs"][1]["item_id"] = "maple_wood"
    with pytest.raises(ValidationError, match="unique"):
        GameContent.model_validate(payload)


def test_crop_roster_separates_tutorial_and_regular_economy():
    content = load_content()
    tutorial = content.crop_map["orange_berry"]
    regular_ids = ["carrot", "potato", "wheat", "pumpkin"]
    regular = [content.crop_map[crop_id] for crop_id in regular_ids]

    assert tutorial.stamina_cost == 0
    assert tutorial.seed_item_id not in {entry.item_id for entry in content.shop}
    assert all(crop.stamina_cost == 0 for crop in regular)
    assert all(crop.growth_seconds > tutorial.growth_seconds for crop in regular)
    assert all(crop.quality.thresholds == sorted(crop.quality.thresholds) for crop in regular)


def test_crafting_prices_reflect_inputs_and_mining_stamina_value():
    content = load_content()
    assert content.stamina.restore_seconds == 12 * 60
    assert all(task.duration_seconds >= task.stamina_cost * content.stamina.restore_seconds
               for task in content.mining_tasks)
    mining_values_per_stamina = []
    for task in content.mining_tasks:
        item = content.item_map[task.produce_item_id]
        average_yield = (task.yield_min + task.yield_max) / 2
        mining_values_per_stamina.append(average_yield * item.sell_price / task.stamina_cost)

    minimum_value = min(mining_values_per_stamina)
    maximum_value = max(mining_values_per_stamina)
    assert {
        item_id: content.item_map[item_id].sell_price
        for item_id in ("maple_plank", "herbal_salve", "flour", "pickled_carrot")
    } == {
        "maple_plank": 110,
        "herbal_salve": 120,
        "flour": 130,
        "pickled_carrot": 130,
    }
    for recipe in content.recipes:
        input_value = sum(
            content.item_map[entry.item_id].sell_price * entry.quantity
            for entry in recipe.inputs
        )
        output_value = content.item_map[recipe.produce_item_id].sell_price * recipe.produce_quantity
        added_value_per_stamina = (output_value - input_value) / recipe.stamina_cost
        if _is_equipment_recipe(content, recipe):
            # 装备配方不按售价考核，见 test_equipment_recipes_are_a_gear_line_not_a_money_line。
            assert recipe.stamina_cost == 5
            continue
        assert recipe.collect_xp == recipe.stamina_cost * 6
        if _is_refining_recipe(content, recipe):
            continue
        if recipe.id in {"make_iron_ingot", "make_wood_oil", "make_precision_parts"}:
            # 建设材料的定价受设施成本约束，不套日常赚钱配方收益区间。
            assert output_value > input_value
            continue
        if recipe.id == "brew_night_bell_drink":
            # 限时来源材料的活动配方保留 PR 售价，不纳入常驻采矿/加工收益对齐。
            assert output_value > input_value
            continue
        if recipe.id in {
            'make_flax_thread', 'make_reed_mat', 'make_rope', 'make_woven_cloth',
            'make_waterproof_canvas', 'make_voyage_sail', 'make_grain_fodder', 'make_nutrition_fodder',
        }:
            assert 52 <= added_value_per_stamina <= 55
        else:
            assert minimum_value <= added_value_per_stamina <= maximum_value


def _is_equipment_recipe(content, recipe) -> bool:
    return content.item_map[recipe.produce_item_id].kind == "equipment"


def test_equipment_recipes_are_a_gear_line_not_a_money_line():
    """装备配方的回报在副本里，不在售价上：三把武器全部卖 200，锻造出来一定亏钱。"""
    content = load_content()
    equipment = [recipe for recipe in content.recipes
                 if _is_equipment_recipe(content, recipe)
                 and content.item_map[recipe.produce_item_id].equipment.slot == "weapon"]

    assert {recipe.id for recipe in equipment} == {
        "forge_red_copper_greatsword",
        "forge_moon_silver_dagger",
        "bind_maple_flame_tome",
    }
    assert {
        recipe.id: [(entry.item_id, entry.quantity) for entry in recipe.inputs]
        for recipe in equipment
    } == {
        "forge_red_copper_greatsword": [("red_copper_ore", 20)],
        "forge_moon_silver_dagger": [("moon_silver_ore", 10)],
        "bind_maple_flame_tome": [("maple_wood", 10), ("maple_resin", 3), ("amber_beeswax", 1)],
    }
    for recipe in equipment:
        produce = content.item_map[recipe.produce_item_id]
        assert recipe.station_id == "town_workbench"
        assert (recipe.stamina_cost, recipe.produce_quantity) == (5, 1)
        assert recipe.unlock_condition.params["level"] == 10
        # 装备不进品质系统，加工是唯一允许产出无品质物品的产业。
        assert produce.has_quality is False
        assert produce.sell_price == 200
        assert produce.equipment.slot == "weapon"


def _is_refining_recipe(content, recipe) -> bool:
    """提纯配方把 units 高、score 低的原料换成 score 高的精料，回报在饲料槽而不在售价上。"""
    produce = content.item_map[recipe.produce_item_id].feed
    if produce is None:
        return False
    inputs = [content.item_map[entry.item_id].feed for entry in recipe.inputs]
    return all(entry is None or entry.score < produce.score for entry in inputs)


def test_refining_recipes_trade_sale_value_for_feed_score():
    """提纯配方不按售价考核，但必须真的提高单位品质分，否则它就没有存在意义。"""
    content = load_content()
    refining = [recipe for recipe in content.recipes if _is_refining_recipe(content, recipe)]
    assert {recipe.id for recipe in refining} == {"mill_fish_meal", "mix_fodder", "refine_fodder", "make_grain_fodder", "make_nutrition_fodder"}
    for recipe in refining:
        produce = content.item_map[recipe.produce_item_id].feed
        best_input = max(
            (content.item_map[entry.item_id].feed.score for entry in recipe.inputs if content.item_map[entry.item_id].feed),
            default=0,
        )
        assert produce.score > best_input


def test_unknown_crop_item_is_rejected():
    payload = load_content().model_dump()
    payload["crops"][0]["seed_item_id"] = "missing_seed"
    with pytest.raises(ValidationError, match="unknown item"):
        GameContent.model_validate(payload)


def test_duplicate_ids_are_rejected():
    payload = json.loads(load_content().model_dump_json())
    payload["items"].append(payload["items"][0])
    with pytest.raises(ValidationError, match="duplicate item"):
        GameContent.model_validate(payload)


def test_non_increasing_quality_thresholds_are_rejected():
    payload = load_content().model_dump()
    payload["crops"][0]["quality"]["thresholds"] = [40, 80, 80, 220]
    with pytest.raises(ValidationError, match="strictly increasing"):
        GameContent.model_validate(payload)


def test_every_recipe_requires_an_explicit_unlock_condition():
    payload = load_content().model_dump()
    payload["recipes"][0].pop("unlock_condition")
    with pytest.raises(ValidationError, match="unlock_condition"):
        GameContent.model_validate(payload)


def test_unknown_or_invalid_recipe_unlock_hook_is_rejected():
    payload = load_content().model_dump()
    payload["recipes"][0]["unlock_condition"]["hook"] = "implicit_default"
    with pytest.raises(ValidationError, match="unknown unlock hook"):
        GameContent.model_validate(payload)

    payload = load_content().model_dump()
    payload["recipes"][0]["unlock_condition"]["params"] = {}
    with pytest.raises(ValidationError, match="positive integer level"):
        GameContent.model_validate(payload)


def test_mining_task_requires_known_site_and_quality_item():
    payload = load_content().model_dump()
    payload["mining_tasks"][0]["site_id"] = "missing_mine"
    with pytest.raises(ValidationError, match="unknown site"):
        GameContent.model_validate(payload)

    payload = load_content().model_dump()
    payload["mining_tasks"][0]["produce_item_id"] = "carrot_seed"
    with pytest.raises(ValidationError, match="output must support quality"):
        GameContent.model_validate(payload)


def test_delve_content_is_wired_up():
    content = load_content()
    expedition = content.exploration_expedition_map["spiritfruit_meadow"]
    battle_choice = expedition.event_map["warden_grove"].choices[0]

    assert expedition.kind == "delve"
    # 灵果草甸已经外放，出厂内容里不该再留内测路线。
    assert expedition.beta is False
    assert not [entry for entry in content.exploration_expeditions if entry.beta]
    assert battle_choice.battle.enemy_ids == ["fruitheart_warden"]
    assert battle_choice.battle.can_flee is False
    assert content.delve_enemy_map["fruitheart_warden"].boss is True
    assert content.item_map["red_copper_greatsword"].equipment.attribute == "strength"
    assert content.item_map["moon_silver_dagger"].equipment.attribute == "agility"
    assert content.item_map["maple_flame_tome"].equipment.attribute == "intelligence"
    assert content.item_map["hunters_charm"].equipment.slot == "accessory"
    assert content.item_map["herbal_salve"].delve_use.effect == "heal"


def test_equipment_items_cannot_use_the_quality_system():
    payload = load_content().model_dump()
    equipment = next(item for item in payload["items"] if item["id"] == "red_copper_greatsword")
    equipment["has_quality"] = True

    with pytest.raises(ValidationError, match="quality"):
        GameContent.model_validate(payload)


def test_weapons_must_name_an_attribute_and_a_damage_dice():
    payload = load_content().model_dump()
    equipment = next(item for item in payload["items"] if item["id"] == "red_copper_greatsword")
    equipment["equipment"]["damage_dice"] = "1d7"

    with pytest.raises(ValidationError, match="damage dice"):
        GameContent.model_validate(payload)


def test_battles_are_rejected_outside_delve_expeditions_and_with_unknown_enemies():
    payload = load_content().model_dump()
    transport = next(
        entry for entry in payload["exploration_expeditions"] if entry["id"] == "red_maple_hinterland"
    )
    transport["events"][0]["choices"][0]["battle"] = {"enemy_ids": ["meadow_nibbler"]}

    with pytest.raises(ValidationError, match="delve expedition"):
        GameContent.model_validate(payload)

    payload = load_content().model_dump()
    delve = next(entry for entry in payload["exploration_expeditions"] if entry["id"] == "spiritfruit_meadow")
    delve["events"][0]["choices"][0]["battle"]["enemy_ids"] = ["nobody"]

    with pytest.raises(ValidationError, match="unknown enemies"):
        GameContent.model_validate(payload)


def test_a_checked_choice_cannot_also_start_a_battle():
    payload = load_content().model_dump()
    delve = next(entry for entry in payload["exploration_expeditions"] if entry["id"] == "spiritfruit_meadow")
    choice = delve["events"][0]["choices"][0]
    choice["check"] = {"attribute": "strength", "mode": "best", "dice": "normal", "dc": 12}
    choice["failure"] = {"text": "失手了。"}

    with pytest.raises(ValidationError, match="start a battle"):
        GameContent.model_validate(payload)


def test_fixed_rewards_must_be_quality_free_items():
    payload = load_content().model_dump()
    delve = next(entry for entry in payload["exploration_expeditions"] if entry["id"] == "spiritfruit_meadow")
    camp = next(entry for entry in delve["events"] if entry["id"] == "herder_shelter")
    camp["choices"][0]["success"]["fixed_rewards"] = [{"item_id": "tough_fodder", "quantity": 1}]

    with pytest.raises(ValidationError, match="quality-free"):
        GameContent.model_validate(payload)
