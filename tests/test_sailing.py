import random

import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain import PlayerState
from red_leaf_town.domain.economy import add_item
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_content import PartnerCatalog, PartnerDefinition
from red_leaf_town.sailing_content import SailingContent, load_sailing_content


@pytest.fixture
def sailing_game(monkeypatch):
    content = load_content()
    repo = InMemoryPlayerRepository(content)
    catalog = PartnerCatalog(partners=[PartnerDefinition.model_validate({
        'id': name, 'name': name, 'rarity': 3,
        'tendencies': [{'industry': industry, 'level_1': 40, 'level_60': 80}],
        'avatar_crops': [{'breakthrough': stage, 'x': 0, 'y': 0, 'w': 1, 'h': 1} for stage in range(3)],
    }) for name, industry in [('sailor', 'aquatic'), ('scout', 'exploration'), ('farmer', 'farming')]])
    clock = [1_700_000_000]
    game = GameService(content, repo, clock=lambda: clock[0], rng=random.Random(7), partner_catalog_loader=lambda: catalog)
    player = game.ensure_player('sailing-sub', '航海测试员')
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS', 'someone-else')
    for entry in catalog.partners:
        game.admin_grant_partner(player.player_id, entry.id)

    def prepare(p):
        p.experience = content.level_definition(16).total_xp
        p.coins = 100_000
        p.stamina = 100
        p.sailing.ship_built = True
        for item in ('pickled_carrot', 'refined_fodder', 'maple_plank', 'red_copper_ore'):
            add_item(p, item, 50, 1)
    repo.update(player.player_id, prepare)
    return game, repo, player, clock


def start(game, request_id='voyage-1', route='reed_bay', party=None, supply='none'):
    return game.start_sailing('sailing-sub', route, ['sailor'] if party is None else party, supply, request_id)


def finish(game, clock, started):
    run = started['state']['sailing']['active_run']
    clock[0] = run['ready_at']
    return game.collect_sailing('sailing-sub', run['run_id'])


def test_sailing_content_references_and_duplicate_validation():
    content = load_sailing_content()
    content.validate_items(load_content().item_map)
    with pytest.raises(ValueError, match='unknown sailing items'):
        content.validate_items({})
    payload = content.model_dump()
    payload['routes'].append(payload['routes'][0])
    with pytest.raises(ValueError, match='duplicate sailing'):
        SailingContent.model_validate(payload)


def test_schema_30_migrates_without_changing_existing_assets(sailing_game):
    game, repo, player, _ = sailing_game
    old = repo.get(player.player_id).model_dump()
    old.pop('sailing')
    old['schema_version'] = 30
    migrated = PlayerState.model_validate(old)
    assert migrated.schema_version == PlayerState.model_fields["schema_version"].default
    assert migrated.sailing.active_run is None
    assert migrated.sailing.completed_voyages == 0
    assert migrated.inventory == old['inventory']


def test_public_sailing_retains_level_gates(sailing_game, monkeypatch):
    game, repo, player, _ = sailing_game
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS', 'someone-else')
    assert game.snapshot_by_sub('sailing-sub')['sailing']['unlocked']
    repo.update(player.player_id, lambda p: setattr(p, 'experience', 0))
    assert not game.snapshot_by_sub('sailing-sub')['sailing']['unlocked']
    for action in (lambda: game.build_sailing_ship('sailing-sub'), lambda: start(game),
                   lambda: game.collect_sailing('sailing-sub', 'missing'),
                   lambda: game.upgrade_sailing('sailing-sub', 'cargo', 0)):
        with pytest.raises(GameError, match='16'):
            action()


@pytest.mark.parametrize('party', [[], ['sailor', 'sailor'], ['sailor'] * 4, ['missing'], [None], 'sailor', [{}]])
def test_invalid_parties_never_charge(sailing_game, party):
    game, repo, player, _ = sailing_game
    before = repo.get(player.player_id)
    with pytest.raises(GameError):
        start(game, party=party)
    after = repo.get(player.player_id)
    assert after.coins == before.coins
    assert after.inventory == before.inventory
    assert after.sailing.active_run is None


