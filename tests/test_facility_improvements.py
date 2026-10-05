import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain.economy import add_item
from red_leaf_town.domain.models import PlayerState
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_content import PartnerCatalog, PartnerDefinition


@pytest.fixture
def game():
    content = load_content().model_copy(deep=True)
    repo = InMemoryPlayerRepository(content)
    clock = [1_800_000_000]
    partner = PartnerDefinition.model_validate({
        "id": "worker", "name": "工匠", "rarity": 3, "growth_curve": "linear",
        "tendencies": [{"industry": i, "level_1": 220, "level_60": 240} for i in ("farming", "crafting", "aquatic")],
        "trait_codes": ["primordial_return", "ghostfire_refinement"],
    })
    service = GameService(content, repo, clock=lambda: clock[0], partner_catalog_loader=lambda: PartnerCatalog(partners=[partner]))
    player = service.ensure_player("facilities", "建设测试")
    service.admin_grant_partner(player.player_id, partner.id)

    def prepare(p):
        p.experience = content.levels[-1].total_xp
        p.coins = 1_000_000
        p.stamina = 1000
        for item in content.items:
            add_item(p, item.id, 1000, 1 if item.has_quality else 0)
    repo.update(player.player_id, prepare)
    service.snapshot_by_sub("facilities")
    return service, repo, player.player_id, clock


def build_room(game):
    service, repo, pid, clock = game
    service.upgrade_facility("facilities", "refining_1")
    service.assign_refining_partner("facilities", "worker")
    return service, repo, pid, clock


def test_refining_cancel_refund_and_ordinary_boundary(game):
    service, repo, pid, clock = build_room(game)
    recipe = service.content.refining_recipes[0]
    before = repo.get(pid)
    service.start_refining("facilities", 0, recipe.id, 1)
    task = repo.get(pid).refining.slots[0]
    assert repo.get(pid).stamina == before.stamina
    clock[0] = task.quality_times[0] - 1
    service.finish_refining("facilities", 0, task.task_id, cancel=True)
    assert repo.get(pid).inventory[recipe.input_item_id] == before.inventory[recipe.input_item_id]
    service.start_refining("facilities", 0, recipe.id, 1)
    task = repo.get(pid).refining.slots[0]
    clock[0] = task.quality_times[0]
    result = service.finish_refining("facilities", 0, task.task_id, cancel=True)
    assert result["result"]["quality"] == task.quality_at(clock[0])
    assert not result["result"]["refunded"]
    assert repo.get(pid).inventory[recipe.input_item_id][1] == before.inventory[recipe.input_item_id][1] - recipe.input_quantity
    saved = repo.get(pid).inventory
    with pytest.raises(GameError):
        service.finish_refining("facilities", 0, task.task_id, cancel=True)
    assert repo.get(pid).inventory == saved


def test_refining_cap_freezes_and_full_room_locks_even_when_finished(game):
    service, repo, pid, clock = build_room(game)
    recipe = service.content.refining_recipes[0]
    for slot in range(len(repo.get(pid).refining.slots)):
        service.start_refining("facilities", slot, recipe.id, 1)
    before = repo.get(pid)
    service.content.refining_recipes[0].top_hours *= 2
    clock[0] = max(t.quality_times[-1] for t in before.refining.slots) + 1
    state = service.snapshot_by_sub("facilities")
    assert all(s["quality"] == s["max_quality"] for s in state["facilities"]["refining"]["slots"])
    for action in (lambda: service.assign_refining_partner("facilities", ""),
                   lambda: service.assign_partner("facilities", 0, "worker")):
        with pytest.raises(GameError):
            action()
    for slot, task in enumerate(before.refining.slots):
        service.finish_refining("facilities", slot, task.task_id)
    service.assign_partner("facilities", 0, "worker")
    assert not repo.get(pid).refining.assigned_partner_ids


def test_refining_shared_roster_and_invalid_quality(game):
    service, repo, pid, clock = build_room(game)
    with pytest.raises(GameError):
        service.start_refining("facilities", 0, service.content.refining_recipes[0].id, 5)
    assert not repo.get(pid).refining.occupied
    service.assign_crafting_partner("facilities", "town_workbench", "worker")
    assert not repo.get(pid).refining.assigned_partner_ids
    assert service._industry_assigned_count(repo.get(pid), "crafting") == len(repo.get(pid).crafting_stations[0].assigned_partner_ids)


