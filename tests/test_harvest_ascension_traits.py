from dataclasses import replace
import random

import pytest

from red_leaf_town.application import GameService
from red_leaf_town.content import load_content, GatheringDrawDefinition
from red_leaf_town.domain import OwnedPartnerState, AnimalState, LivestockFacilityState
from red_leaf_town.domain.livestock import FacilityParameters, SpeciesParameters, advance_facility
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_traits import active_partner_trait_codes


@pytest.fixture
def game():
    content = load_content()
    service = GameService(content, InMemoryPlayerRepository(content), clock=lambda: 1793940000)
    player = service.ensure_player('trait-test', '特性测试')
    player.owned_partners = [OwnedPartnerState(partner_id=pid, level=20, breakthrough=0, acquired_at=1)
                             for pid in ['leilei', 'luo_nali', 'gujian_miao', 'mami', 'zhuoyan']]
    return service, player


def task(service, player, pid, industry, draws=None):
    return service._build_production_task_snapshot(
        player=player, assigned_partner_ids=[pid], industry=industry, content_id='test',
        production_slot_id='test', now=1793940000, base_duration=600, minimum_duration=600,
        time_difficulty=100, produce_item_id='maple_wood', yield_min=1, yield_max=1,
        harvest_xp=1, stamina_cost=1, quality=service.content.crops[0].quality, draws=draws,
    )


def test_all_five_unlock_at_first_breakthrough_and_report_ui_state(game):
    service, player = game
    catalog = service.partner_catalog_loader()
    for owned in player.owned_partners:
        codes = catalog.partner_map[owned.partner_id].trait_codes
        assert active_partner_trait_codes(codes, 0) == codes[:1]
        assert active_partner_trait_codes(codes, 1) == codes
        assert active_partner_trait_codes(codes, 2) == codes
        assert service._partner_trait_view(codes[-1], 0)['unlocked'] is False
        assert service._partner_trait_view(codes[-1], 1)['unlocked'] is True
        assert service._partner_trait_view(codes[-1], 0)['min_breakthrough'] == 1


def test_mami_reduces_final_duration_below_floor_and_freezes_task(game):
    service, player = game
    before = task(service, player, 'mami', 'farming')
    player.owned_partners[3].breakthrough = 1
    after = task(service, player, 'mami', 'farming')
    assert before.final_duration == 600
    assert after.final_duration == 552
    assert before.final_duration == 600


@pytest.mark.parametrize('base,expected', [(1, 2), (10, 13), (30, 39)])
def test_gathering_bonus_rounds_down_with_minimum_one(game, base, expected):
    service, player = game
    draws = GatheringDrawDefinition(base_draws=base, difficulty=100)
    before = task(service, player, 'luo_nali', 'gathering', draws)
    player.owned_partners[1].breakthrough = 1
    after = task(service, player, 'luo_nali', 'gathering', draws)
    assert before.draw_count == base
    assert after.draw_count == expected


@pytest.mark.parametrize('mode,attribute,actor,expected', [
    ('single', 'agility', 'gujian_miao', 2), ('sum', 'agility', 'gujian_miao', 0),
    ('single', 'strength', 'gujian_miao', 0), ('single', 'agility', 'leilei', 0),
])
def test_agility_bonus_only_applies_to_own_non_group_check(game, mode, attribute, actor, expected):
    service, player = game
    player.owned_partners[2].breakthrough = 1
    context = dict(industry='exploration', check_mode=mode, check_attribute=attribute,
                   check_actor_partner_ids=[actor], check_bonus=0)
    service._execute_partner_trait_phase(['gujian_miao'], 'exploration_event', context, player=player)
    assert context['check_bonus'] == expected
    context['check_bonus'] = 0
    service._execute_partner_trait_phase(['gujian_miao'], 'exploration_event', context,
                                        player=player, trait_snapshot={'gujian_miao': ['snowtrace_trick']})
    assert context['check_bonus'] == 0


def test_livestock_fraction_survives_reload_with_unchanged_feed_and_specials():
    facility = LivestockFacilityState(facility_id='test')
    params = FacilityParameters(cycle_seconds=28800, capacity=1, quality_multiplier=1,
        overflow_cycles=10, gene_cap=100, ability=0, feed_score=0, quality_gene_coefficient=0,
        affection_cap=100, affection_quality_base=1, affection_quality_per_point=0,
        normal_yield_multiplier=1.25,
        species={'test': SpeciesParameters('test', 1, 1, 1, 1, 0, special_item_id='special', special_chance=1)})
    animal = AnimalState(animal_id='a', species_id='test', facility_id='test', stage='adult', affection=100)
    for cycle in range(4):
        result = advance_facility(facility, [animal], params, 1, 1, random.Random(1))
        assert result.consumed_units == 1
        assert animal.yield_progress == ((cycle + 1) * .25) % 1
        animal = AnimalState.model_validate_json(animal.model_dump_json())
    assert animal.pending_total == 5
    assert animal.pending_special == 4
    assert params.cycle_seconds == 28800
    old = AnimalState(animal_id='b', species_id='test', facility_id='test', stage='adult', affection=100)
    result = advance_facility(facility, [old], replace(params, normal_yield_multiplier=1), 4, 4, random.Random(1))
    assert old.pending_total == 4 and old.pending_special == 4
    assert result.consumed_units == 4


def test_gathering_bonus_exceeds_base_draw_cap(game):
    service, player = game
    draws = GatheringDrawDefinition(base_draws=30, ability_bonus=5, difficulty=1)
    before = task(service, player, 'luo_nali', 'gathering', draws)
    player.owned_partners[1].breakthrough = 1
    after = task(service, player, 'luo_nali', 'gathering', draws)
    assert before.draw_count == 60
    assert after.draw_count == 78
    assert type(after).model_validate_json(after.model_dump_json()).draw_count == 78


def test_departure_freezes_unlocked_traits_and_legacy_runs_keep_base_only(game):
    service, player = game
    def prepare(state):
        state.owned_partners = player.owned_partners
        state.owned_partners[4].breakthrough = 1
        state.experience = service.content.levels[-1].total_xp
        state.coins = 100000
        state.stamina = 60
    service.repository.update(player.player_id, prepare)
    service.start_exploration('trait-test', 'spiritfruit_meadow',
                              ['leilei', 'gujian_miao', 'zhuoyan'], 'gujian_miao')
    stored = service.repository.get(player.player_id)
    run = stored.exploration_run
    assert run.partner_trait_codes['zhuoyan'] == ['wildfire_instinct', 'flame_chain']
    assert run.partner_trait_codes['gujian_miao'] == ['snowtrace_trick']
    assert service._delve_members(run)['zhuoyan'].trait_codes == ['wildfire_instinct', 'flame_chain']
    legacy = run.model_dump()
    del legacy['partner_trait_codes']
    legacy_run = type(run).model_validate(legacy)
    assert service._delve_members(legacy_run)['zhuoyan'].trait_codes == ['wildfire_instinct']