def test_trial_freezes_results_and_collects_once_after_arrival(sailing_game):
    game, repo, player, clock = sailing_game
    before = repo.get(player.player_id)
    started = start(game)
    run = started['state']['sailing']['active_run']
    frozen = repo.get(player.player_id).sailing.active_run.model_dump()
    assert run['ready_at'] - run['started_at'] == 60
    assert not run['drops'] and not run['logs']
    assert repo.get(player.player_id).coins == before.coins - run['coins']
    assert repo.get(player.player_id).stamina == before.stamina - run['stamina']
    assert repo.get(player.player_id).inventory == before.inventory
    assert start(game)['result']['duplicate']
    assert repo.get(player.player_id).sailing.active_run.model_dump() == frozen
    assert game.snapshot_by_sub('sailing-sub')['sailing']['active_run'] == run
    with pytest.raises(GameError, match='还没有回港'):
        game.collect_sailing('sailing-sub', run['run_id'])
    clock[0] = run['ready_at'] + 86400
    ready = game.snapshot_by_sub('sailing-sub')
    assert not next(p for p in ready['partners'] if p['partner_id'] == 'sailor')['locked']
    assert ready['sailing']['active_run']['drops']
    claimed = game.collect_sailing('sailing-sub', run['run_id'])
    assert claimed['state']['sailing']['completed_voyages'] == 1
    assert claimed['state']['sailing']['active_run'] is None
    assert repo.get(player.player_id).sailing.last_run.model_dump() == frozen
    after = repo.get(player.player_id)
    for drop in frozen['drops']:
        assert after.inventory[drop['item_id']][drop['quality']] == drop['quantity']
    assert after.experience == before.experience + frozen['experience']
    assert next(p for p in after.owned_partners if p.partner_id == 'sailor').experience > 0
    assert game.collect_sailing('sailing-sub', run['run_id'])['result']['duplicate']
    assert repo.get(player.player_id).inventory == after.inventory
    assert repo.get(player.player_id).experience == after.experience
    assert start(game)['result']['duplicate']
    assert repo.get(player.player_id).sailing.active_run is None


def test_normal_routes_unlock_by_completed_voyages(sailing_game):
    game, _, _, clock = sailing_game
    with pytest.raises(GameError, match='完成 1 次'):
        start(game, route='white_sail')
    first = finish(game, clock, start(game))
    assert first['state']['sailing']['routes'][1]['unlocked']
    second = start(game, 'voyage-2', 'white_sail')
    run = second['state']['sailing']['active_run']
    assert not run['trial'] and run['ready_at'] - run['started_at'] == 21600
    with pytest.raises(GameError, match='船还在航行'):
        start(game, 'voyage-3')
    finish(game, clock, second)
    finish(game, clock, start(game, 'voyage-3'))
    third = start(game, 'voyage-4', 'mist_isles', ['scout'])
    assert third['state']['sailing']['active_run']['ability'] > 0
    finish(game, clock, third)


def test_supply_cost_uses_lowest_quality_and_failure_rolls_back(sailing_game):
    game, repo, player, _ = sailing_game
    def prepare(p):
        p.inventory['pickled_carrot'] = {1: 1, 3: 4}
    repo.update(player.player_id, prepare)
    start(game, supply='ration')
    state = repo.get(player.player_id)
    assert state.inventory['pickled_carrot'] == {3: 3}
    assert state.sailing.active_run.consumed_inputs == [
        {'item_id': 'pickled_carrot', 'quality': 1, 'quantity': 1},
        {'item_id': 'pickled_carrot', 'quality': 3, 'quantity': 1},
    ]


