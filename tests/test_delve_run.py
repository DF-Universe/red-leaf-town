from __future__ import annotations

import random

import pytest

from red_leaf_town.application import GameError, GameService
from red_leaf_town.content import load_content
from red_leaf_town.domain import CarriedItemSnapshot, PlayerState
from red_leaf_town.domain.economy import add_item
from red_leaf_town.infrastructure import InMemoryPlayerRepository
from red_leaf_town.partner_content import PartnerCatalog, PartnerDefinition


PARTY = ("leader", "scout", "guard")


def partner(partner_id: str, exploration: int | None, stats: dict) -> PartnerDefinition:
    tendencies = [{"industry": "farming", "level_1": 10, "level_60": 10}]
    if exploration is not None:
        tendencies.insert(0, {"industry": "exploration", "level_1": exploration, "level_60": exploration})
    return PartnerDefinition.model_validate({
        "id": partner_id,
        "name": partner_id,
        "rarity": 3,
        "tendencies": tendencies,
        "exploration_stats": stats,
        "avatar_crops": [
            {"breakthrough": stage, "x": 0, "y": 0, "w": 1, "h": 1}
            for stage in range(3)
        ],
    })


class ScriptedRandom(random.Random):
    """d20 全给 20，其它骰给最大值：一路命中、一路暴击，用来把流程跑通。"""

    def randint(self, a: int, b: int) -> int:
        return b

    def choice(self, seq):
        return seq[0]


class SteadyRandom(random.Random):
    """d20 一律 10，其它骰给最大值。配合下面把敌人 AC 拉满，我方必失、敌人必中。"""

    def randint(self, a: int, b: int) -> int:
        return 10 if (a, b) == (1, 20) else b

    def choice(self, seq):
        return seq[0]


@pytest.fixture
def delve_game():
    content = load_content().model_copy(deep=True)
    repository = InMemoryPlayerRepository(content)
    catalog = PartnerCatalog(partners=[
        partner("leader", 40, {"strength": 15, "agility": 12, "intelligence": 12, "luck": 13}),
        partner("scout", 20, {"strength": 11, "agility": 15, "intelligence": 12, "luck": 12}),
        partner("guard", None, {"strength": 14, "agility": 10, "intelligence": 11, "luck": 10}),
    ])
    service = GameService(
        content,
        repository,
        clock=lambda: 1_700_000_000,
        rng=ScriptedRandom(7),
        partner_catalog_loader=lambda: catalog,
    )
    player = service.ensure_player("delve-sub", "探秘测试员")
    for partner_id in PARTY:
        service.admin_grant_partner(player.player_id, partner_id)

    def prepare(state):
        state.experience = next(entry.total_xp for entry in content.levels if entry.level == 16)
        state.coins = 20_000
        state.stamina = 60
        add_item(state, "red_copper_greatsword", 2)
        add_item(state, "moon_silver_dagger", 1)
        add_item(state, "hunters_charm", 1)
        add_item(state, "herbal_salve", 3, 2)

    repository.update(player.player_id, prepare)
    return service, repository, player


def start(service, **overrides):
    payload = {
        "loadout": {
            "leader": {"weapon_item_id": "red_copper_greatsword", "accessory_item_id": "hunters_charm"},
            "scout": {"weapon_item_id": "moon_silver_dagger"},
            "guard": {"weapon_item_id": "red_copper_greatsword"},
        },
        "carried_items": [{"item_id": "herbal_salve", "quality": 2, "quantity": 2}],
    }
    payload.update(overrides)
    return service.start_exploration("delve-sub", "spiritfruit_meadow", list(PARTY), "leader", **payload)


def battle_choice(run) -> str:
    return next(
        choice["id"]
        for choice in run["current_event"]["choices"]
        if choice.get("battle")
    )


