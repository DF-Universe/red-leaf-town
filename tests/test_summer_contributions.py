"""Production integration regressions for community PRs #2 and #3."""
import asyncio
from types import SimpleNamespace

import pytest
from quart import Quart

from red_leaf_town.application import GameError
from red_leaf_town.application.summer_skins import load_skin_catalog, skin_artworks, VIEW_PREFIX
from red_leaf_town.domain import PlayerState, DelveBattleState, DelveLoadoutSnapshot
from red_leaf_town.domain import delve_battle
from red_leaf_town.domain.economy import add_item
from red_leaf_town.runtime import set_service
from red_leaf_town.web.routes import create_blueprint, COOKIE_NAME
from game_host.jwt import subject_encode, AUD_RED_LEAF_TOWN
from test_seasons import event_game, close_window
from test_delve_battle import battle, member, FixedRandom

DUNGEON = 'midsummer_appeal_ground'
PARTY = ['guqi_sp', 'fein_sp', 'ai_xinyu']
SKIN = 'ai_xinyu_swimsuit'


def fund_skin(game):
    skin = next(x for x in load_skin_catalog()['skins'] if x['id'] == SKIN)
    def fund(p):
        for cost in skin['cost']:
            add_item(p, cost['item_id'], cost['quantity'] * 2)
    game[1].update(game[3], fund)
    return skin


def test_skin_redemption_is_atomic_idempotent_and_persists_gallery(event_game):
    s, repo, _, pid, _ = event_game
    skin = fund_skin(event_game)
    assert not s.claim_summer_skin('event-sub', SKIN)['result']['duplicate']
    after = repo.get(pid).inventory
    assert s.claim_summer_skin('event-sub', SKIN)['result']['duplicate']
    assert repo.get(pid).inventory == after
    assert all(sum(after[c['item_id']].values()) == c['quantity'] for c in skin['cost'])
    s.select_partner_artwork('event-sub', 'ai_xinyu', 100)
    restored = PlayerState.model_validate_json(repo.get(pid).model_dump_json())
    assert next(p for p in restored.owned_partners if p.partner_id == 'ai_xinyu').artwork_stage != 100
    record = next(p for p in s._partner_snapshots(restored) if p['partner_id'] == 'ai_xinyu')
    assert record['artwork']['skin_id'] == SKIN
    assert record['artwork_stage'] == 100
    s.select_partner_artwork('event-sub', 'ai_xinyu', 0)
    assert VIEW_PREFIX + 'ai_xinyu' not in repo.get(pid).crossover_claims


def test_skin_missing_materials_and_closed_event_do_not_consume(event_game):
    s, repo, _, pid, _ = event_game
    before = repo.get(pid).inventory
    with pytest.raises(GameError, match='材料不足'):
        s.claim_summer_skin('event-sub', SKIN)
    assert repo.get(pid).inventory == before
    fund_skin(event_game)
    close_window(event_game)
    before = repo.get(pid).inventory
    with pytest.raises(GameError, match='不在进行中'):
        s.claim_summer_skin('event-sub', SKIN)
    assert repo.get(pid).inventory == before


def test_locked_skin_selection_does_not_change_save(event_game):
    s, repo, _, pid, _ = event_game
    with pytest.raises(GameError, match='尚未解锁'):
        s.select_partner_artwork('event-sub', 'ai_xinyu', 100)
    assert VIEW_PREFIX + 'ai_xinyu' not in repo.get(pid).crossover_claims


def test_skin_route_auth_and_repeated_redemption(event_game):
    fund_skin(event_game)
    async def run():
        set_service(event_game[0])
        try:
            app = Quart(__name__)
            app.register_blueprint(create_blueprint())
            client = app.test_client()
            url = '/api/red-leaf-town/summer/skins/' + SKIN + '/claim'
            assert (await client.post(url)).status_code == 401
            client.set_cookie('localhost', COOKIE_NAME, subject_encode('event-sub', AUD_RED_LEAF_TOWN))
            headers = {'X-Requested-With': 'XMLHttpRequest'}
            response = await client.post(url, headers=headers)
            assert response.status_code == 200
            response = await client.post(url, headers=headers)
            assert (await response.get_json())['data']['result']['duplicate']
        finally:
            set_service(None)
    asyncio.run(run())


def start_dungeon(game):
    s, repo, _, pid, _ = game
    repo.update(pid, lambda p: setattr(p, 'coins', 0))
    s.start_exploration('event-sub', DUNGEON, PARTY, 'guqi_sp')
    return s, repo, pid