def test_insufficient_resources_do_not_charge_or_move_partner(sailing_game):
    game, repo, player, _ = sailing_game
    def prepare(p):
        p.inventory['pickled_carrot'] = {1: 1}
        p.fishing.companion_partner_id = 'sailor'
    repo.update(player.player_id, prepare)
    before = repo.get(player.player_id)
    with pytest.raises(GameError, match='补给数量不足'):
        start(game, supply='ration')
    after = repo.get(player.player_id)
    assert after.inventory == before.inventory and after.coins == before.coins
    assert after.stamina == before.stamina
    assert after.fishing.companion_partner_id == 'sailor'
    repo.update(player.player_id, lambda p: setattr(p, 'stamina', 0))
    with pytest.raises(GameError, match='体力不足'):
        start(game)
    assert repo.get(player.player_id).coins == before.coins


def test_departure_clears_fishing_and_blocks_farming_fishing_and_exploration(sailing_game):
    game, repo, player, clock = sailing_game
    game.assign_fishing_companion('sailing-sub', 'sailor')
    started = start(game, party=['sailor', 'scout', 'farmer'])
    assert repo.get(player.player_id).fishing.companion_partner_id == ''
    with pytest.raises(GameError, match='伙伴正在'):
        game.assign_fishing_companion('sailing-sub', 'sailor')
    with pytest.raises(GameError, match='伙伴正在'):
        game.assign_partner('sailing-sub', 0, 'farmer')
    with pytest.raises(GameError, match='伙伴正在出海'):
        game.start_exploration('sailing-sub', 'red_maple_hinterland', ['scout'], 'scout')
    clock[0] = started['state']['sailing']['active_run']['ready_at']
    game.assign_fishing_companion('sailing-sub', 'sailor')
    game.start_exploration('sailing-sub', 'red_maple_hinterland', ['scout'], 'scout')


def test_active_explorer_cannot_sail(sailing_game):
    game, _, _, _ = sailing_game
    game.start_exploration('sailing-sub', 'red_maple_hinterland', ['scout'], 'scout')
    with pytest.raises(GameError, match='伙伴正在探索'):
        start(game, party=['scout'])


def test_upgrades_are_atomic_bounded_and_frozen_for_voyage(sailing_game):
    game, repo, player, clock = sailing_game
    before = repo.get(player.player_id)
    upgraded = game.upgrade_sailing('sailing-sub', 'cargo', 0)
    assert upgraded['result']['level'] == 1
    assert repo.get(player.player_id).coins < before.coins
    with pytest.raises(GameError, match='等级已变化'):
        game.upgrade_sailing('sailing-sub', 'cargo', 0)
    started = start(game)
    assert started['state']['sailing']['active_run']['cargo_level'] == 1
    with pytest.raises(GameError, match='回港'):
        game.upgrade_sailing('sailing-sub', 'cargo', 1)
    finish(game, clock, started)
    game.upgrade_sailing('sailing-sub', 'cargo', 1)
    game.upgrade_sailing('sailing-sub', 'cargo', 2)
    with pytest.raises(GameError, match='满级'):
        game.upgrade_sailing('sailing-sub', 'cargo', 3)
    assert repo.get(player.player_id).sailing.last_run.cargo_level == 1


def test_busy_production_partner_cannot_depart_and_assignment_is_preserved(sailing_game):
    game, repo, player, _ = sailing_game
    game.buy('sailing-sub', 'carrot_seed', 1)
    game.assign_partner('sailing-sub', 0, 'farmer')
    game.plant('sailing-sub', 0, 'carrot')
    before = repo.get(player.player_id)
    with pytest.raises(GameError, match='伙伴正在参与任务'):
        start(game, party=['farmer'])
    after = repo.get(player.player_id)
    assert after.coins == before.coins and after.stamina == before.stamina
    assert after.plots == before.plots
    assert after.sailing.active_run is None