def fight_until_resolved(service, limit: int = 40) -> dict:
    """一直攻击第一个还活着的敌人，直到战斗结束。"""

    for _ in range(limit):
        state = service.snapshot_by_sub("delve-sub")
        run = state["exploration"]["active_run"]
        if run is None or run["battle"] is None:
            return state
        battle = run["battle"]
        target = next(enemy["key"] for enemy in battle["enemies"] if enemy["hp"] > 0)
        result = service.resolve_delve_battle_action("delve-sub", "attack", target)
        if result["result"]["outcome"] != "ongoing":
            return result["state"]
    raise AssertionError("战斗没有在预期回合内结束")


def test_delve_requires_a_full_party_and_freezes_gear_and_items(delve_game):
    service, repository, player = delve_game

    with pytest.raises(GameError, match="正好 3 名伙伴"):
        service.start_exploration("delve-sub", "spiritfruit_meadow", ["leader", "scout"], "leader")

    started = start(service)
    run = started["state"]["exploration"]["active_run"]
    saved = repository.get(player.player_id)

    assert started["state"]["player"]["coins"] == 20_000 - 2500
    # 装备只是冻结数值，不从仓库扣除；道具则当场扣掉。
    assert saved.inventory["red_copper_greatsword"][0] == 2
    assert saved.inventory["herbal_salve"][2] == 1
    assert [item["quantity"] for item in run["carried_items"]] == [2]
    leader = next(entry for entry in run["party"] if entry["partner_id"] == "leader")
    # 24 + 2×等级 1 + 2×力量 15 = 56，AC = 10 + 敏捷 12 的调整值 +1。
    assert leader["combat"]["max_hp"] == 56
    assert leader["combat"]["armor_class"] == 11
    assert leader["loadout"]["damage_dice"] == "1d10"
    assert leader["loadout"]["proficiency_bonus"] == 1


def test_gear_must_exist_be_owned_and_fit_its_slot(delve_game):
    service, _, _ = delve_game

    with pytest.raises(GameError, match="不能装备在这个槽位"):
        start(service, loadout={"leader": {"weapon_item_id": "hunters_charm"}})

    with pytest.raises(GameError, match="数量不够"):
        start(service, loadout={
            "leader": {"weapon_item_id": "moon_silver_dagger"},
            "scout": {"weapon_item_id": "moon_silver_dagger"},
        })

    with pytest.raises(GameError, match="不能带进副本"):
        start(service, carried_items=[{"item_id": "red_copper_ore", "quality": 1, "quantity": 1}])


def test_a_battle_node_holds_depth_until_the_fight_is_won(delve_game):
    service, _, _ = delve_game
    started = start(service)
    run = started["state"]["exploration"]["active_run"]

    result = service.resolve_exploration_event("delve-sub", battle_choice(run))
    fighting = result["state"]["exploration"]["active_run"]

    assert result["result"]["battle_started"] is True
    assert fighting["depth"] == 0
    assert fighting["battle"] is not None
    assert fighting["battle"]["enemies"][0]["hp"] > 0
    with pytest.raises(GameError, match="战斗结束之前"):
        service.withdraw_exploration("delve-sub")

    after = fight_until_resolved(service)
    won = after["exploration"]["active_run"]

    assert won["battle"] is None
    assert won["depth"] == 1
    assert won["pending_rewards"], "打赢之后节点奖励才结算"


def test_a_wipe_loses_every_frozen_reward_and_ends_the_run(delve_game):
    service, repository, player = delve_game
    started = start(service)
    run = started["state"]["exploration"]["active_run"]
    service.resolve_exploration_event("delve-sub", battle_choice(run))

    def stack_the_odds(state):
        # 让这场战斗必输：敌人打不动，我方一下就倒。
        for enemy in state.exploration_run.battle.enemies:
            enemy.armor_class = 25
        for combatant in state.exploration_run.combat_party.values():
            combatant.hp = 1

    repository.update(player.player_id, stack_the_odds)
    service.rng = SteadyRandom(3)

    state = fight_until_resolved(service, limit=200)

    assert state["exploration"]["active_run"] is None
    saved = repository.get(player.player_id)
    assert not saved.inventory.get("tough_fodder")
    # 装备没有被消耗，带进去的药膏则跟着战利品一起没了。
    assert saved.inventory["red_copper_greatsword"][0] == 2
    assert saved.inventory["herbal_salve"][2] == 1


