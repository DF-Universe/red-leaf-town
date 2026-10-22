import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest
from quart import Quart

from red_leaf_town.application import GameError
from red_leaf_town.domain.models import PendingBigCatchState, PlayerState, SeasonProgressState
from red_leaf_town.summer_content import POINT_UNITS, SUMMER_ID, load_summer_event
from red_leaf_town.story_content import load_story_catalog, serialize_script
from red_leaf_town.story_assets import load_story_asset_catalog
from red_leaf_town.partner_content import load_partner_catalog
from red_leaf_town.runtime import set_service
from red_leaf_town.web.routes import create_blueprint, COOKIE_NAME
from game_host.jwt import AUD_RED_LEAF_TOWN, subject_encode
from test_seasons import event_game, begin_gathering, close_window, SPOT, SITE, SEASON


def points(game):
    p = game[1].get(game[3]).season_progress.get(SUMMER_ID)
    return p.units if p else 0


def give_points(game, amount):
    game[1].update(game[3], lambda p: p.season_progress.__setitem__(SUMMER_ID, SeasonProgressState(units=amount * POINT_UNITS)))


def test_fishing_actual_stamina_and_duplicate(event_game):
    s, repo, clock, pid, _ = event_game
    result = s.cast_line('event-sub', SPOT, 'summer-cast')
    assert points(event_game) == result['result']['stamina_cost'] * POINT_UNITS
    s.cast_line('event-sub', SPOT, 'summer-cast')
    assert points(event_game) == 4 * POINT_UNITS
    close_window(event_game)
    s.cast_line('event-sub', SPOT, 'summer-cast')
    assert points(event_game) == 4 * POINT_UNITS


def test_ordinary_cast_does_not_score(event_game):
    event_game[0].cast_line('event-sub', 'town_creek', 'normal-cast')
    assert points(event_game) == 0


@pytest.mark.parametrize('action,expected', [('fight', 3), ('release', 0)])
def test_big_catch_counts_only_spent_stamina(event_game, action, expected):
    s, repo, clock, pid, _ = event_game
    repo.update(pid, lambda p: setattr(p.fishing, 'pending_big_catch', PendingBigCatchState(spot_id=SPOT, created_at=clock[0])))
    s.resolve_big_catch('event-sub', action)
    assert points(event_game) == expected * POINT_UNITS
    with pytest.raises(GameError):
        s.resolve_big_catch('event-sub', action)
    assert points(event_game) == expected * POINT_UNITS


def test_gathering_counts_duration_once_not_waiting_or_sp_multiplier(event_game):
    task = begin_gathering(event_game)
    event_game[2][0] = task.ready_at + 500
    event_game[0].snapshot_by_sub('event-sub')
    assert points(event_game) == 0
    event_game[0].collect_gathering('event-sub', SITE)
    assert points(event_game) == task.final_duration
    with pytest.raises(GameError):
        event_game[0].collect_gathering('event-sub', SITE)
    assert points(event_game) == task.final_duration
    # A second non-hour task preserves fractional progress exactly.
    event_game[0].start_gathering('event-sub', SITE, 'gather_summer_firefly_meadow')
    next_task = next(x.task_snapshot for x in event_game[1].get(event_game[3]).gathering_sites if x.site_id == SITE)
    event_game[2][0] = next_task.ready_at
    event_game[0].collect_gathering('event-sub', SITE)
    assert points(event_game) == task.final_duration + next_task.final_duration


def test_gathering_after_event_yields_items_but_no_points(event_game):
    begin_gathering(event_game)
    close_window(event_game)
    assert event_game[0].collect_gathering('event-sub', SITE)['result']['drops']
    assert points(event_game) == 0


def start_run(game, event='boardwalk_sprint'):
    s, repo, clock, pid, _ = game
    s.start_exploration('event-sub', SEASON, ['guqi_sp', 'fein_sp', 'ai_xinyu'], 'guqi_sp')
    def setup(p):
        p.exploration_run.current_event_id = event
        p.exploration_run.current_rolls = [1, 1]
    repo.update(pid, setup)


def test_exploration_failure_and_withdraw_keep_points(event_game):
    start_run(event_game)
    result = event_game[0].resolve_exploration_event('event-sub', 'dash_across')['result']
    assert points(event_game) == result['stamina_cost'] * POINT_UNITS
    total = points(event_game)
    event_game[0].withdraw_exploration('event-sub')
    assert points(event_game) == total


