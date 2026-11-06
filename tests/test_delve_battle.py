from __future__ import annotations

import random

import pytest

from red_leaf_town.domain import DelveBattleState, DelveEnemyState, DelveLoadoutSnapshot, DelveMemberState
from red_leaf_town.domain import delve_battle
from red_leaf_town.domain.delve_battle import BattleError, PartyMember


class FixedRandom(random.Random):
    """按脚本吐点数的骰子，用来把命中、暴击和大失败钉死。"""

    def __init__(self, script: list[int]):
        super().__init__(0)
        self.script = list(script)

    def randint(self, a: int, b: int) -> int:
        if self.script:
            return max(a, min(b, self.script.pop(0)))
        return b

    def choice(self, seq):
        return seq[0]


def member(partner_id: str, *, strength=12, agility=12, intelligence=12, luck=10, hp=40, dice="1d10"):
    loadout = DelveLoadoutSnapshot(damage_dice=dice, attack_attribute="strength")
    return PartyMember(
        partner_id=partner_id,
        name=partner_id,
        strength=strength,
        agility=agility,
        intelligence=intelligence,
        luck=luck,
        state=DelveMemberState(max_hp=hp, hp=hp, armor_class=12),
        loadout=loadout,
    )


def battle(enemy_hp=30, order=("hero", "beast#1"), can_flee=True, flee_dc=12):
    return DelveBattleState(
        battle_id="battle",
        event_id="event",
        choice_id="choice",
        depth=1,
        can_flee=can_flee,
        flee_dc=flee_dc,
        enemies=[DelveEnemyState(
            key="beast#1",
            enemy_id="beast",
            name="野兽",
            max_hp=enemy_hp,
            hp=enemy_hp,
            armor_class=12,
        )],
        order=list(order),
    )


def test_max_hp_and_armor_come_from_level_strength_and_gear():
    gear = DelveLoadoutSnapshot(max_hp_bonus=8, armor_bonus=2)

    assert delve_battle.member_max_hp(10, 12) == 24 + 20 + 24
    assert delve_battle.member_max_hp(10, 12, gear) == 24 + 20 + 24 + 8
    assert delve_battle.armor_class(15) == 12
    assert delve_battle.armor_class(15, gear) == 14


def test_luck_widens_the_critical_range_up_to_two_points():
    assert delve_battle.critical_minimum(9) == 20
    assert delve_battle.critical_minimum(13) == 19
    assert delve_battle.critical_minimum(15) == 18
    # 幸运再高也只压两点，继续拉宽必须靠内容而不是属性。
    assert delve_battle.critical_minimum(20) == 18


def test_natural_one_misses_and_natural_twenty_doubles_the_damage_dice():
    hero = member("hero")
    state = battle()
    rng = FixedRandom([1])
    delve_battle.member_attack(rng, state, hero, "beast#1")

    assert state.enemies[0].hp == 30
    assert state.logs[-1].hit is False

    rng = FixedRandom([20, 6, 6])
    delve_battle.member_attack(rng, state, hero, "beast#1")
    log = state.logs[-1]

    # 暴击掷两颗 1d10（6+6）再加力量调整值 +1。
    assert log.critical is True
    assert log.damage == 13
    assert state.enemies[0].hp == 17


def test_an_accessory_advantage_keeps_the_better_of_two_dice_once():
    hero = member("hero")
    state = battle()
    state.advantage_uses_left["hero"] = 1

    delve_battle.member_prepare_advantage(state, hero)
    rng = FixedRandom([3, 17, 5])
    delve_battle.member_attack(rng, state, hero, "beast#1")

    assert state.logs[-1].rolls == [3, 17]
    assert state.logs[-1].hit is True
    assert state.advantage_ready["hero"] is False
    with pytest.raises(BattleError, match="用完"):
        delve_battle.member_prepare_advantage(state, hero)


def test_full_health_trait_grants_advantage_only_above_ninety_percent_hp():
    hero = member("bai_lin", hp=100)
    hero.trait_codes = ["full_health_advantage"]
    state = battle()

    delve_battle.member_attack(FixedRandom([3, 17, 5]), state, hero, "beast#1")
    assert state.logs[-1].rolls == [3, 17]

    hero.state.hp = 90
    delve_battle.member_attack(FixedRandom([17, 5]), state, hero, "beast#1")
    assert state.logs[-1].rolls == [17]


def test_strength_weapon_trait_trades_five_attack_for_ten_damage():
    hero = member("bai_lin", strength=18, hp=100, dice="1d10")
    hero.loadout.weapon_item_id = "copper_greatsword"
    hero.trait_codes = ["strength_weapon_tradeoff"]
    state = battle(enemy_hp=40)

    delve_battle.member_attack(FixedRandom([13, 6]), state, hero, "beast#1")

    log = state.logs[-1]
    assert log.modifier == -1  # 力量调整值 +4，再承受特性的 -5。
    assert log.damage == 20  # 1d10 掷出 6，力量 +4，特性 +10。
    assert state.enemies[0].hp == 20