def test_items_are_spent_from_the_frozen_pack_and_the_rest_comes_home(delve_game):
    service, repository, player = delve_game
    started = start(service)
    run = started["state"]["exploration"]["active_run"]
    service.resolve_exploration_event("delve-sub", battle_choice(run))

    battle = service.snapshot_by_sub("delve-sub")["exploration"]["active_run"]["battle"]
    actor = battle["current_actor"]
    assert battle["current_actor_is_party"] is True
    used = service.resolve_delve_battle_action("delve-sub", "item", actor, "herbal_salve", 2)

    assert [item["quantity"] for item in used["state"]["exploration"]["active_run"]["carried_items"]] == [1]

    fight_until_resolved(service)
    withdrawn = service.withdraw_exploration("delve-sub")

    assert [entry["quantity"] for entry in withdrawn["result"]["returned_items"]] == [1]
    saved = repository.get(player.player_id)
    assert saved.inventory["herbal_salve"][2] == 2


def test_equipment_drops_stay_out_of_the_quality_buckets(delve_game):
    service, repository, player = delve_game
    start(service)

    def hand_out_gear(state):
        state.exploration_run.pending_fixed_rewards.append(
            CarriedItemSnapshot(item_id="moonlit_pendant", quality=0, quantity=1)
        )

    repository.update(player.player_id, hand_out_gear)
    result = service.withdraw_exploration("delve-sub")
    saved = repository.get(player.player_id)

    assert saved.inventory["moonlit_pendant"] == {0: 1}
    assert [entry["item_id"] for entry in result["result"]["equipment_drops"]] == ["moonlit_pendant"]


def test_schema_thirty_gives_older_runs_empty_delve_state():
    migrated = PlayerState.model_validate({
        "schema_version": 29,
        "player_id": "old",
        "oauth_sub": "old-sub",
        "display_name": "老存档",
        "stamina_updated_at": 0,
        "created_at": 0,
        "updated_at": 0,
        "owned_partners": [{"partner_id": "leader", "level": 5, "experience": 0, "stars": 1, "acquired_at": 0}],
        "exploration_run": {
            "run_id": "run",
            "expedition_id": "red_maple_hinterland",
            "expedition_kind": "transport",
            "partner_ids": ["leader"],
            "leader_partner_id": "leader",
            "exploration_ability": 30,
            "entry_fee": 1000,
            "started_at": 0,
            "current_event_id": "windfallen_timber",
            "current_rolls": [7, 7],
        },
    })

    assert migrated.schema_version == PlayerState.model_fields["schema_version"].default
    assert migrated.exploration_run.battle is None
    assert migrated.exploration_run.loadout == {}
    assert migrated.exploration_run.combat_party == {}
    assert migrated.exploration_run.carried_items == []
    assert migrated.exploration_run.pending_fixed_rewards == []


def outcomes_of(expedition):
    for event in expedition.events:
        for choice in event.choices:
            for outcome in (choice.success, choice.failure, choice.critical_success, choice.critical_failure):
                if outcome is not None:
                    yield event, choice, outcome


def test_the_meadow_only_drops_grass_moss_herb_and_tree_seeds():
    expedition = load_content().exploration_expedition_map["spiritfruit_meadow"]

    assert expedition.kind == "delve"
    assert expedition.beta is False
    assert (expedition.min_level, expedition.entry_fee, expedition.max_depth) == (10, 2500, 8)

    materials = {reward.item_id for _, _, outcome in outcomes_of(expedition) for reward in outcome.rewards}
    fixed = {reward.item_id for _, _, outcome in outcomes_of(expedition) for reward in outcome.fixed_rewards}

    assert materials == {"tough_fodder", "silver_star_moss", "autumn_herb"}
    assert fixed == {"peach_berry_seed", "berry_berry_seed", "ironwood_bracer", "moonlit_pendant", "hunters_charm"}


