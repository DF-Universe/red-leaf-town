from datetime import datetime
import json
import random

import pytest
from pydantic import ValidationError

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain.models import CarriedItemSnapshot, PlayerState, ProductionResultSnapshot
from red_leaf_town.domain.season import scale_quantities
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_traits import execute_partner_traits
from red_leaf_town.season_content import DEFAULT_SEASONS_PATH, SeasonCatalog, load_seasons

SEASON = 'summer_tide_festival'
SITE = 'summer_firefly_meadow'
TASK = 'gather_summer_firefly_meadow'
SPOT = 'summer_lantern_bay'


@pytest.fixture
def event_game(tmp_path):
    schedule = json.loads(DEFAULT_SEASONS_PATH.read_text())
    entry = schedule['seasons'][0]
    entry['starts_at'] = '2026-09-01T00:00:00+08:00'
    entry['ends_at'] = '2026-10-01T00:00:00+08:00'
    path = tmp_path / 'seasons.json'
    path.write_text(json.dumps(schedule))
    clock = [int(datetime.fromisoformat('2026-09-29T00:00:00+08:00').timestamp())]
    content = load_content().model_copy(deep=True)
    repo = InMemoryPlayerRepository(content)
    service = GameService(content, repo, clock=lambda: clock[0], rng=random.Random(9),
                          season_loader=lambda: load_seasons(path))
    player = service.ensure_player('event-sub', '活动测试')
    def prepare(state):
        state.experience = content.levels[-1].total_xp
        state.coins = 100000
        state.stamina = 68
    repo.update(player.player_id, prepare)
    for partner in ['guqi_sp', 'fein_sp', 'ai_xinyu']:
        service.admin_grant_partner(player.player_id, partner)
    return service, repo, clock, player.player_id, path


def begin_gathering(game):
    service, repo, clock, pid, _ = game
    service.assign_gathering_partner('event-sub', SITE, 'fein_sp')
    service.start_gathering('event-sub', SITE, TASK)
    site = next(x for x in repo.get(pid).gathering_sites if x.site_id == SITE)
    return site.task_snapshot


def close_window(game):
    game[2][0] = int(datetime.fromisoformat('2026-10-01T00:00:00+08:00').timestamp())


@pytest.mark.parametrize('failure', ['future', 'ended', 'missing', 'malformed', 'unknown'])
def test_closed_or_invalid_schedule_hides_and_blocks_event_content(event_game, failure):
    service, repo, clock, pid, path = event_game
    if failure == 'future':
        clock[0] = 1_700_000_000
    elif failure == 'ended':
        close_window(event_game)
    elif failure == 'missing':
        path.unlink()
    elif failure == 'malformed':
        path.write_text('{broken')
    else:
        path.write_text('{"seasons": []}')
    state = service.snapshot_by_sub('event-sub')
    assert SITE not in [x['site_id'] for x in state['gathering_sites']]
    assert SEASON not in [x['id'] for x in state['exploration']['expeditions']]
    assert SPOT not in [x['id'] for x in state['aquatic']['spots']]
    assert state['aquatic']['codex']['total'] == 9
    for operation in [
        lambda: service.start_gathering('event-sub', SITE, TASK),
        lambda: service.cast_line('event-sub', SPOT, 'blocked-cast'),
        lambda: service.start_exploration('event-sub', SEASON, ['guqi_sp', 'fein_sp', 'ai_xinyu'], 'guqi_sp'),
    ]:
        with pytest.raises(GameError, match='活动'):
            operation()
    assert repo.get(pid).coins == 100000
    # Ordinary content remains usable even if the schedule is broken.
    service.cast_line('event-sub', 'town_creek', 'ordinary-cast')


def test_open_schedule_exposes_notes_but_never_fishing_outputs(event_game):
    service = event_game[0]
    state = service.snapshot_by_sub('event-sub')
    spot = next(x for x in state['aquatic']['spots'] if x['id'] == SPOT)
    assert spot['event_note'] and spot['event_badge']
    assert 'outputs' not in spot and 'big_catch' not in spot
    assert next(x for x in state['gathering_sites'] if x['site_id'] == SITE)['event_note']
    assert next(x for x in state['exploration']['expeditions'] if x['id'] == SEASON)['leader_note']