def test_refining_experience_is_room_work_union_not_slot_sum(game):
    service, repo, pid, clock = build_room(game)
    recipe = service.content.refining_recipes[0]
    service.start_refining("facilities", 0, recipe.id, 1)
    single = repo.get(pid).model_copy(deep=True)
    service.start_refining("facilities", 1, recipe.id, 1)
    double = repo.get(pid).model_copy(deep=True)
    now = single.refining.slots[0].quality_times[-1]
    service._settle_refining(single, now)
    service._settle_refining(double, now)
    assert single.owned_partners == double.owned_partners
    saved = double.model_copy(deep=True)
    service._settle_refining(double, now + 86400)
    assert saved.owned_partners == double.owned_partners


def test_merged_farm_independent_tasks_atomic_costs_and_single_collection(game):
    service, repo, pid, clock = game
    service.assign_partner("facilities", 0, "worker")
    service.upgrade_facility("facilities", "farm_1", "worker")
    before = repo.get(pid)
    again = service.upgrade_facility("facilities", "farm_1", "worker")
    assert not again["result"]["changed"]
    assert repo.get(pid).coins == before.coins
    crop = service.content.crop_map["carrot"]
    state = service.plant("facilities", 0, crop.id)["state"]
    after = repo.get(pid)
    tasks = [p.task_snapshot for p in after.plots[:2]]
    assert tasks[0] is not tasks[1]
    assert tasks[0].assigned_partner_ids == tasks[1].assigned_partner_ids
    assert not after.plots[1].assigned_partner_ids
    assert len(state["plots"]) == len(after.plots) - after.farm_upgrade_stage
    assert sum(before.inventory[crop.seed_item_id].values()) - sum(after.inventory[crop.seed_item_id].values()) == len(tasks)
    with pytest.raises(GameError):
        service.plant("facilities", 1, crop.id)
    clock[0] = max(t.ready_at for t in tasks)
    service.snapshot_by_sub("facilities")
    ready = repo.get(pid)
    quantity = sum(r.quantity for p in ready.plots[:2] for r in p.task_results)
    service.harvest("facilities", 0)
    collected = repo.get(pid)
    assert sum(collected.inventory[crop.produce_item_id].values()) - sum(before.inventory[crop.produce_item_id].values()) == quantity
    assert all(p.empty for p in collected.plots[:2])
    with pytest.raises(GameError):
        service.harvest("facilities", 0)


def test_merged_farm_resource_failure_rolls_back_and_cancel_refunds(game):
    service, repo, pid, clock = game
    service.upgrade_facility("facilities", "farm_1")
    crop = service.content.crop_map["carrot"]
    repo.update(pid, lambda p: p.inventory.__setitem__(crop.seed_item_id, {0: 1}))
    before = repo.get(pid)
    with pytest.raises(GameError):
        service.plant("facilities", 0, crop.id)
    assert repo.get(pid).inventory == before.inventory
    repo.update(pid, lambda p: add_item(p, crop.seed_item_id, 1, 0))
    before = repo.get(pid)
    service.plant("facilities", 0, crop.id)
    service.cancel_task("facilities", "farming", "0")
    assert repo.get(pid).inventory == before.inventory
    assert all(p.empty for p in repo.get(pid).plots[:2])


def test_pond_species_cost_and_feed_upgrade_preserve_existing_state(game):
    service, repo, pid, clock = game
    definition = service.content.pond_map["pond_blue"]
    before = repo.get(pid)
    service.build_pond("facilities", definition.id)
    after = repo.get(pid)
    for material in definition.build_materials:
        assert sum(before.inventory[material.item_id].values()) - sum(after.inventory[material.item_id].values()) == material.quantity
    with pytest.raises(GameError):
        service.stock_pond("facilities", definition.id, "crucian", 1)
    service.stock_pond("facilities", definition.id, definition.species_id, 1)
    before = repo.get(pid)
    pond = before.ponds[-1]
    previous = service._pond_parameters(before, pond)
    service.upgrade_facility("facilities", "feed_1")
    after = repo.get(pid)
    assert after.ponds[-1].population == pond.population
    assert service._pond_parameters(after, after.ponds[-1]).feed_per_cycle < previous.feed_per_cycle
    assert service._feed_slot_capacity(after) > service._feed_slot_capacity(before)


def test_old_state_gains_level_land_once_without_free_upgrades(game):
    service, repo, pid, clock = game
    old = repo.get(pid).model_dump()
    for field in ("farm_upgrade_stage", "feed_upgrade_stage", "refining"):
        old.pop(field)
    old["schema_version"] = 33
    old["plots"] = old["plots"][:8]
    migrated = PlayerState.model_validate(old)
    service._settle(migrated, clock[0])
    once = migrated.model_dump()
    service._settle(migrated, clock[0])
    assert migrated.model_dump() == once
    assert len(migrated.plots) == service.content.level_definition(migrated.level).plot_slots
    assert not migrated.refining.built and not migrated.farm_upgrade_stage and not migrated.feed_upgrade_stage