def test_tree_fruit_seeds_only_drop_off_rare_branches_and_the_boss():
    expedition = load_content().exploration_expedition_map["spiritfruit_meadow"]
    sources: dict[str, set[str]] = {"peach_berry_seed": set(), "berry_berry_seed": set()}
    for event, choice, outcome in outcomes_of(expedition):
        for reward in outcome.fixed_rewards:
            if reward.item_id in sources:
                degree = "critical_success" if outcome is choice.critical_success else "success"
                sources[reward.item_id].add(f"{event.id}:{choice.id}:{degree}" if event.id == "mana_sapling" else f"{event.id}:{degree}")

    # 桃桃果种子：灌丛大成功、幼树摘果、打完 BOSS 保底一颗。
    assert sources["peach_berry_seed"] == {
        "fruit_thicket:critical_success",
        "mana_sapling:pick_the_seed:success",
        "mana_sapling:pick_the_seed:critical_success",
        "mana_sapling:dig_the_berry:critical_success",
        "warden_grove:success",
    }
    # 莓莓果种子只有幼树这一处：起根成功，或者摘果时撞上大成功。
    assert sources["berry_berry_seed"] == {
        "mana_sapling:pick_the_seed:critical_success",
        "mana_sapling:dig_the_berry:success",
        "mana_sapling:dig_the_berry:critical_success",
    }


def test_the_boss_hands_over_the_hunters_charm_the_second_gate_asks_for():
    content = load_content().model_copy(deep=True)
    boss_fight = content.exploration_expedition_map["spiritfruit_meadow"].event_map["warden_grove"].choices[0]

    assert [enemy for enemy in boss_fight.battle.enemy_ids] == ["fruitheart_warden"]
    assert boss_fight.battle.can_flee is False
    assert content.delve_enemy_map["fruitheart_warden"].boss is True
    assert [reward.item_id for reward in boss_fight.success.fixed_rewards] == ["hunters_charm", "peach_berry_seed"]


def test_the_boss_acts_twice_a_turn(delve_game):
    """首领在先攻序列里只占一格，靠多段攻击把行动经济补回来。"""
    service, _, _ = delve_game
    content = service.content

    assert content.delve_enemy_map["fruitheart_warden"].attacks_per_turn == 2
    assert all(
        enemy.attacks_per_turn == 1
        for enemy in content.delve_enemies
        if not enemy.boss
    )


def test_multiattack_swings_once_per_action_and_stops_when_the_party_is_down():
    from red_leaf_town.domain import delve_battle
    from red_leaf_town.domain.models import DelveBattleState, DelveEnemyState, DelveLoadoutSnapshot, DelveMemberState

    def build_party(hp: int):
        members = {}
        for name in ("a", "b"):
            members[name] = delve_battle.PartyMember(
                partner_id=name, name=name, strength=10, agility=10, intelligence=10, luck=10,
                state=DelveMemberState(max_hp=hp, hp=hp, armor_class=1),
                loadout=DelveLoadoutSnapshot(),
            )
        return members

    def swing(members, times):
        # 用同一批队员对象，打完之后可以直接检查他们的状态。
        enemy = DelveEnemyState(key="e", enemy_id="e", name="敌人", max_hp=10, hp=10, armor_class=10)
        battle = DelveBattleState(
            battle_id="b", event_id="e", choice_id="c", depth=1,
            enemies=[enemy], order=["a", "b", "e"],
        )
        delve_battle.enemy_turn(
            random.Random(3), battle, enemy,
            [{"name": "打", "to_hit": 20, "damage_dice": "1d4"}], members, times,
        )
        return battle

    # AC 1 对 to_hit +20：每一击必中，所以日志条数就是出手次数。
    assert len(swing(build_party(100), 1).logs) == 1
    assert len(swing(build_party(100), 3).logs) == 3
    # 打空了就收手，不会对着倒下的队伍继续挥。
    downed = build_party(1)
    battle = swing(downed, 4)
    assert len(battle.logs) == 2
    assert all(member.down for member in downed.values())
    assert delve_battle.battle_outcome(battle, downed) == "wiped"


