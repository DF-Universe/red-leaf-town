from __future__ import annotations

import json
from uuid import uuid4

import pytest

from red_leaf_town.application import GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain import (
    CommissionBoardEntry,
    CommissionPayout,
    OwnedPartnerState,
    PlayerState,
    QQIdentity,
)
from red_leaf_town.domain import RedemptionCode
from red_leaf_town.infrastructure import (
    RedisCommissionBoard,
    RedisPlayerRepository,
    RedisRedemptionCodes,
)
from red_leaf_town.partner_content import load_partner_catalog
from game_host.redis import redis_global


@pytest.fixture
def repository():
    prefix = f"test:rlt:{uuid4().hex}:"

    class IsolatedRedisRepository(RedisPlayerRepository):
        PREFIX = prefix

    instance = IsolatedRedisRepository(redis_global, load_content())
    yield instance
    for key in redis_global.scan_iter(match=f"{prefix}*"):
        redis_global.delete(key)


def test_oauth_player_actions_and_openid_binding_persist(repository):
    service = GameService(load_content(), repository, clock=lambda: 1_700_000_000)
    first = service.ensure_player("redis-oauth-sub", "小叶")
    second = service.ensure_player("redis-oauth-sub", "小叶子")
    assert second.player_id == first.player_id

    service.buy("redis-oauth-sub", "carrot_seed", 2)
    reloaded = repository.get(first.player_id)
    assert reloaded.inventory["carrot_seed"][0] == 2
    assert reloaded.coins < first.coins

    identity = QQIdentity(platform="QQ", bot_id="official-bot", subject="opaque-openid")
    code = service.create_binding_code("redis-oauth-sub")
    service.bind_identity(code, identity)
    assert repository.player_id_for_identity(identity) == first.player_id
    assert repository.binding_labels(first.player_id) == ["QQ Bot"]

    repository.update(
        first.player_id,
        lambda player: player.owned_partners.append(
            OwnedPartnerState(partner_id="partner_test", acquired_at=1_700_000_000),
        ),
    )
    repository.update(
        first.player_id,
        lambda player: setattr(player.plots[0], "assigned_partner_ids", ["partner_test"]),
    )
    reloaded = repository.get(first.player_id)
    assert [partner.partner_id for partner in reloaded.owned_partners] == ["partner_test"]
    assert reloaded.plots[0].assigned_partner_ids == ["partner_test"]


def test_schema_two_spirit_warehouse_is_rewritten_with_partner_fields(repository):
    player_id = "legacy-partner-player"
    repository.redis.set(repository._player_key(player_id), json.dumps({
        "schema_version": 2,
        "player_id": player_id,
        "oauth_sub": "legacy-partner-sub",
        "display_name": "旧居民",
        "stamina_updated_at": 1,
        "created_at": 1,
        "updated_at": 1,
        "owned_spirits": [{"spirit_id": "maple_sprite", "acquired_at": 2}],
    }))

    loaded = repository.get(player_id)
    assert loaded.schema_version == PlayerState.model_fields["schema_version"].default
    assert loaded.owned_partners[0].partner_id == "maple_sprite"

    repository.update(player_id, lambda player: None)
    stored = json.loads(repository.redis.get(repository._player_key(player_id)))
    assert stored["schema_version"] == PlayerState.model_fields["schema_version"].default
    assert stored["owned_partners"][0]["partner_id"] == "maple_sprite"
    assert "owned_spirits" not in stored


def test_gathering_assignment_and_task_snapshot_persist(repository):
    service = GameService(load_content(), repository, clock=lambda: 1_700_000_000)
    player = service.ensure_player("redis-gathering-sub", "林间居民")
    service.admin_grant_partner(player.player_id, "fein")
    service.snapshot_by_sub("redis-gathering-sub")
    service.assign_gathering_partner("redis-gathering-sub", "maple_forest", "fein")
    service.start_gathering("redis-gathering-sub", "maple_forest", "collect_maple_wood")

    reloaded = repository.get(player.player_id)
    assert reloaded.schema_version == PlayerState.model_fields["schema_version"].default
    assert reloaded.gathering_sites[0].assigned_partner_ids == ["fein"]
    assert reloaded.gathering_sites[0].task_snapshot.industry == "gathering"
    # 伙伴倾向数值随平衡改动，这里只校验快照落的是当前配置算出来的能力，不锁死具体数字。
    fein = load_partner_catalog().partner_map["fein"]
    assert reloaded.gathering_sites[0].task_snapshot.quality_parameters.ability == fein.ability_at(
        "gathering", 1, fein.rarity
    )
    assert reloaded.gathering_sites[0].task_snapshot.minimum_duration == 8 * 3600
    assert [output.item_id for output in reloaded.gathering_sites[0].task_snapshot.output_pool] == [
        "maple_wood",
        "woodland_mushroom",
        "maple_resin",
        "amber_beeswax",
    ]