def test_refining_roster_talent_allows_workshop_and_room_together(game):
    service, repo, pid, clock = build_room(game)
    worker = service.partner_catalog_loader().partners[0]
    second = worker.model_copy(update={"id": "second"})
    service.partner_catalog_loader = lambda: PartnerCatalog(partners=[worker, second])
    service.admin_grant_partner(pid, second.id)
    with pytest.raises(GameError, match="编制"):
        service.assign_crafting_partner("facilities", "town_workbench", second.id)
    assert repo.get(pid).refining.assigned_partner_ids == [worker.id]
    node = service.content.talent_map["crafting_roster_1"]
    def enable(p):
        p.talent_nodes = list(dict.fromkeys([*p.talent_nodes, *node.prerequisites, node.id]))
    repo.update(pid, enable)
    service.assign_crafting_partner("facilities", "town_workbench", second.id)
    state = repo.get(pid)
    assert state.refining.assigned_partner_ids == [worker.id]
    assert state.crafting_stations[0].assigned_partner_ids == [second.id]


def test_refining_ignores_crafting_traits_and_time_talent(game):
    service, repo, pid, clock = build_room(game)
    recipe = service.content.refining_recipes[0]
    service.start_refining("facilities", 0, recipe.id, 1)
    baseline = repo.get(pid).refining.slots[0]
    worker = service.partner_catalog_loader().partners[0].model_copy(update={"trait_codes": []})
    service.partner_catalog_loader = lambda: PartnerCatalog(partners=[worker])
    talent = next(n for n in service.content.talents if "crafting_duration_reduction" in n.modifiers)
    repo.update(pid, lambda p: p.talent_nodes.append(talent.id))
    service.start_refining("facilities", 1, recipe.id, 1)
    assert repo.get(pid).refining.slots[1].quality_times == baseline.quality_times
    route = service.content.exploration_expeditions[0]
    with pytest.raises(GameError, match="精制房"):
        service.start_exploration("facilities", route.id, [worker.id], worker.id)


def test_refining_higher_raw_quality_is_faster_with_same_output_curve(game):
    service, repo, pid, clock = build_room(game)
    recipe = next(r for r in service.content.refining_recipes if r.id == "grape_wine")
    raw = service.content.item_map[recipe.input_item_id]
    tasks = []
    for slot, quality in enumerate(range(1, 5)):
        repo.update(pid, lambda p: add_item(p, recipe.input_item_id, recipe.input_quantity, quality))
        service.start_refining("facilities", slot, recipe.id, quality)
        tasks.append(repo.get(pid).refining.slots[slot])
    for slower, faster in zip(tasks, tasks[1:]):
        assert all(a > b for a, b in zip(slower.quality_times, faster.quality_times))
        assert slower.output_prices == faster.output_prices
        slow_profit = (slower.output_prices[-1] - slower.input_value) / (slower.quality_times[-1] - slower.started_at)
        fast_profit = (faster.output_prices[-1] - faster.input_value) / (faster.quality_times[-1] - faster.started_at)
        assert fast_profit > slow_profit
    # 提质边际增值/时间保持一致，容许逐段向上取整的一秒误差。
    for task in tasks:
        durations = [at - task.started_at for at in task.quality_times]
        raw_ordinary = raw.sell_price * recipe.input_quantity
        for duration, price in zip(durations, task.output_prices):
            expected = durations[-1] * (price - raw_ordinary) / (task.output_prices[-1] - raw_ordinary)
            assert abs(duration - expected) <= 1


def test_double_field_consumes_and_refunds_two_task_items(game):
    service, repo, pid, clock = game
    service.upgrade_facility("facilities", "farm_1")
    item = next(i for i in service.content.task_items if i.timing == "start" and (not i.eligible_industries or "farming" in i.eligible_industries))
    count = len(service._farm_units(repo.get(pid), 0))
    repo.update(pid, lambda p: p.task_items.__setitem__(item.id, count - 1))
    before = repo.get(pid)
    with pytest.raises(GameError):
        service.plant("facilities", 0, "carrot", item.id)
    assert repo.get(pid).inventory == before.inventory
    repo.update(pid, lambda p: p.task_items.__setitem__(item.id, count))
    service.plant("facilities", 0, "carrot", item.id)
    assert not repo.get(pid).task_items.get(item.id)
    service.cancel_task("facilities", "farming", "0")
    assert repo.get(pid).task_items[item.id] == count
