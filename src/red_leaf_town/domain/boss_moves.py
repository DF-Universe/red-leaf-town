"""Midsummer boss moves, contributed in PR #3. Phase timing survives reloads."""
DEFAULT_LEAD = "{flavor}，整室金箔一齐反光——「{name}」扫过全场。"
DEFAULT_LEAD_AGAIN = "「{name}」的金光又掠过。"

BOSS_MOVES: dict[str, dict] = {
    # 二阶段型：掉到半血以下播报「暖粉何在」，此后每 3 回合一次。
    "midsummer_forum_king": {
        "hp_ratio": 0.5,
        "enter_text": (
            "吧主甲把尾巴往台面上一压，尾尖的贝壳咬合起来，整片看台的灯牌应声亮起——「暖粉何在？时老师救我！」"
            "冥冥之中有一股力量，让吧主甲火力全开。"
        ),
        "every": 3,
        "name": "嚣张吧！！",
        "to_hit": 7,
        "damage_dice": "2d6",
        "damage_bonus": 3,
        "flavor": "帝王吧是这样的，怎么输，我不会啊！！！",
        "lead": "{flavor}——「{name}」扫过全场。",
        "lead_again": "「{name}」的光浪又推过来。",
        "telegraph": "美味的小孩，品尝……我能品尝十个！！看台的灯牌正逐排点亮——它要放大招了。",
    },
}

def boss_turn(rng, battle, enemy, members, normal_turn):
    spec = BOSS_MOVES.get(enemy.enemy_id)
    if not spec or enemy.hp <= 0:
        return False
    if enemy.phase_started_round is None and enemy.hp > enemy.max_hp * spec['hp_ratio']:
        return False
    entered = enemy.phase_started_round is None
    if entered:
        enemy.phase_started_round = battle.round
    elapsed = battle.round - enemy.phase_started_round
    before = {id(entry) for entry in battle.logs}
    if elapsed % spec['every'] == 0:
        _unleash(rng, battle, enemy, spec, members)
    else:
        normal_turn()
        if elapsed % spec['every'] == spec['every'] - 1:
            _telegraph(battle, enemy, spec)
    if entered:
        _announce(battle, before, spec)
    return True


def _unleash(rng, battle, enemy, spec, members) -> None:
    """全体重击：每名存活伙伴各挨一次单独的命中检定。"""

    from . import delve_battle as _BATTLE

    targets = list(_BATTLE.living_members(members))
    name = str(spec["name"])
    flavor = str(spec["flavor"])
    to_hit = int(spec["to_hit"])
    lead_first = str(spec.get("lead") or DEFAULT_LEAD).format(flavor=flavor, name=name)
    lead_again = str(spec.get("lead_again") or DEFAULT_LEAD_AGAIN).format(name=name)
    for index, target in enumerate(targets):
        rolls, kept = _BATTLE._roll_d20(rng, "normal")
        total = kept + to_hit
        critical = kept == 20
        hit = kept != 1 and (critical or total >= target.state.armor_class)
        damage = 0
        if hit:
            damage = max(
                1,
                _BATTLE.roll_dice(rng, str(spec["damage_dice"]), 2 if critical else 1)
                + int(spec["damage_bonus"]),
            )
            target.state.hp = max(0, target.state.hp - damage)

        lead = lead_first if index == 0 else lead_again
        if not hit:
            text = f"{lead}{target.name}堪堪避过了这一记。"
        else:
            text = f"{lead}{target.name}被正面击中，受到 {damage} 点伤害。"
            if target.state.hp <= 0:
                text += f" {target.name}失去了战斗力。"

        _BATTLE._log(
            battle,
            actor=enemy.key,
            actor_name=enemy.name,
            action="attack",
            target=target.partner_id,
            target_name=target.name,
            roll=kept,
            rolls=rolls,
            modifier=to_hit,
            total=total,
            hit=hit,
            critical=critical and hit,
            damage=damage,
            text=text,
        )


def _telegraph(battle, enemy, spec) -> None:
    """把预警追加到首领这一回合的最后一条战报上（不改条数，前端只多一句话）。"""

    for entry in reversed(battle.logs):
        if entry.actor == enemy.key and entry.action == "attack":
            entry.text = f"{entry.text}{spec['telegraph']}"
            return


def _announce(battle, before_ids, spec) -> None:
    """把「进二阶段」的播报前置到首领这一回合的第一条新战报上。

    不新增战报条目：`DelveBattleLog.action` 的 Literal 里没有「播报」这一类，
    硬塞一条会被 pydantic 拒。用对象 id 差集找新增条目，避免 60 条上限裁剪后下标错位。
    """

    text = str(spec.get("enter_text") or "")
    if not text:
        return
    for entry in battle.logs:
        if id(entry) not in before_ids:
            entry.text = f"{text}{entry.text}"
            return