def test_route_pools_are_varied_and_rare_items_never_common():
    content = load_sailing_content()
    items = load_content().item_map
    common = {o.item_id for r in content.routes for o in r.outputs if o.rarity == 'common'}
    assert len({o.item_id for r in content.routes for o in r.outputs}) == 20
    for route in content.routes:
        assert len(route.outputs) in (7, 8)
        assert sum(o.rarity == 'common' for o in route.outputs) == 3
        assert sum(o.rarity == 'rare' for o in route.outputs) == 2
        for output in route.outputs:
            if output.rarity == 'rare':
                assert output.item_id not in common
            assert (items[output.item_id].kind == 'equipment') == (output.rarity == 'equipment')
    payload = content.model_dump()
    next(o for o in payload['routes'][0]['outputs'] if o['rarity'] == 'rare')['item_id'] = 'sea_shell'
    with pytest.raises(ValueError, match='cannot be common'):
        SailingContent.model_validate(payload)


@pytest.mark.parametrize('count', [1, 6, 14, 18, 30, 60])
@pytest.mark.parametrize('nets,rare_supply', [(0, False), (3, False), (3, True)])
def test_equipment_first_drop_cost_is_250_stamina_even_with_bonuses(count, nets, rare_supply):
    from red_leaf_town.application.sailing import sailing_output_pool
    for route in load_sailing_content().routes:
        for stamina in (1, route.stamina):
            pool = sailing_output_pool(route, count, stamina, nets, rare_supply)
            equipment_id = next(o.item_id for o in route.outputs if o.rarity == 'equipment')
            chance = next(o.weight for o in pool if o.item_id == equipment_id) / sum(o.weight for o in pool)
            voyage_chance = 1 - (1 - chance) ** count
            assert stamina / voyage_chance == pytest.approx(250)


@pytest.mark.parametrize('crop_id,reference', [('passho_berry', 'peach_berry'), ('pamtre_berry', 'berry_berry')])
def test_sailing_berries_plant_harvest_and_follow_existing_economy(sailing_game, crop_id, reference):
    game, repo, player, clock = sailing_game
    crop = game.content.crop_map[crop_id]
    previous = game.content.crop_map[reference]
    assert crop.stamina_cost == previous.stamina_cost > 0
    assert crop.quality == previous.quality
    assert game.content.item_map[crop_id].sell_price == game.content.item_map[reference].sell_price
    assert crop.seed_item_id not in {s.item_id for s in game.content.shop}
    repo.update(player.player_id, lambda p: add_item(p, crop.seed_item_id, 1))
    before = repo.get(player.player_id).stamina
    planted = game.plant('sailing-sub', 0, crop_id)
    assert repo.get(player.player_id).stamina == before - crop.stamina_cost
    clock[0] += planted['result']['final_duration']
    harvested = game.harvest('sailing-sub', 0)
    assert harvested['result']['item_id'] == crop_id
    assert harvested['result']['quantity'] == 2


def test_weighted_sailing_can_award_and_collect_exploration_equipment(sailing_game, monkeypatch):
    game, repo, player, clock = sailing_game
    monkeypatch.setattr(game.rng, 'random', lambda: 0.999999999)
    started = start(game)
    run = repo.get(player.player_id).sailing.active_run
    assert run.rule_version == 4
    assert {d.item_id for d in run.drops} == {'bay_tide_blade'}
    finish(game, clock, started)
    assert repo.get(player.player_id).inventory['bay_tide_blade'][0] >= 1