@pytest.mark.parametrize('refresh_first', [False, True])
def test_gathering_bonus_frozen_and_collectable_after_closure(event_game, refresh_first):
    service, repo, clock, pid, path = event_game
    task = begin_gathering(event_game)
    # Resolve an identical unboosted task with the same RNG as the expected baseline.
    baseline = next(x for x in repo.get(pid).gathering_sites if x.site_id == SITE).model_copy(deep=True)
    baseline.task_snapshot.season_bonus = None
    rng_state = service.rng.getstate()
    service._resolve_gathering_outputs(baseline, task.ready_at)
    expected = scale_quantities([x.quantity for x in baseline.task_results], 1.2)
    service.rng.setstate(rng_state)
    # Changing the reward configuration or moving the partner cannot alter a running task.
    path.write_text('{"seasons": []}')
    close_window(event_game)
    service.assign_gathering_partner('event-sub', SITE, '')
    if refresh_first:
        state = service.snapshot_by_sub('event-sub')
        site = next(x for x in state['gathering_sites'] if x['site_id'] == SITE)
        assert site['ready'] and site['available_tasks'] == []
    result = service.collect_gathering('event-sub', SITE)
    assert [x['quantity'] for x in result['result']['drops']] == expected
    assert result['result']['season_bonus']['partner_id'] == 'fein_sp'
    assert SITE not in [x['site_id'] for x in result['state']['gathering_sites']]
    inventory = repo.get(pid).inventory
    with pytest.raises(GameError):
        service.collect_gathering('event-sub', SITE)
    assert repo.get(pid).inventory == inventory


def test_direct_collect_without_any_settlement_gets_bonus(event_game):
    service, repo, clock, pid, _ = event_game
    task = begin_gathering(event_game)
    assert next(x for x in repo.get(pid).gathering_sites if x.site_id == SITE).task_results == []
    clock[0] = task.ready_at
    result = service.collect_gathering('event-sub', SITE)['result']
    assert result['season_bonus']['multiplier'] == 1.2
    assert sum(x['quantity'] for x in result['drops']) >= task.draw_count


def test_fishing_bonus_atomic_inventory_codex_and_duplicate_request(event_game):
    service, repo, clock, pid, _ = event_game
    service.assign_fishing_companion('event-sub', 'ai_xinyu')
    # Deterministic ordinary fish, so no big-catch pending state can obscure the assertion.
    output = service.content.fishing_spot_map[SPOT].outputs[0]
    service._pick_fishing_entry = lambda pool: (output, False)
    result = service.cast_line('event-sub', SPOT, 'one-cast')
    drops = result['result']['drops']
    assert result['result']['companion_bonus']['multiplier'] == 1.2
    total = sum(x['quantity'] for x in drops)
    assert total == sum(scale_quantities([1] * result['result']['draws'], 1.2))
    inventory = repo.get(pid).inventory
    assert sum(inventory[output.item_id].values()) == total
    assert sum(x['quantity'] for x in result['state']['inventory'] if x['item_id'] == output.item_id) == total
    assert repo.get(pid).fish_codex.entries[0].caught == total
    close_window(event_game)
    duplicate = service.cast_line('event-sub', SPOT, 'one-cast')
    assert duplicate['result']['duplicate']
    assert repo.get(pid).inventory == inventory
    assert output.item_id in [x['item_id'] for x in duplicate['state']['aquatic']['codex']['pool']]