def test_losing_the_opening_enemy_turns_ends_the_run_instead_of_hanging(delve_game):
    """敌人抢到先攻并在我方出手前打光队伍时，这一趟要当场结束。

    否则战斗会挂在那里，之后每个行动都被判成「不是我方的回合」，整个存档卡死。
    """
    service, repository, player = delve_game
    started = start(service)
    run = started["state"]["exploration"]["active_run"]

    def nearly_dead(state):
        for combatant in state.exploration_run.combat_party.values():
            combatant.hp = 1

    repository.update(player.player_id, nearly_dead)
    service.rng = SteadyRandom(3)

    result = service.resolve_exploration_event("delve-sub", battle_choice(run))

    if result["result"]["outcome"] == "wiped":
        assert result["result"]["battle_started"] is False
        assert result["state"]["exploration"]["active_run"] is None
        assert repository.get(player.player_id).exploration_run is None
    else:
        # 没有被开场打光的话，至少要轮到我方，不能停在敌人回合上。
        assert result["state"]["exploration"]["active_run"]["battle"]["current_actor_is_party"] is True


@pytest.mark.parametrize('boss', [False, True])
def test_delve_achievements_distinguish_battle_victory_and_boss_return(delve_game, monkeypatch, boss):
    from red_leaf_town.application import service as service_module
    service, repository, player = delve_game
    started = start(service)
    service.resolve_exploration_event('delve-sub', battle_choice(started['state']['exploration']['active_run']))
    def prepare(state):
        run = state.exploration_run
        run.depth = service.content.exploration_expedition_map[run.expedition_id].max_depth - 1
        run.battle.enemies[0].boss = boss
    repository.update(player.player_id, prepare)
    def win(rng, battle, actor, target):
        for enemy in battle.enemies:
            enemy.hp = 0
    monkeypatch.setattr(service_module.delve_battle, 'member_attack', win)
    target = repository.get(player.player_id).exploration_run.battle.enemies[0].key
    won = service.resolve_delve_battle_action('delve-sub', 'attack', target)
    entries = {a['achievement_id']: a for a in won['state']['achievements']['entries']}
    assert entries['first_meadow_victory']['completed']
    assert not entries['meadow_completed']['completed']
    assert repository.get(player.player_id).achievement_stats.delve_wins['spiritfruit_meadow'] == 1
    returned = service.withdraw_exploration('delve-sub')
    assert next(a for a in returned['state']['achievements']['entries'] if a['achievement_id'] == 'meadow_completed')['completed'] == boss
    with pytest.raises(GameError):
        service.withdraw_exploration('delve-sub')


def test_lost_corridor_random_pool_and_fixed_depths(delve_game):
    service, _, _ = delve_game
    route = service.content.exploration_expedition_map["lost_corridor"]
    assert (route.kind, route.min_level, route.max_depth, route.entry_fee) == ("delve", 15, 6, 1000)
    from red_leaf_town.domain.models import ExplorationRunState
    starts = set()
    for seed in range(100):
        service.rng = random.Random(seed)
        run = ExplorationRunState(run_id=str(seed), expedition_id=route.id, partner_ids=list(PARTY), leader_partner_id="leader", exploration_ability=100, entry_fee=1000, started_at=0, current_event_id="scuttlers_outer", expedition_kind="delve", current_rolls=[10, 10])
        for depth in range(1, 7):
            event_id = service._pick_exploration_event(route, run, depth)
            if depth == 1:
                starts.add(event_id)
            if depth == 3:
                assert event_id == "outer_crystal_chamber"
            if depth == 6:
                assert event_id == "miracle_spring"
            run.event_counts[event_id] = run.event_counts.get(event_id, 0) + 1
    assert len(starts) >= 3