def test_battle_immediate_wipe_counts_spend(event_game, monkeypatch):
    start_run(event_game)
    monkeypatch.setattr(event_game[0], '_begin_delve_battle', lambda *args: 'wiped')
    result = event_game[0].resolve_exploration_event('event-sub', 'kick_the_shells_loose')['result']
    assert result['outcome'] == 'wiped'
    assert points(event_game) == result['stamina_cost'] * POINT_UNITS
    assert event_game[1].get(event_game[3]).exploration_run is None


@pytest.mark.parametrize('terminal', ['victory', 'wiped', 'fled'])
def test_battle_scores_at_terminal_once(event_game, monkeypatch, terminal):
    from red_leaf_town.domain import delve_battle
    s, repo, clock, pid, _ = event_game
    start_run(event_game)
    monkeypatch.setattr(s, '_run_delve_enemy_turns', lambda *args: 'ongoing')
    result = s.resolve_exploration_event('event-sub', 'kick_the_shells_loose')['result']
    assert points(event_game) == 0
    def setup(p):
        battle = p.exploration_run.battle
        battle.order = ['guqi_sp', 'fein_sp', 'ai_xinyu'] + [e.key for e in battle.enemies]
        battle.turn_index = 0
    repo.update(pid, setup)
    monkeypatch.setattr(s, '_run_delve_enemy_turns', lambda *args: terminal)
    monkeypatch.setattr(delve_battle, 'member_attack', lambda *args: None)
    monkeypatch.setattr(delve_battle, 'attempt_flee', lambda *args: True)
    outcome = s.resolve_delve_battle_action('event-sub', 'flee' if terminal == 'fled' else 'attack', target='tide_hermit#0')
    assert outcome['result']['outcome'] == terminal
    assert points(event_game) == result['stamina_cost'] * POINT_UNITS
    run = repo.get(pid).exploration_run
    if run:
        assert run.season_pending_units == 0
        s.withdraw_exploration('event-sub')
        assert points(event_game) == result['stamina_cost'] * POINT_UNITS


def test_all_rewards_total_and_idempotent(event_game):
    s, repo, _, pid, _ = event_game
    give_points(event_game, 1000)
    before = repo.get(pid)
    for milestone in load_summer_event().milestones:
        s.claim_summer_reward('event-sub', milestone.points)
        assert s.claim_summer_reward('event-sub', milestone.points)['result']['duplicate']
    after = repo.get(pid)
    assert after.guide_leaves - before.guide_leaves == 20
    assert after.coins - before.coins == 20000
    assert after.inventory['miracle_crystal'][0] == 18
    assert after.inventory['partner_notes_medium'][0] == 12
    assert after.inventory['ribbed_berry_seed'][0] == 1
    assert after.inventory['night_bellflower'][3] == 10
    assert after.inventory['sea_shell'][3] == 12
    assert len(after.season_progress[SUMMER_ID].claimed_thresholds) == 10


def test_reward_rejects_insufficient_future_expired(event_game):
    s, repo, clock, pid, _ = event_game
    with pytest.raises(GameError, match='不足'):
        s.claim_summer_reward('event-sub', 100)
    give_points(event_game, 1000)
    clock[0] = 1
    with pytest.raises(GameError, match='领取期'):
        s.claim_summer_reward('event-sub', 100)
    close_window(event_game)
    s.claim_summer_reward('event-sub', 100)  # 7-day grace
    clock[0] += 7 * 86400
    with pytest.raises(GameError, match='领取期'):
        s.claim_summer_reward('event-sub', 200)


def test_story_gates_replay_and_complete_finale(event_game):
    s = event_game[0]
    assert not s.story_cue('event-sub', 'summer:summer_chapter_1')['stories']
    with pytest.raises(GameError, match='尚未解锁'):
        s.mark_story_seen('event-sub', 'summer_chapter_1')
    give_points(event_game, 1000)
    assert not s.story_cue('event-sub', 'summer:summer_chapter_1')['stories']
    for i in range(11):
        result = s.story_cue('event-sub', f'summer:summer_chapter_{i}')
        assert len(result['stories']) == 1
        s.mark_story_seen('event-sub', f'summer_chapter_{i}')
    assert s.story_cue('event-sub', 'summer:summer_chapter_1')['stories']  # replay
    close_window(event_game)
    assert s.story_cue('event-sub', 'summer:summer_chapter_5')['stories']
    assert len(s.snapshot_by_sub('event-sub')['summer_event']['chapters']) == 11
    assert s.story_cue('event-sub', 'summer:summer_chapter_10')['stories']


def test_story_before_event_is_blocked(event_game):
    event_game[2][0] = 1
    with pytest.raises(GameError, match='尚未解锁'):
        event_game[0].story_cue('event-sub', 'summer:summer_chapter_0')


