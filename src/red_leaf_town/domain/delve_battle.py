"""探秘副本的回合制战斗：先攻、命中、伤害、道具与逃跑的纯逻辑。

这里只认快照，不认玩家存档：调用方把队伍状态、装备快照和敌人状态传进来，拿回一段
战斗日志和被就地改过的状态。骰子全部由调用方注入的 rng 掷，方便测试定死种子。

数值口径集中在文件顶部的常量和 `member_max_hp` / `armor_class` 里，调平衡只改这里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from random import Random, SystemRandom

from ..partner_traits import execute_partner_traits
from .models import DelveBattleLog, DelveBattleState, DelveEnemyState, DelveLoadoutSnapshot, DelveMemberState


# 最大 HP = 基础 + 等级项 + 力量项 + 装备加成。
BASE_HP = 24
HP_PER_LEVEL = 2
HP_PER_STRENGTH = 2
BASE_ARMOR_CLASS = 10
# 幸运最多把暴击范围往下压两点（19-20 / 18-20），高于这个就得靠内容而不是属性。
MAX_LUCK_CRIT_WIDTH = 2
FIST_DAMAGE_DICE = "1d4"
# 探秘副本固定三人出发，携带道具最多这么多份。
PARTY_SIZE = 3
CARRY_SLOTS = 6
# 幸运给战利品的品质能力加成，按队伍里最高的幸运调整值算。
LUCK_QUALITY_BONUS = 8

_DICE = re.compile(r"^([1-9]\d?)d(\d+)$")


class BattleError(ValueError):
    """战斗流程里玩家不该做的事，由 service 转成 GameError。"""


def modifier(value: int) -> int:
    return (value - 10) // 2


def member_max_hp(level: int, strength: int, loadout: DelveLoadoutSnapshot | None = None) -> int:
    bonus = loadout.max_hp_bonus if loadout else 0
    return BASE_HP + HP_PER_LEVEL * max(1, level) + HP_PER_STRENGTH * max(1, strength) + bonus


def armor_class(agility: int, loadout: DelveLoadoutSnapshot | None = None) -> int:
    bonus = loadout.armor_bonus if loadout else 0
    return BASE_ARMOR_CLASS + modifier(agility) + bonus


def critical_minimum(luck: int) -> int:
    return 20 - max(0, min(MAX_LUCK_CRIT_WIDTH, modifier(luck)))


def roll_dice(rng: Random | SystemRandom, dice: str, times: int = 1) -> int:
    """掷 `2d6` 这样的骰。times>1 时整组骰数翻倍，用于暴击。"""

    match = _DICE.match(dice or "")
    if match is None:
        raise BattleError(f"看不懂的骰子写法：{dice}")
    count, faces = int(match.group(1)), int(match.group(2))
    return sum(rng.randint(1, faces) for _ in range(count * max(1, times)))


@dataclass
class PartyMember:
    """一名出战伙伴在这场战斗里的全部输入。"""

    partner_id: str
    name: str
    strength: int
    agility: int
    intelligence: int
    luck: int
    state: DelveMemberState
    loadout: DelveLoadoutSnapshot
    trait_codes: list[str] = field(default_factory=list)

    def attribute(self, name: str) -> int:
        return getattr(self, name)

    @property
    def attack_modifier(self) -> int:
        return (
            modifier(self.attribute(self.loadout.attack_attribute))
            + self.loadout.attack_bonus
            + self.loadout.proficiency_bonus
        )

    @property
    def damage_modifier(self) -> int:
        return modifier(self.attribute(self.loadout.attack_attribute))

    @property
    def damage_dice(self) -> str:
        return self.loadout.damage_dice or FIST_DAMAGE_DICE

    @property
    def down(self) -> bool:
        return self.state.hp <= 0


def _roll_d20(rng: Random | SystemRandom, mode: str) -> tuple[list[int], int]:
    rolls = [rng.randint(1, 20)]
    if mode in ("advantage", "disadvantage"):
        rolls.append(rng.randint(1, 20))
        kept = max(rolls) if mode == "advantage" else min(rolls)
    else:
        kept = rolls[0]
    return rolls, kept


def build_initiative(
    rng: Random | SystemRandom,
    members: dict[str, PartyMember],
    enemies: list[DelveEnemyState],
    enemy_initiative: dict[str, int],
) -> list[str]:
    """战斗开始掷一次先攻，整场固定。同分时我方先手。"""

    entries: list[tuple[int, int, str]] = []
    for partner_id, member in members.items():
        total = rng.randint(1, 20) + modifier(member.agility) + member.loadout.initiative_bonus
        entries.append((total, 1, partner_id))
    for enemy in enemies:
        total = rng.randint(1, 20) + enemy_initiative.get(enemy.key, 0)
        entries.append((total, 0, enemy.key))
    entries.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]))
    return [entry[2] for entry in entries]


def living_enemies(battle: DelveBattleState) -> list[DelveEnemyState]:
    return [enemy for enemy in battle.enemies if enemy.hp > 0]


def living_members(members: dict[str, PartyMember]) -> list[PartyMember]:
    return [member for member in members.values() if not member.down]


def current_actor(battle: DelveBattleState, members: dict[str, PartyMember]) -> str:
    """当前该谁行动。已经倒下 / 已经阵亡的单位直接跳过。"""

    for _ in range(len(battle.order) * 2):
        actor = battle.order[battle.turn_index % len(battle.order)]
        if actor in members and not members[actor].down:
            return actor
        enemy = _enemy(battle, actor)
        if enemy is not None and enemy.hp > 0:
            return actor
        _advance(battle)
    return ""


def _enemy(battle: DelveBattleState, key: str) -> DelveEnemyState | None:
    return next((entry for entry in battle.enemies if entry.key == key), None)


def _advance(battle: DelveBattleState) -> None:
    battle.turn_index += 1
    if battle.turn_index >= len(battle.order):
        battle.turn_index = 0
        battle.round += 1


def _log(battle: DelveBattleState, **fields) -> None:
    battle.logs.append(DelveBattleLog(round=battle.round, **fields))
    if len(battle.logs) > 60:
        del battle.logs[:-60]


def member_attack(
    rng: Random | SystemRandom,
    battle: DelveBattleState,
    member: PartyMember,
    target_key: str,
) -> None:
    target = _enemy(battle, target_key)
    if target is None or target.hp <= 0:
        raise BattleError("这个目标已经不在场上了")

    trait_context = {
        "phase": "delve_attack",
        "source_partner_id": member.partner_id,
        "current_hp": member.state.hp,
        "max_hp": member.state.max_hp,
        "weapon_item_id": member.loadout.weapon_item_id,
        "attack_attribute": member.loadout.attack_attribute,
        "dice_adjustment": 0,
        "attack_bonus": 0,
        "damage_bonus": 0,
        "applied_effects": [],
    }
    execute_partner_traits(member.trait_codes, trait_context)
    accessory_advantage = bool(battle.advantage_ready.get(member.partner_id))
    mode = "advantage" if accessory_advantage or trait_context["dice_adjustment"] > 0 else "normal"
    rolls, kept = _roll_d20(rng, mode)
    if accessory_advantage:
        battle.advantage_ready[member.partner_id] = False
    attack_modifier = member.attack_modifier + int(trait_context["attack_bonus"])
    total = kept + attack_modifier
    critical = kept >= critical_minimum(member.luck)
    hit = kept != 1 and (critical or total >= target.armor_class)
    reroll_note = ""
    if not hit and member.state.first_miss_reroll_ready:
        member.state.first_miss_reroll_ready = False
        original = kept
        rerolls, kept = _roll_d20(rng, "normal")
        rolls.extend(rerolls)
        total = kept + attack_modifier
        critical = kept >= critical_minimum(member.luck)
        hit = kept != 1 and (critical or total >= target.armor_class)
        reroll_note = f"临危应变：首次未命中（{original}），重掷为 {kept}。"
    damage = 0
    if hit:
        damage = max(
            1,
            roll_dice(rng, member.damage_dice, 2 if critical else 1)
            + member.damage_modifier
            + int(trait_context["damage_bonus"]),
        )
        target.hp = max(0, target.hp - damage)

    if not hit:
        text = f"{member.name}的攻击被{target.name}闪开了。"
    elif critical:
        text = f"{member.name}抓住破绽命中要害，对{target.name}造成 {damage} 点伤害。"
    else:
        text = f"{member.name}命中{target.name}，造成 {damage} 点伤害。"
    if hit and target.hp <= 0:
        text += f" {target.name}倒下了。"

    _log(
        battle,
        actor=member.partner_id,
        actor_name=member.name,
        action="attack",
        target=target.key,
        target_name=target.name,
        roll=kept,
        rolls=rolls,
        modifier=attack_modifier,
        total=total,
        hit=hit,
        critical=critical and hit,
        damage=damage,
        text=reroll_note + text,
    )


def member_heal(
    rng: Random | SystemRandom,
    battle: DelveBattleState,
    member: PartyMember,
    target: PartyMember,
    item_name: str,
    dice: str,
    flat: int,
) -> int:
    if target.down:
        raise BattleError("倒下的伙伴没法用道具救回来")
    amount = (roll_dice(rng, dice) if dice else 0) + flat
    healed = min(amount, target.state.max_hp - target.state.hp)
    target.state.hp += healed
    _log(
        battle,
        actor=member.partner_id,
        actor_name=member.name,
        action="item",
        target=target.partner_id,
        target_name=target.name,
        healing=healed,
        text=f"{member.name}用了{item_name}，{target.name}恢复 {healed} 点体力。",
    )
    return healed


def member_prepare_advantage(battle: DelveBattleState, member: PartyMember) -> None:
    left = battle.advantage_uses_left.get(member.partner_id, 0)
    if left <= 0:
        raise BattleError("这件饰品的优势次数已经用完了")
    if battle.advantage_ready.get(member.partner_id):
        raise BattleError("已经处于优势状态了")
    battle.advantage_uses_left[member.partner_id] = left - 1
    battle.advantage_ready[member.partner_id] = True
    _log(
        battle,
        actor=member.partner_id,
        actor_name=member.name,
        action="advantage",
        text=f"{member.name}稳住呼吸，下一次攻击将以优势骰进行。",
    )


def attempt_flee(
    rng: Random | SystemRandom,
    battle: DelveBattleState,
    member: PartyMember,
    members: dict[str, PartyMember],
) -> bool:
    """全队敏捷取最高值做一次检定。成功就整队脱离，失败白白丢一个回合。"""

    if not battle.can_flee:
        raise BattleError("这场战斗没法逃跑")
    best = max(living_members(members), key=lambda entry: modifier(entry.agility))
    rolls, kept = _roll_d20(rng, "normal")
    bonus = modifier(best.agility)
    total = kept + bonus
    success = kept != 1 and (kept == 20 or total >= battle.flee_dc)
    _log(
        battle,
        actor=member.partner_id,
        actor_name=member.name,
        action="flee",
        roll=kept,
        rolls=rolls,
        modifier=bonus,
        total=total,
        hit=success,
        text=(
            f"{best.name}带着队伍拉开距离，成功脱离了战斗。"
            if success
            else f"{best.name}没能找到脱身的空隙，队伍被缠住了。"
        ),
    )
    return success


def enemy_turn(
    rng: Random | SystemRandom,
    battle: DelveBattleState,
    enemy: DelveEnemyState,
    attacks: list[dict],
    members: dict[str, PartyMember],
    attacks_per_turn: int = 1,
) -> None:
    """敌人的一个回合。多段攻击的每一击都单独选目标、单独掷骰，打空了就提前收手。"""

    from .boss_moves import boss_turn

    def normal_turn():
        for _ in range(max(1, attacks_per_turn)):
            if not living_members(members):
                return
            _enemy_attack(rng, battle, enemy, attacks, members)

    if not boss_turn(rng, battle, enemy, members, normal_turn):
        normal_turn()


def _enemy_attack(
    rng: Random | SystemRandom,
    battle: DelveBattleState,
    enemy: DelveEnemyState,
    attacks: list[dict],
    members: dict[str, PartyMember],
) -> None:
    alive = living_members(members)
    if not alive:
        return
    target = rng.choice(alive)
    attack = rng.choice(attacks)
    rolls, kept = _roll_d20(rng, "normal")
    total = kept + int(attack["to_hit"])
    critical = kept == 20
    hit = kept != 1 and (critical or total >= target.state.armor_class)
    damage = 0
    if hit:
        damage = max(
            1,
            roll_dice(rng, str(attack["damage_dice"]), 2 if critical else 1) + int(attack.get("damage_bonus", 0)),
        )
        target.state.hp = max(0, target.state.hp - damage)

    if not hit:
        text = f"{enemy.name}的{attack['name']}落空了。"
    else:
        text = f"{enemy.name}的{attack['name']}命中{target.name}，造成 {damage} 点伤害。"
        if target.state.hp <= 0:
            text += f" {target.name}失去了战斗力。"

    _log(
        battle,
        actor=enemy.key,
        actor_name=enemy.name,
        action="attack",
        target=target.partner_id,
        target_name=target.name,
        roll=kept,
        rolls=rolls,
        modifier=int(attack["to_hit"]),
        total=total,
        hit=hit,
        critical=critical and hit,
        damage=damage,
        text=text,
    )


def advance_turn(battle: DelveBattleState) -> None:
    _advance(battle)


def battle_outcome(battle: DelveBattleState, members: dict[str, PartyMember]) -> str:
    """`victory` / `wiped` / `ongoing`。"""

    if not living_enemies(battle):
        return "victory"
    if not living_members(members):
        return "wiped"
    return "ongoing"