def test_exploration_bonus_frozen_return_excludes_fixed_rewards(event_game):
    service, repo, clock, pid, path = event_game
    service.start_exploration('event-sub', SEASON, ['guqi_sp', 'fein_sp', 'ai_xinyu'], 'guqi_sp')
    def rewards(player):
        run = player.exploration_run
        run.pending_rewards = [ProductionResultSnapshot(item_id='sea_shell', quantity=10, quality=2, resolved_at=clock[0])]
        run.pending_fixed_rewards = [CarriedItemSnapshot(item_id='ribbed_berry_seed', quantity=1, quality=0)]
    repo.update(pid, rewards)
    close_window(event_game)
    path.write_text('{"seasons": []}')
    result = service.withdraw_exploration('event-sub')['result']
    assert result['drops'][0]['quantity'] == 12
    assert result['equipment_drops'][0]['quantity'] == 1
    assert result['leader_bonus']['partner_id'] == 'guqi_sp'
    with pytest.raises(GameError):
        service.withdraw_exploration('event-sub')
    assert repo.get(pid).inventory['sea_shell'][2] == 12


def test_old_task_migration_does_not_gain_event_bonus(event_game):
    task = begin_gathering(event_game)
    player = event_game[1].get(event_game[3]).model_dump()
    player['schema_version'] = 31
    for site in player['gathering_sites']:
        if site['task_snapshot']:
            site['task_snapshot'].pop('season_bonus')
    migrated = PlayerState.model_validate(player)
    assert migrated.schema_version == PlayerState.model_fields["schema_version"].default
    assert next(x for x in migrated.gathering_sites if x.site_id == SITE).task_snapshot.season_bonus is None


@pytest.mark.parametrize('dates', [
    ('2026-10-01T00:00:00+08:00', '2026-09-01T00:00:00+08:00'),
    ('2026-09-01T00:00:00', '2026-10-01T00:00:00'),
])
def test_schedule_requires_ordered_timezone_aware_dates(dates):
    with pytest.raises(ValidationError):
        SeasonCatalog.model_validate({'seasons': [{'id': 'test', 'name': 'test', 'starts_at': dates[0], 'ends_at': dates[1]}]})


@pytest.mark.parametrize('phase,industry,action,field,amount', [
    ('task_prepare', 'gathering', '', 'quality_ability_bonus', 25),
    ('instant_action', 'aquatic', 'fishing_cast', 'quality_ability_bonus', 25),
    ('asset_prepare', 'aquatic', 'pond_segment', 'quality_bonus', 25),
    ('output_draw', 'gathering', '', 'draw_bonus', 1),
    ('task_prepare', 'mining', '', 'quality_ability_bonus', 0),
])
def test_summer_mood_uses_real_industry_phases(phase, industry, action, field, amount):
    context = {'source_partner_id': 'fein_sp', 'phase': phase, 'industry': industry, 'action': action, 'world': {'weather': {'id': 'sunny'}}}
    execute_partner_traits(['summer_mood'], context)
    assert context.get(field, 0) == amount


def test_stage_presence_requires_leader_and_stacks_agility_bonus():
    for leader, attribute, expected in [('guqi_sp', 'agility', 2), ('guqi_sp', 'strength', 1), ('other', 'agility', 0)]:
        context = {'industry': 'exploration', 'phase': 'exploration_event', 'action': 'exploration_check', 'check_attribute': attribute,
                   'leader_partner_id': leader, 'source_partner_id': 'guqi_sp'}
        execute_partner_traits(['stage_presence'], context)
        assert context.get('check_bonus', 0) == expected


def test_cancel_closed_task_and_release_partner(event_game):
    service, repo, clock, pid, path = event_game
    begin_gathering(event_game)
    path.write_text('{"seasons": []}')
    service.cancel_task('event-sub', 'gathering', SITE)
    service.assign_gathering_partner('event-sub', SITE, '')
    state = service.snapshot_by_sub('event-sub')
    assert SITE not in [x['site_id'] for x in state['gathering_sites']]


def test_exploration_continues_after_closure_and_snapshot_reload(event_game):
    service, repo, clock, pid, path = event_game
    service.start_exploration('event-sub', SEASON, ['guqi_sp', 'fein_sp', 'ai_xinyu'], 'guqi_sp')
    def freeze_event(player):
        player.exploration_run.current_event_id = 'boardwalk_sprint'
        # Exercise JSON persistence of the frozen bonus, as Redis does.
        player.exploration_run = type(player.exploration_run).model_validate_json(player.exploration_run.model_dump_json())
    repo.update(pid, freeze_event)
    close_window(event_game)
    state = service.snapshot_by_sub('event-sub')
    assert state['exploration']['active_run'] is not None
    service.resolve_exploration_event('event-sub', 'walk_the_rail')
    result = service.withdraw_exploration('event-sub')['result']
    assert result['depth'] == 1
    assert result['leader_bonus']['multiplier'] == 1.2