def test_migration_preserves_existing_save(event_game):
    old = event_game[1].get(event_game[3]).model_dump()
    old['schema_version'] = 32
    old.pop('season_progress')
    state = PlayerState.model_validate(old)
    assert state.schema_version == PlayerState.model_fields["schema_version"].default and state.season_progress == {}
    assert state.coins == old['coins']
    assert [p.model_dump() for p in state.owned_partners] == old['owned_partners']


def test_public_scripts_preserve_share_dialogue_and_layout():
    catalog = load_story_catalog()
    assets = load_story_asset_catalog()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'docs/summer-story-import.json').read_text())
    for entry in manifest['chapters']:
        script = catalog.script_map[entry['story_id']]
        old = json.loads((root / f"data/story_shares/{entry['share_id']}.json").read_text()) if (root / f"data/story_shares/{entry['share_id']}.json").exists() else None
        new = serialize_script(script, assets, load_partner_catalog())
        assert new['mode'] == 'stage'
        assert len(new['steps']) == entry['steps']
        assert script.rewards.empty
        if entry.get('render_sha256'):
            steps = copy.deepcopy(new['steps'])
            for step in steps:
                if step.get('asset'):
                    step.pop('asset_id', None)
                    step.pop('partner_id', None)
                    step['asset'].pop('id', None)
            digest = hashlib.sha256(json.dumps(steps, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            assert digest == entry['render_sha256']
        for i, step in enumerate(new['steps']):
            if step.get('asset'):
                assert '/community/' not in step['asset']['asset_key']
            if old:
                source = old['steps'][i]
                if step['type'] == 'dialogue':
                    assert step == source
                elif step.get('asset'):
                    assert step['asset']['layouts'] == source['asset']['layouts']
                    assert step.get('layout') == source.get('layout')


def test_reward_route_auth_and_repeat(event_game):
    async def run():
        s = event_game[0]
        set_service(s)
        try:
            app = Quart(__name__)
            app.register_blueprint(create_blueprint())
            client = app.test_client()
            url = '/api/red-leaf-town/summer/rewards/100/claim'
            assert (await client.post(url)).status_code == 401
            client.set_cookie('localhost', COOKIE_NAME, subject_encode('event-sub', AUD_RED_LEAF_TOWN))
            give_points(event_game, 100)
            headers = {'X-Requested-With': 'XMLHttpRequest'}
            first = await client.post(url, headers=headers)
            assert first.status_code == 200
            second = await client.post(url, headers=headers)
            assert (await second.get_json())['data']['result']['duplicate']
        finally:
            set_service(None)
    asyncio.run(run())


def test_fractional_points_cannot_claim_early(event_game):
    s, repo, _, pid, _ = event_game
    repo.update(pid, lambda p: p.season_progress.__setitem__(SUMMER_ID, SeasonProgressState(units=100 * POINT_UNITS - 1)))
    with pytest.raises(GameError, match='不足'):
        s.claim_summer_reward('event-sub', 100)
    repo.update(pid, lambda p: setattr(p.season_progress[SUMMER_ID], 'units', 100 * POINT_UNITS))
    assert not s.claim_summer_reward('event-sub', 100)['result']['duplicate']


def test_cancelled_gathering_never_scores(event_game):
    begin_gathering(event_game)
    event_game[0].cancel_task('event-sub', 'gathering', SITE)
    assert points(event_game) == 0


@pytest.mark.parametrize("chapter", range(6, 11))
def test_late_summer_chapters_require_points_and_previous_read(event_game, chapter):
    s, repo, _, pid, _ = event_game
    story_id = f"summer_chapter_{chapter}"
    previous = f"summer_chapter_{chapter - 1}"
    give_points(event_game, chapter * 100)
    assert not s.story_cue('event-sub', 'summer:' + story_id)['stories']
    with pytest.raises(GameError, match='尚未解锁'):
        s.mark_story_seen('event-sub', story_id)
    repo.update(pid, lambda p: p.seen_story_ids.append(previous))
    give_points(event_game, chapter * 100 - 1)
    assert not s.story_cue('event-sub', 'summer:' + story_id)['stories']
    with pytest.raises(GameError, match='尚未解锁'):
        s.mark_story_seen('event-sub', story_id)
    give_points(event_game, chapter * 100)
    result = s.story_cue('event-sub', 'summer:' + story_id)
    assert len(result['stories']) == 1
    s.mark_story_seen('event-sub', story_id)
    assert story_id in repo.get(pid).seen_story_ids