def test_crafting_inputs_and_task_snapshot_persist_atomically(repository):
    from red_leaf_town.domain.economy import add_item

    service = GameService(load_content(), repository, clock=lambda: 1_700_000_000)
    player = service.ensure_player("redis-crafting-sub", "工坊居民")
    repository.update(player.player_id, lambda state: setattr(state, "experience", 60))
    repository.update(player.player_id, lambda state: add_item(state, "maple_wood", 2, 2))
    service.start_crafting("redis-crafting-sub", "town_workbench", "saw_maple_plank")

    reloaded = repository.get(player.player_id)
    assert reloaded.schema_version == PlayerState.model_fields["schema_version"].default
    assert "maple_wood" not in reloaded.inventory
    task = reloaded.crafting_stations[0].task_snapshot
    assert task.industry == "crafting"
    assert [entry.model_dump() for entry in task.consumed_inputs] == [
        {"item_id": "maple_wood", "quality": 2, "quantity": 2},
    ]


def test_mining_site_and_task_snapshot_persist(repository):
    service = GameService(load_content(), repository, clock=lambda: 1_700_000_000)
    player = service.ensure_player("redis-mining-sub", "矿山居民")
    repository.update(player.player_id, lambda state: setattr(state, "experience", 20))
    service.start_mining("redis-mining-sub", "copper_foothill", "mine_red_copper")

    reloaded = repository.get(player.player_id)
    assert reloaded.schema_version == PlayerState.model_fields["schema_version"].default
    assert reloaded.mining_sites[0].site_id == "copper_foothill"
    assert reloaded.mining_sites[0].task_snapshot.industry == "mining"


@pytest.fixture
def board():
    prefix = f"test:rlt:commission:{uuid4().hex}:"

    class IsolatedBoard(RedisCommissionBoard):
        PREFIX = prefix

    instance = IsolatedBoard(redis_global)
    yield instance
    for key in redis_global.scan_iter(match=f"{prefix}*"):
        redis_global.delete(key)


def board_entry(commission_id="commission-abcdef01", owner_id="owner-1"):
    return CommissionBoardEntry(
        commission_id=commission_id,
        day="2026-08-24",
        owner_id=owner_id,
        owner_name="枫一",
        item_id="carrot",
        quantity=5,
        tier=1,
        reward_maple_flame=100,
        owner_reward=80,
        taker_reward=40,
        forwarded_at=1_700_000_000,
    )


def payout_for(entry, taker_name="枫二"):
    return CommissionPayout(
        commission_id=entry.commission_id,
        day=entry.day,
        maple_flame=entry.owner_reward,
        taker_name=taker_name,
        item_id=entry.item_id,
        quantity=entry.quantity,
        completed_at=1_700_000_100,
    )


def test_commission_board_round_trips_through_redis(board):
    entry = board_entry()
    board.publish(entry)

    assert board.get(entry.day, entry.commission_id) == entry
    assert [item.commission_id for item in board.list_open(entry.day)] == [entry.commission_id]
    assert board.list_open(entry.day, exclude_player_id=entry.owner_id) == []


def test_only_the_first_taker_wins_the_same_commission(board):
    entry = board_entry()
    board.publish(entry)

    first = board.take(entry.day, entry.commission_id, "helper-1", payout_for(entry))
    second = board.take(entry.day, entry.commission_id, "helper-2", payout_for(entry))

    assert first is not None
    assert second is None
    assert board.list_open(entry.day) == []
    assert [item.maple_flame for item in board.drain_payouts(entry.owner_id)] == [entry.owner_reward]
    assert board.drain_payouts(entry.owner_id) == []


def test_releasing_a_take_puts_the_commission_back_and_cancels_the_payout(board):
    entry = board_entry()
    board.publish(entry)
    payout = payout_for(entry)
    board.take(entry.day, entry.commission_id, "helper-1", payout)

    board.release(entry.day, entry.commission_id, "helper-1", payout)

    assert [item.commission_id for item in board.list_open(entry.day)] == [entry.commission_id]
    assert board.drain_payouts(entry.owner_id) == []