def test_window_boundaries_and_ineligible_partners(event_game):
    catalog = load_seasons(event_game[4])
    season = catalog[SEASON]
    assert not season.is_open(int(season.starts_at.timestamp()) - 1)
    assert season.is_open(int(season.starts_at.timestamp()))
    assert not season.is_open(int(season.ends_at.timestamp()))
    assert season.bonus_snapshot('leader_bonus', ['fein_sp']) is None
    assert season.bonus_snapshot('companion_bonus', ['fein_sp']) is None
    assert season.bonus_snapshot('gathering_bonus', ['ai_xinyu']) is None


def test_shipped_activity_links_and_rewards_are_valid():
    from red_leaf_town.partner_content import load_partner_catalog
    from red_leaf_town.gacha_pools import load_gacha_pools
    content = load_content()
    catalog = load_partner_catalog()
    seasons = load_seasons()
    for definition in [*content.exploration_expeditions, *content.fishing_spots,
                       *content.gathering_sites, *content.gathering_tasks]:
        if definition.season_id:
            assert definition.season_id in seasons
    for season in seasons.values():
        for bonus in [season.leader_bonus, season.companion_bonus, season.gathering_bonus]:
            if bonus:
                assert bonus.partner_id in catalog.partner_map
    for partner_id in ['guqi_sp', 'fein_sp']:
        assert not catalog.partner_map[partner_id].standard_recruitable
        pools = [pool for pool in load_gacha_pools().values() if partner_id in pool.partner_ids]
        assert len(pools) == 1
        assert pools[0].featured_partner_id == partner_id
        assert pools[0].season_id == SEASON


def test_gathering_task_cannot_bypass_site_schedule():
    from red_leaf_town.content import GameContent
    data = load_content().model_dump()
    next(task for task in data['gathering_tasks'] if task['id'] == TASK)['season_id'] = ''
    with pytest.raises(ValidationError, match="site's season"):
        GameContent.model_validate(data)


@pytest.mark.parametrize('pool_id,partner_id', [('guqi-sp-up-1','guqi_sp'),('fein-sp-up-1','fein_sp')])
def test_summer_up_pool_visibility_rate_and_expired_request_replay(event_game, pool_id, partner_id):
    service, repo, clock, pid, path = event_game
    state = service.snapshot_by_sub('event-sub')
    pool = next(pool for pool in state['gacha_pools'] if pool['pool_id'] == pool_id)
    assert pool['featured_partner_id'] == partner_id
    assert pool['featured_rate'] == .8
    assert pool['background']['asset_key']
    assert pool['background']['id'] == partner_id + '_gacha'
    assert {entry['rarity'] for entry in pool['catalog']} == {3, 4, 5}
    other = 'fein_sp' if partner_id == 'guqi_sp' else 'guqi_sp'
    assert other not in {entry['partner_id'] for entry in pool['catalog']}
    repo.update(pid, lambda player: setattr(player, 'guide_leaves', 20))
    original = service.recruit('event-sub', 1, 'summer-test-pull', pool_id)
    leaves = repo.get(pid).guide_leaves
    close_window(event_game)
    assert pool_id not in [pool['pool_id'] for pool in service.snapshot_by_sub('event-sub')['gacha_pools']]
    with pytest.raises(GameError, match='活动'):
        service.recruit('event-sub', 1, 'summer-next-pull', pool_id)
    assert repo.get(pid).guide_leaves == leaves
    replayed = service.recruit('event-sub', 1, 'summer-test-pull', pool_id)
    assert replayed['result']['replayed']
    assert repo.get(pid).guide_leaves == leaves
    assert replayed['result']['results'] == original['result']['results']
