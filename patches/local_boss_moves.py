"""首领大招（运行时补丁包，不改动 red_leaf_town 包内文件）。

支持两种节奏：

  * **周期型**：`battle.round % every == 0` 的回合放一次全体重击。
  * **二阶段型**：首领血量掉到 `hp_ratio` 以下才进二阶段并播报，**当回合就放第一发**，
    之后以「进二阶段的那一回合」为锚点每 `every` 回合一次。

背景：`DelveEnemyDefinition` 只有 `attacks` / `attacks_per_turn` / `boss` 三个字段，
**没有技能栏**；敌人每回合就是 `enemy_turn()` 里循环 `attacks_per_turn` 次 `_enemy_attack()`。
所以首领大招只能由补丁注入：service 走的是模块属性查找 `delve_battle.enemy_turn(...)`。

做法：
  * 在配置的回合上，把该首领这一回合的**普通攻击整体换成一次全体重击**：
    遍历所有存活伙伴，每击照常掷 d20、比 AC、算伤害（暴击也保留），目标不再是随机抽取。
  * 放招的**前一回合**给首领的普通攻击补一句预警（追加到该条战报的 `text`）。
  * 二阶段型另外在**首次跌破阈值**的那一回合，把入场播报**前置**到该回合第一节战报上
    （不新增条目，前端刻印卡照常渲染）。
  * 战报仍用 `action="attack"`：`DelveBattleLog.action` 是
    `Literal["attack","item","flee","advantage"]`，写别的会被 pydantic 拒。

节奏细节：二阶段型若「从下一轮起算」，在血量较薄的副本里二阶段只剩 3~5 回合，
整场只放得出一次，「每 N 回合」等于没兑现 —— 所以第一发放在进二阶段当回合。

锚点记在模块级 `_PHASE_ANCHOR`（键 `battle.battle_id`，最多跟 64 场）。这是**内存态**：
重启后已打到一半的战斗会按当时血线重新锚定（播报重放一次，节奏从那一刻重新起算）；
战斗本身在存档里，不受影响。

配置在 `BOSS_MOVES`：改周期 / 数值 / 文案只改这张表。
接线：应用装配层里 `install_boss_moves()`。
"""

from __future__ import annotations

# 模块级引用，由 install_boss_moves() 填上（上游包要等 sys.path 就绪才能 import，
# 而 _unleash / _telegraph 是模块级函数，只能走这个口子拿模块）。
_BATTLE = None

# 全体重击的默认文案模板（占位符：{flavor} / {name}）。印加王沿用原文案，
# 新首领可以在自己的配置里给 `lead` / `lead_again` 覆盖。
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

# battle_id -> 进二阶段的回合数。见模块 docstring 末尾关于内存态的说明。
_PHASE_ANCHOR: dict[str, int] = {}
_MAX_TRACKED_BATTLES = 64


def install_boss_moves() -> None:
    """patch `domain.delve_battle.enemy_turn`：给配置的首领插入周期/二阶段大招。幂等。"""

    from red_leaf_town.domain import delve_battle

    if getattr(delve_battle, "_boss_moves_installed", False):
        return

    global _BATTLE
    _BATTLE = delve_battle

    original = delve_battle.enemy_turn

    def enemy_turn(rng, battle, enemy, attacks, members, attacks_per_turn=1):
        spec = BOSS_MOVES.get(getattr(enemy, "enemy_id", ""))
        if not spec or enemy.hp <= 0:
            return original(rng, battle, enemy, attacks, members, attacks_per_turn)

        every = max(1, int(spec["every"]))
        ratio = spec.get("hp_ratio")
        entered_phase = False

        if ratio is None:
            # 周期型：整场回合数取模。
            due = battle.round % every == 0
            warn = battle.round % every == every - 1
        else:
            if enemy.hp > enemy.max_hp * float(ratio):
                return original(rng, battle, enemy, attacks, members, attacks_per_turn)
            anchor, entered_phase = _phase_entry(battle, spec)
            elapsed = battle.round - anchor
            # 进二阶段那一回合就放第一发：实测这一档血量下二阶段只有 3~5 回合，
            # 从下一轮起算的话整场只放得出一次，「每三回合」等于没兑现。
            due = elapsed % every == 0
            warn = elapsed % every == every - 1

        if due:
            before = {id(entry) for entry in battle.logs}
            _unleash(rng, battle, enemy, spec, members)
            if entered_phase:
                _announce(battle, before, spec)
            return

        before = {id(entry) for entry in battle.logs}
        original(rng, battle, enemy, attacks, members, attacks_per_turn)
        if warn:
            _telegraph(battle, enemy, spec)
        if entered_phase:
            _announce(battle, before, spec)

    delve_battle.enemy_turn = enemy_turn
    delve_battle._boss_moves_installed = True


def _phase_entry(battle, spec) -> tuple[int, bool]:
    """首次跌破阈值时记下二阶段锚点。返回 (锚点回合, 是否刚进二阶段)。"""

    anchor = _PHASE_ANCHOR.get(battle.battle_id)
    if anchor is not None:
        return anchor, False
    anchor = battle.round
    _PHASE_ANCHOR[battle.battle_id] = anchor
    while len(_PHASE_ANCHOR) > _MAX_TRACKED_BATTLES:
        _PHASE_ANCHOR.pop(next(iter(_PHASE_ANCHOR)))
    return anchor, True


def _unleash(rng, battle, enemy, spec, members) -> None:
    """全体重击：每名存活伙伴各挨一次单独的命中检定。"""

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