def test_withdrawing_fails_once_the_commission_was_taken(board):
    entry = board_entry()
    board.publish(entry)

    assert board.withdraw(entry.day, entry.commission_id, "someone-else") is None
    board.take(entry.day, entry.commission_id, "helper-1", payout_for(entry))
    assert board.withdraw(entry.day, entry.commission_id, entry.owner_id) is None


def test_restoring_payouts_keeps_them_for_the_next_read(board):
    entry = board_entry()
    payouts = [payout_for(entry)]

    board.restore_payouts(entry.owner_id, payouts)

    assert board.drain_payouts(entry.owner_id) == payouts


@pytest.fixture
def codes():
    prefix = f"test:rlt:code:{uuid4().hex}:"

    class IsolatedRedisCodes(RedisRedemptionCodes):
        PREFIX = prefix

    instance = IsolatedRedisCodes(redis_global)
    yield instance
    for key in redis_global.scan_iter(match=f"{prefix}*"):
        redis_global.delete(key)


def make_code(value: str) -> RedemptionCode:
    return RedemptionCode(code=value, batch="redis", created_at=1_700_000_000)


def test_redemption_codes_survive_a_round_trip(codes):
    assert codes.create([make_code("AAAA-1111"), make_code("BBBB-2222")]) == 2
    assert codes.create([make_code("AAAA-1111")]) == 0

    stored = codes.get("AAAA-1111")
    assert stored is not None and stored.batch == "redis" and stored.redeemed is False
    assert {entry.code for entry in codes.list_recent()} == {"AAAA-1111", "BBBB-2222"}


def test_only_the_first_claim_on_a_code_wins(codes):
    codes.create([make_code("CCCC-3333")])

    assert codes.claim("CCCC-3333", "player-a", 1_700_000_100) is not None
    assert codes.claim("CCCC-3333", "player-b", 1_700_000_200) is None

    stored = codes.get("CCCC-3333")
    assert stored.redeemed_by == "player-a"
    assert stored.redeemed_at == 1_700_000_100
    assert codes.list_recent()[0].redeemed_by == "player-a"


def test_releasing_a_claim_puts_the_code_back(codes):
    """兑换失败（比如撞上 180 天上限）时要能把码放回去，而且只有占码的人放得回去。"""
    codes.create([make_code("DDDD-4444")])
    codes.claim("DDDD-4444", "player-a", 1_700_000_100)

    codes.release("DDDD-4444", "player-b")
    assert codes.get("DDDD-4444").redeemed_by == "player-a"

    codes.release("DDDD-4444", "player-a")
    assert codes.get("DDDD-4444").redeemed is False
    assert codes.claim("DDDD-4444", "player-b", 1_700_000_300) is not None


def test_claiming_a_code_that_does_not_exist(codes):
    assert codes.claim("EEEE-5555", "player-a", 1_700_000_100) is None


def test_sailing_snapshot_and_claim_survive_service_reload(repository, monkeypatch):
    from red_leaf_town.domain.economy import add_item
    service = GameService(load_content(), repository, clock=lambda: 1_700_000_000)
    player = service.ensure_player('redis-sailing-sub', '航海持久化测试')
    monkeypatch.setenv('RED_LEAF_TOWN_BETA_PLAYERS', player.player_id)
    partner_id = service.partner_catalog_loader().partners[0].id
    service.admin_grant_partner(player.player_id, partner_id)
    def prepare(p):
        p.experience = service.content.level_definition(16).total_xp
        p.coins = 20_000
        p.stamina = 50
        add_item(p, "composite_plank", 20, 1)
        add_item(p, 'voyage_sail', 1, 1)
    repository.update(player.player_id, prepare)
    service.build_sailing_ship('redis-sailing-sub')
    assert repository.get(player.player_id).sailing.ship_built
    service.start_sailing('redis-sailing-sub', 'reed_bay', [partner_id], 'none', 'redis-voyage')
    frozen = repository.get(player.player_id).sailing.active_run
    reloaded = GameService(load_content(), repository, clock=lambda: frozen.ready_at)
    claim = reloaded.collect_sailing('redis-sailing-sub', frozen.run_id)
    assert claim['result']['drops']
    state = repository.get(player.player_id)
    assert state.sailing.active_run is None
    assert state.sailing.last_run == frozen
    assert state.sailing.completed_voyages == 1
    assert reloaded.collect_sailing('redis-sailing-sub', frozen.run_id)['result']['duplicate']
    assert repository.get(player.player_id).inventory == state.inventory