def test_strength_weapon_trait_does_not_apply_to_unarmed_or_agility_weapons():
    hero = member("bai_lin", strength=18, agility=16, hp=100, dice="1d6")
    hero.trait_codes = ["strength_weapon_tradeoff"]
    state = battle(enemy_hp=50)

    delve_battle.member_attack(FixedRandom([13, 6]), state, hero, "beast#1")
    assert state.logs[-1].modifier == 4
    assert state.logs[-1].damage == 10

    hero.loadout.weapon_item_id = "silver_dagger"
    hero.loadout.attack_attribute = "agility"
    delve_battle.member_attack(FixedRandom([13, 6]), state, hero, "beast#1")
    assert state.logs[-1].modifier == 3
    assert state.logs[-1].damage == 9


def test_downed_members_stop_acting_and_a_full_party_wipe_ends_the_battle():
    hero = member("hero", hp=6)
    members = {"hero": hero}
    state = battle()

    delve_battle.enemy_turn(
        FixedRandom([18, 6]),
        state,
        state.enemies[0],
        [{"name": "撞击", "to_hit": 4, "damage_dice": "1d6", "damage_bonus": 2}],
        members,
    )

    assert hero.state.hp == 0
    assert hero.down is True
    assert delve_battle.battle_outcome(state, members) == "wiped"


def test_killing_the_last_enemy_is_a_victory():
    hero = member("hero")
    members = {"hero": hero}
    state = battle(enemy_hp=4)

    delve_battle.member_attack(FixedRandom([18, 8]), state, hero, "beast#1")

    assert state.enemies[0].hp == 0
    assert delve_battle.battle_outcome(state, members) == "victory"
    with pytest.raises(BattleError, match="不在场上"):
        delve_battle.member_attack(FixedRandom([18, 8]), state, hero, "beast#1")


def test_fleeing_uses_the_best_agility_in_the_party():
    slow = member("slow", agility=8)
    fast = member("fast", agility=18)
    members = {"slow": slow, "fast": fast}
    state = battle(order=("slow", "fast", "beast#1"), flee_dc=13)

    assert delve_battle.attempt_flee(FixedRandom([8]), state, slow, members) is False
    # 9 + 敏捷 18 的调整值 +4 = 13，正好过线，且用的是队里最快的那个人而不是行动的人。
    assert delve_battle.attempt_flee(FixedRandom([9]), state, slow, members) is True

    locked = battle(can_flee=False)
    with pytest.raises(BattleError, match="没法逃跑"):
        delve_battle.attempt_flee(FixedRandom([20]), locked, slow, members)


def test_healing_never_overflows_the_maximum():
    hero = member("hero", hp=40)
    hero.state.hp = 35
    state = battle()

    healed = delve_battle.member_heal(FixedRandom([6, 6]), state, hero, hero, "秋露药膏", "2d6", 4)

    assert healed == 5
    assert hero.state.hp == 40


def test_initiative_is_rolled_once_and_ties_go_to_the_party():
    hero = member("hero", agility=10)
    members = {"hero": hero}
    enemies = [DelveEnemyState(
        key="beast#1", enemy_id="beast", name="野兽", max_hp=10, hp=10, armor_class=12,
    )]

    order = delve_battle.build_initiative(FixedRandom([12, 12]), members, enemies, {"beast#1": 0})

    assert order == ["hero", "beast#1"]


def test_flame_chain_waits_for_hit_and_is_once_per_battle_after_reload():
    hero = member('hero')
    hero.trait_codes = ['flame_chain']
    state = battle(enemy_hp=100)
    delve_battle.member_attack(FixedRandom([1]), state, hero, 'beast#1')
    assert state.first_hit_followup_used == []
    delve_battle.member_attack(FixedRandom([12, 3, 20, 4, 4]), state, hero, 'beast#1')
    assert len(state.logs) == 3
    assert state.logs[-1].critical is True
    assert state.logs[-1].damage == 9
    assert state.first_hit_followup_used == ['hero']
    state = DelveBattleState.model_validate_json(state.model_dump_json())
    delve_battle.member_attack(FixedRandom([12, 3]), state, hero, 'beast#1')
    assert len(state.logs) == 4
    fresh = battle()
    delve_battle.member_attack(FixedRandom([12, 3, 12, 3]), fresh, hero, 'beast#1')
    assert len(fresh.logs) == 2


def test_flame_chain_changes_dead_target_and_does_not_reuse_accessory_advantage():
    hero = member('hero')
    hero.trait_codes = ['flame_chain']
    state = battle(enemy_hp=2)
    state.enemies.append(state.enemies[0].model_copy(update={'key': 'beast#2', 'hp': 30, 'max_hp': 30}))
    state.advantage_ready['hero'] = True
    delve_battle.member_attack(FixedRandom([2, 12, 3, 1]), state, hero, 'beast#1')
    assert state.enemies[0].hp == 0
    assert state.enemies[1].hp == 30
    assert state.logs[-1].rolls == [1]
    assert state.logs[-1].hit is False
    assert len(state.logs) == 2