def _start_corridor(delve_game):
    service, repository, player = delve_game
    # 流程测试缩短战斗；真实敌人数值另做固定种子的整趟模拟。
    for enemy in service.content.delve_enemies:
        enemy.max_hp = 1
    return service.start_exploration("delve-sub", "lost_corridor", list(PARTY), "leader", loadout={
        "leader": {"weapon_item_id": "red_copper_greatsword"},
        "scout": {"weapon_item_id": "moon_silver_dagger"},
        "guard": {"weapon_item_id": "red_copper_greatsword"},
    })


@pytest.mark.parametrize("finish_depth,crystals,books", [(3, 1, 0), (6, 3, 2)])
def test_corridor_crystals_and_books_only_settle_after_victory_and_withdrawal(delve_game, finish_depth, crystals, books):
    service, repository, player = delve_game
    _start_corridor(delve_game)
    for depth in range(1, finish_depth + 1):
        run = service.snapshot_by_sub("delve-sub")["exploration"]["active_run"]
        event = run["current_event"]
        service.resolve_exploration_event("delve-sub", event["choices"][0]["id"])
        assert not repository.get(player.player_id).inventory.get("miracle_crystal")
        if repository.get(player.player_id).exploration_run.battle:
            with pytest.raises(GameError, match="战斗结束之前"):
                service.withdraw_exploration("delve-sub")
            fight_until_resolved(service)
    saved = repository.get(player.player_id)
    assert sum(i.quantity for i in saved.exploration_run.pending_fixed_rewards if i.item_id == "miracle_crystal") == crystals
    service.withdraw_exploration("delve-sub")
    saved = repository.get(player.player_id)
    assert saved.inventory["miracle_crystal"] == {0: crystals}
    assert saved.inventory.get("partner_notes_medium", {}).get(0, 0) == books
    before = saved.inventory.copy()
    with pytest.raises(GameError):
        service.withdraw_exploration("delve-sub")
    assert repository.get(player.player_id).inventory == before


def test_corridor_is_locked_below_15(delve_game):
    service, repository, player = delve_game
    repository.update(player.player_id, lambda state: setattr(state, "experience", service.content.level_definition(14).total_xp))
    with pytest.raises(GameError, match="15"):
        service.start_exploration("delve-sub", "lost_corridor", list(PARTY), "leader")


def test_corridor_wipe_loses_unbanked_crystals(delve_game):
    service, repository, player = delve_game
    _start_corridor(delve_game)
    for _ in range(3):
        run = service.snapshot_by_sub("delve-sub")["exploration"]["active_run"]
        service.resolve_exploration_event("delve-sub", run["current_event"]["choices"][0]["id"])
        fight_until_resolved(service)
    assert any(item.item_id == "miracle_crystal" for item in repository.get(player.player_id).exploration_run.pending_fixed_rewards)
    def prepare_wipe(state):
        run = state.exploration_run
        run.current_event_id = "sentry_inner"
        for member in run.combat_party.values():
            member.hp = 1
    repository.update(player.player_id, prepare_wipe)
    enemy = service.content.delve_enemy_map["ruin_sentry"]
    enemy.max_hp = 10000
    enemy.armor_class = 100
    enemy.initiative_bonus = 100
    enemy.attacks[0].to_hit = 100
    service.rng = SteadyRandom(1)
    service.resolve_exploration_event("delve-sub", "fight")
    for _ in range(20):
        saved = repository.get(player.player_id)
        if saved.exploration_run is None:
            break
        target = saved.exploration_run.battle.enemies[0].key
        service.resolve_delve_battle_action("delve-sub", "attack", target)
    saved = repository.get(player.player_id)
    assert saved.exploration_run is None
    assert not saved.inventory.get("miracle_crystal")
    assert saved.inventory["red_copper_greatsword"][0] == 2
    assert all(member.partner_id in PARTY for member in saved.owned_partners)