def test_crafting_queue_partial_rewards_and_reservations_survive_reload(repository):
    now = 1_700_000_000
    content = load_content()
    service = GameService(content, repository, clock=lambda: now)
    player = service.ensure_player("redis-craft-queue", "工坊居民")

    def prepare(state):
        state.experience = 60
        state.inventory["maple_wood"] = {1: 8}
        state.task_items["harvest_knot"] = 4

    repository.update(player.player_id, prepare)
    started = service.start_crafting(
        "redis-craft-queue", "town_workbench", "saw_maple_plank", "harvest_knot", quantity=4,
    )
    now = started["state"]["crafting_stations"][0]["task_snapshot"]["ready_at"]
    reloaded = GameService(content, repository, clock=lambda: now)
    state = reloaded.snapshot_by_sub("redis-craft-queue")["crafting_stations"][0]
    assert state["completed_count"] == 1
    assert state["queued_count"] == 2
    reloaded = GameService(content, repository, clock=lambda: now)
    cancelled = reloaded.cancel_task("redis-craft-queue", "crafting", "town_workbench")
    assert cancelled["result"]["refunded_task_items"] == {"harvest_knot": 2}
    reloaded = GameService(content, repository, clock=lambda: now)
    collected = reloaded.collect_crafting("redis-craft-queue", "town_workbench")
    assert collected["result"]["quantity"] == 2
    assert collected["state"]["crafting_stations"][0]["empty"]
    state = repository.get(player.player_id)
    assert state.inventory["maple_wood"] == {1: 4}
    assert state.task_items["harvest_knot"] == 2


def test_growth_achievement_history_and_claims_survive_reload(repository):
    now = 1_700_000_000
    service = GameService(load_content(), repository, clock=lambda: now)
    player = service.ensure_player('redis-growth-achievements', '成就持久化测试')
    def prepare(state):
        stats = state.achievement_stats
        stats.gathering_items = {'collect_reed_wetland': ['tough_vine', 'reed', 'wild_lotus_root', 'amber_cattail']}
        stats.livestock_item_ids = ['duck_egg', 'wool', 'jade_duck_egg', 'cloud_fleece']
        stats.bred_species_ids = ['duck', 'sheep']
        stats.sailing_route_ids = ['reed_bay', 'white_sail', 'mist_isles']
        stats.completed_expedition_ids = ['red_maple_hinterland']
        stats.completed_delve_ids = ['spiritfruit_meadow']
        stats.delve_wins = {'spiritfruit_meadow': 1}
    repository.update(player.player_id, prepare)
    reloaded = GameService(load_content(), repository, clock=lambda: now)
    entries = reloaded.snapshot_by_sub('redis-growth-achievements')['achievements']['entries']
    completed = {a['achievement_id'] for a in entries if a['completed']}
    assert {'first_wetland', 'wetland_collection', 'duck_sheep_products', 'duck_sheep_bred',
            'duck_sheep_specials', 'three_sailing_routes', 'hinterland_completed',
            'first_meadow_victory', 'meadow_completed'} <= completed
    result = reloaded.claim_all_achievements('redis-growth-achievements')
    again = GameService(load_content(), repository, clock=lambda: now)
    assert again.snapshot_by_sub('redis-growth-achievements')['player']['maple_flame'] == result['result']['maple_flame']
    assert again.claim_all_achievements('redis-growth-achievements')['result']['maple_flame'] == 0


def test_summer_reward_concurrent_claim_persists_once(repository):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    from red_leaf_town.domain.models import SeasonProgressState
    now = int(datetime.fromisoformat('2026-09-29T12:00:00+08:00').timestamp())
    service = GameService(load_content(), repository, clock=lambda: now)
    player = service.ensure_player('redis-summer', '夏活持久化')
    repository.update(player.player_id, lambda p: p.season_progress.__setitem__(
        'summer_tide_festival', SeasonProgressState(units=100 * 1800)))
    before = repository.get(player.player_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.claim_summer_reward('redis-summer', 100), range(2)))
    assert sum(not r['result']['duplicate'] for r in results) == 1
    reloaded = GameService(load_content(), repository, clock=lambda: now)
    state = reloaded.snapshot_by_sub('redis-summer')
    assert state['summer_event']['milestones'][0]['claimed']
    after = repository.get(player.player_id)
    assert after.guide_leaves == before.guide_leaves + 2
    assert after.coins == before.coins + 3000
    assert after.season_progress['summer_tide_festival'].units == 100 * 1800