def test_free_dungeon_save_roundtrip_and_single_atomic_first_clear(event_game, monkeypatch):
    s, repo, pid = start_dungeon(event_game)
    state = PlayerState.model_validate_json(repo.get(pid).model_dump_json())
    assert state.exploration_run.entry_fee == 0 and state.coins == 0
    before_leaves = state.guide_leaves
    def complete(p):
        p.exploration_run.status = 'completed'
        p.exploration_run.battle = None
    repo.update(pid, complete)
    original = repo.update
    commits = []
    def record(player_id, mutation):
        result = original(player_id, mutation)
        commits.append(repo.get(player_id))
        return result
    monkeypatch.setattr(repo, 'update', record)
    result = s.withdraw_exploration('event-sub')
    assert len(commits) == 1
    assert result['result']['guide_leaves'] == 2
    assert commits[0].guide_leaves == before_leaves + 2
    assert commits[0].exploration_run is None
    assert DUNGEON in commits[0].achievement_stats.completed_expedition_ids
    assert DUNGEON not in [x['id'] for x in result['state']['exploration']['expeditions']]
    with pytest.raises(GameError):
        s.withdraw_exploration('event-sub')
    with pytest.raises(GameError, match='已经通关'):
        s.start_exploration('event-sub', DUNGEON, PARTY, 'guqi_sp')
    assert repo.get(pid).guide_leaves == before_leaves + 2


def test_early_withdraw_does_not_grant_or_lock_first_clear(event_game):
    s, repo, pid = start_dungeon(event_game)
    before = repo.get(pid).guide_leaves
    result = s.withdraw_exploration('event-sub')
    assert result['result']['guide_leaves'] == 0
    assert repo.get(pid).guide_leaves == before
    s.start_exploration('event-sub', DUNGEON, PARTY, 'guqi_sp')


def test_boss_phase_survives_save_reload_and_warns_before_next_burst():
    state = battle(enemy_hp=110)
    state.enemies[0].enemy_id = 'midsummer_forum_king'
    state.enemies[0].hp = 55
    state.round = 4
    members = {'hero': member('hero', hp=1000)}
    attacks = [{'to_hit': 1, 'damage_dice': '1d4', 'damage_bonus': 0, 'name': '普通攻击'}]
    delve_battle.enemy_turn(FixedRandom([]), state, state.enemies[0], attacks, members, 2)
    assert state.enemies[0].phase_started_round == 4
    assert '暖粉何在' in state.logs[-1].text
    state = DelveBattleState.model_validate_json(state.model_dump_json())
    for round_, expected in [(5, 2), (6, 2), (7, 1)]:
        state.round = round_
        previous = len(state.logs)
        delve_battle.enemy_turn(FixedRandom([]), state, state.enemies[0], attacks, members, 2)
        assert len(state.logs) - previous == expected
        assert '暖粉何在' not in state.logs[-1].text
        if round_ == 6:
            assert '要放大招了' in state.logs[-1].text


@pytest.mark.parametrize('actor,mode,expected', [('', 'highest', 'intelligence'), ('guqi_sp', 'highest', 'agility'), ('fein_sp', 'highest', 'intelligence'), ('guqi_sp', 'sum', 'intelligence')])
def test_signature_weapon_requires_explicit_sp_selection(event_game, actor, mode, expected):
    s = event_game[0]
    run = SimpleNamespace(partner_ids=PARTY, loadout={'guqi_sp': DelveLoadoutSnapshot(weapon_item_id='spotlight_mic_blade')})
    choice = SimpleNamespace(check=SimpleNamespace(attribute='intelligence', mode=mode, dice='normal', dc=14))
    context = {'selected_actor_partner_id': actor}
    s._prime_exploration_check_context(run, choice, context)
    assert context['check_attribute'] == expected
    assert s._exploration_check_preview(run, choice, context)['check_attribute'] == expected


def test_signature_weapon_rejects_non_owner(event_game):
    s, repo, _, pid, _ = event_game
    repo.update(pid, lambda p: add_item(p, 'spotlight_mic_blade', 1))
    with pytest.raises(GameError, match='认主'):
        s._build_delve_loadout(repo.get(pid), PARTY, {'fein_sp': {'weapon_item_id': 'spotlight_mic_blade'}})
    assert s._build_delve_loadout(repo.get(pid), PARTY, {'guqi_sp': {'weapon_item_id': 'spotlight_mic_blade'}})['guqi_sp'].weapon_item_id == 'spotlight_mic_blade'