def test_sailing_economy_stays_near_existing_fishing_curve():
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'sailing_yield.py'
    spec = importlib.util.spec_from_file_location('sailing_yield', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fishing_spec = importlib.util.spec_from_file_location('fishing_yield', script.with_name('fishing_yield.py'))
    fishing = importlib.util.module_from_spec(fishing_spec)
    fishing_spec.loader.exec_module(fishing)
    service = GameService.__new__(GameService)
    service.content = load_content()
    lake = service.content.fishing_spots[1]
    for ability in (0, 40, 80, 120):
        baseline = fishing.cast_yield(service, lake, ability, 0)['per_stamina']
        upgraded = fishing.cast_yield(service, lake, ability, service.content.fishing_combo.max_layers)['per_stamina']
        for route in load_sailing_content().routes:
            direct, chain = module.sailing_yield(service, route, ability, ability)
            assert 0.55 * baseline <= direct <= 1.3 * baseline
            assert 0.65 * baseline <= chain <= 1.3 * baseline
            for supply in load_sailing_content().supplies:
                best = module.sailing_yield(service, route, ability, ability, 3, 3, supply.id, 4)[1]
                assert best <= 1.7 * upgraded


def test_build_ship_charges_once_and_spends_low_quality_first(sailing_game):
    game, repo, player, _ = sailing_game
    def prepare(p):
        p.sailing.ship_built = False
        p.inventory['composite_plank'] = {1: 7, 2: 16}
        p.inventory['voyage_sail'] = {1: 1, 3: 1}
    repo.update(player.player_id, prepare)
    before = repo.get(player.player_id)
    result = game.build_sailing_ship('sailing-sub')
    after = repo.get(player.player_id)
    assert after.sailing.ship_built
    assert after.coins == before.coins - 10_000
    assert after.stamina == before.stamina
    assert after.inventory['composite_plank'] == {2: 3}
    assert after.inventory['voyage_sail'] == {3: 1}
    assert result['state']['sailing']['trial_available']
    assert game.build_sailing_ship('sailing-sub')['result']['duplicate']
    assert repo.get(player.player_id).inventory == after.inventory
    assert repo.get(player.player_id).coins == after.coins
    assert start(game)['result']['trial']


@pytest.mark.parametrize('coins,boards', [(9999, 20), (10000, 19), (10000, 20)])
def test_build_ship_insufficient_resources_roll_back(sailing_game, coins, boards):
    game, repo, player, _ = sailing_game
    def prepare(p):
        p.sailing.ship_built = False
        p.coins = coins
        p.inventory['composite_plank'] = {1: boards}
    repo.update(player.player_id, prepare)
    before = repo.get(player.player_id)
    with pytest.raises(GameError, match='不足'):
        game.build_sailing_ship('sailing-sub')
    after = repo.get(player.player_id)
    assert after.coins == before.coins and after.inventory == before.inventory
    assert not after.sailing.ship_built
    with pytest.raises(GameError, match='先建造'):
        start(game)
    with pytest.raises(GameError, match='先建造'):
        game.upgrade_sailing('sailing-sub', 'cargo', 0)


def test_old_saves_preserve_existing_ships_only():
    from red_leaf_town.domain.sailing import SailingState
    assert not SailingState.model_validate({}).ship_built
    assert SailingState.model_validate({'completed_voyages': 1}).ship_built
    assert SailingState.model_validate({'cargo_level': 1}).ship_built
    assert not SailingState.model_validate({'ship_built': False, 'completed_voyages': 1}).ship_built


def test_composite_plank_recipe_consumes_both_materials(sailing_game):
    game, repo, player, clock = sailing_game
    recipe = game.content.recipe_map['make_composite_plank']
    assert {i.item_id: i.quantity for i in recipe.inputs} == {'maple_plank': 2, 'moon_silver_ore': 2}
    assert recipe.produce_quantity == 1
    repo.update(player.player_id, lambda p: add_item(p, 'moon_silver_ore', 2, 1))
    before = repo.get(player.player_id)
    crafted = game.start_crafting('sailing-sub', 'town_workbench', recipe.id)
    after = repo.get(player.player_id)
    assert sum(after.inventory['maple_plank'].values()) == sum(before.inventory['maple_plank'].values()) - 2
    assert not after.inventory.get('moon_silver_ore')
    clock[0] = crafted['result']['ready_at']
    game.collect_crafting('sailing-sub', 'town_workbench')
    assert sum(repo.get(player.player_id).inventory['composite_plank'].values()) == 1


def test_sailing_quality_is_rolled_per_unit_frozen_and_collected_once(sailing_game, monkeypatch):
    from red_leaf_town.application import sailing
    game, repo, player, clock = sailing_game
    monkeypatch.setattr(sailing, 'draw_weighted_batches', lambda *args: [('sea_fish', 3), ('bay_tide_blade', 1)])
    rolls = iter([2, 5, 2])
    monkeypatch.setattr(sailing, 'roll_quality', lambda *args: next(rolls))
    started = start(game)
    run = repo.get(player.player_id).sailing.active_run
    assert {(d.item_id, d.quality): d.quantity for d in run.drops} == {
        ('sea_fish', 2): 2, ('sea_fish', 5): 1, ('bay_tide_blade', 0): 1,
    }
    monkeypatch.setattr(sailing, 'roll_quality', lambda *args: pytest.fail('must not reroll after departure'))
    result = finish(game, clock, started)
    assert repo.get(player.player_id).inventory['sea_fish'] == {2: 2, 5: 1}
    assert repo.get(player.player_id).sailing.collected_items['sea_fish'] == 3
    assert {d['quality_name'] for d in result['state']['sailing']['last_run']['drops']} == {'良品', '奇迹', ''}
    assert game.collect_sailing('sailing-sub', run.run_id)['result']['duplicate']
    assert repo.get(player.player_id).inventory['sea_fish'] == {2: 2, 5: 1}


def test_old_sailing_drops_and_inventory_remain_collectible_and_sellable(sailing_game):
    from red_leaf_town.domain.sailing import SailingDrop
    game, repo, player, clock = sailing_game
    started = start(game)
    def legacy(p):
        p.sailing.active_run.rule_version = 3
        p.sailing.active_run.drops = [SailingDrop(item_id='sea_fish', quantity=2),
                                     SailingDrop(item_id='bay_sardine', quantity=3)]
        add_item(p, 'sea_fish', 4)
    repo.update(player.player_id, legacy)
    finish(game, clock, started)
    assert repo.get(player.player_id).inventory['sea_fish'] == {1: 6}
    assert repo.get(player.player_id).inventory['bay_sardine'] == {0: 3}
    game.sell('sailing-sub', 'sea_fish', 6, 1)
    game.sell('sailing-sub', 'bay_sardine', 3)


def test_sailing_quality_configuration_cannot_silently_fall_back():
    content = load_sailing_content()
    items = load_content().item_map
    payload = content.model_dump()
    payload['routes'][0]['outputs'][0]['quality'] = None
    with pytest.raises(ValueError, match='quality curve does not match'):
        SailingContent.model_validate(payload).validate_items(items)
    payload = content.model_dump()
    payload['routes'][0]['outputs'][-1]['quality'] = payload['routes'][0]['outputs'][0]['quality']
    with pytest.raises(ValueError, match='quality curve does not match'):
        SailingContent.model_validate(payload).validate_items(items)


def test_sailing_achievements_only_count_claims_and_preserve_known_legacy_route(sailing_game):
    game, repo, player, clock = sailing_game
    first = start(game)
    assert not any(a['completed'] for a in first['state']['achievements']['entries'] if a['achievement_id'] == 'first_sailing')
    finish(game, clock, first)
    # A legacy save has a last claimed voyage, but no new route history.
    repo.update(player.player_id, lambda p: p.achievement_stats.sailing_route_ids.clear())
    second = start(game, 'second', 'white_sail')
    clock[0] = second['state']['sailing']['active_run']['ready_at']
    pending = game.snapshot_by_sub('sailing-sub')
    assert next(a for a in pending['achievements']['entries'] if a['achievement_id'] == 'three_sailing_routes')['current'] == 1
    finish(game, clock, second)
    assert repo.get(player.player_id).achievement_stats.sailing_route_ids == ['reed_bay', 'white_sail']
    game.collect_sailing('sailing-sub', second['result']['run_id'])
    assert repo.get(player.player_id).sailing.completed_voyages == 2


@pytest.mark.parametrize('completed_voyages', [0, 1])
def test_cancel_sailing_refunds_snapshot_and_unlocks_without_rewards(sailing_game, completed_voyages):
    game, repo, player, clock = sailing_game
    def prepare(p):
        p.stamina = 40
        p.inventory['pickled_carrot'] = {1: 1, 3: 4}
        p.sailing.completed_voyages = completed_voyages
    repo.update(player.player_id, prepare)
    before = repo.get(player.player_id)
    started = start(game, supply='ration')
    run = started['state']['sailing']['active_run']
    assert next(p for p in started['state']['partners'] if p['partner_id'] == 'sailor')['locked']
    clock[0] += 1
    result = game.cancel_sailing('sailing-sub', run['run_id'])
    after = repo.get(player.player_id)
    assert after.coins == before.coins
    assert after.stamina == before.stamina
    assert after.inventory == before.inventory
    assert after.experience == before.experience
    assert after.owned_partners == before.owned_partners
    assert after.sailing.active_run is None
    assert after.sailing.completed_voyages == completed_voyages
    assert after.sailing.last_run == before.sailing.last_run
    assert after.sailing.discoveries == before.sailing.discoveries
    assert after.sailing.collected_items == before.sailing.collected_items
    assert not next(p for p in result['state']['partners'] if p['partner_id'] == 'sailor')['locked']
    assert result['state']['sailing']['trial_available'] == (completed_voyages == 0)
    with pytest.raises(GameError):
        game.cancel_sailing('sailing-sub', run['run_id'])
    assert repo.get(player.player_id).coins == after.coins
    assert repo.get(player.player_id).inventory == after.inventory
    with pytest.raises(GameError):
        game.collect_sailing('sailing-sub', run['run_id'])
    assert start(game)['result']['duplicate']
    assert repo.get(player.player_id).sailing.active_run is None
    second = start(game, request_id='new-voyage')
    with pytest.raises(GameError):
        game.cancel_sailing('sailing-sub', run['run_id'])
    assert repo.get(player.player_id).sailing.active_run.run_id == second['result']['run_id']


@pytest.mark.parametrize('offset', [0, 1])
def test_cancel_arrived_sailing_is_rejected_and_can_still_collect(sailing_game, offset):
    game, repo, player, clock = sailing_game
    started = start(game, supply='ration')
    run = started['state']['sailing']['active_run']
    clock[0] = run['ready_at'] + offset
    before = repo.get(player.player_id)
    with pytest.raises(GameError, match='船已回港'):
        game.cancel_sailing('sailing-sub', run['run_id'])
    after = repo.get(player.player_id)
    assert after.coins == before.coins
    assert after.inventory == before.inventory
    assert after.sailing.active_run == before.sailing.active_run
    assert game.collect_sailing('sailing-sub', run['run_id'])['result']['drops']


@pytest.mark.parametrize('stamina_at_cancel', [47, 60])
def test_cancel_sailing_stamina_respects_cap_and_preserves_overflow(sailing_game, stamina_at_cancel):
    game, repo, player, _ = sailing_game
    repo.update(player.player_id, lambda p: setattr(p.sailing, 'completed_voyages', 1))
    started = start(game)
    repo.update(player.player_id, lambda p: setattr(p, 'stamina', stamina_at_cancel))
    game.cancel_sailing('sailing-sub', started['result']['run_id'])
    cap = game.content.level_definition(16).stamina_cap
    cost = started['state']['sailing']['active_run']['stamina']
    assert repo.get(player.player_id).stamina == max(stamina_at_cancel, min(cap, stamina_at_cancel + cost))
