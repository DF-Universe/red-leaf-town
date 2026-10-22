"""「盛夏回响」专属效果：点名顾祇SP 执行检定时改用敏捷（运行时补丁包）。

背景（上游机制）：
  * 探索检定的属性来自内容数据 `choice.check.attribute`，在
    `GameService._prime_exploration_check_context()` 里被翻译成每名伙伴的 modifier；
    未被点名时 actor 取该 modifier 最高者，点名则用点名的那个，结果落在
    `context["check_actor_partner_ids"]`。
  * 之后 `_exploration_check_preview()` 用这两个字段算成功率；`partner_traits.py`
    的全队特性阶段读 `context["check_attribute"]` 决定哪些特性适用。
  * 装备（`EquipmentDefinition`）只有固定数值字段，**没有**「以某属性替代检定」
    这种效果位——所以这条效果只能由补丁注入。

做法：
  * 物品 `spotlight_mic_blade` 本身是普通武器（weapon / agility），专属效果由本模块赋予；
  * **认主范围 = 两位顾祇**（SP `guqi_sp` 与本体 `guqi`）：`_build_delve_loadout` 包一层做
    专属校验，其余伙伴装备直接 409「盛夏回响认主」；
  * 效果只认「顾祇SP 装备 + **玩家点名她执行这次检定**」：`_prime_exploration_check_context`
    包一层——只有 `check_actor_partner_ids == ["guqi_sp"]` 时，才把她的 modifier 按**敏捷**
    重算，并把 `context["check_attribute"]` 一起改成 `agility`，让特性阶段也按敏捷结算。
    **不干预选人**：没点名就保持上游同一套 max 规则。
  * `_exploration_check_preview` 包一层：命中时把返回的 `check_attribute` 显示成 `agility`，
    前端与日志看到的属性一致。
  * 本体顾祇装备：认主放行，但本模块对她**完全不起作用**（效果读的是 SP 的装备槽）。
    她拿话筒只有一条纯副作用——战斗攻击属性被武器拽成敏捷。

范围：装备只在 delve 出发时冻结进 `run.loadout`，所以该效果只在探秘副本的检定里生效。

接线：应用装配层里 `install_guqi_weapon()`。
"""

from __future__ import annotations

GUQI_PARTNER_ID = "guqi_sp"
GUQI_BASE_PARTNER_ID = "guqi"
#: 认主范围——两位顾祇都能拿；效果只对 SP 生效。
OWNER_PARTNER_IDS = (GUQI_PARTNER_ID, GUQI_BASE_PARTNER_ID)
WEAPON_ITEM_ID = "spotlight_mic_blade"


def _weapon_active(run) -> bool:
    """顾祇SP 是否在本趟探索里装备了这把武器。"""
    loadout = getattr(run, "loadout", None)
    if not loadout:
        return False
    snapshot = loadout.get(GUQI_PARTNER_ID) if hasattr(loadout, "get") else None
    if snapshot is None:
        return False
    return str(getattr(snapshot, "weapon_item_id", "") or "") == WEAPON_ITEM_ID


def install_guqi_weapon() -> None:
    """patch GameService：点名顾祇SP 执行检定时改用敏捷。幂等。"""
    from red_leaf_town.application.service import GameError, GameService

    if getattr(GameService, "_guqi_weapon_installed", False):
        return

    original_prime = GameService._prime_exploration_check_context
    original_preview = GameService._exploration_check_preview
    original_loadout = GameService._build_delve_loadout

    def _prime(self, run, choice, context):
        original_prime(self, run, choice, context)
        if choice is None or choice.check is None or not _weapon_active(run):
            return
        if choice.check.mode == "sum":
            # 求和模式整队一起掷，没法只改一个人的属性，保持上游行为。
            return
        # 只在「玩家点名顾祇SP 执行这次检定」时换敏捷；
        # 没点名时 actor 由上游 max 决定（她力量/智力都垫底，不会被选中）。
        if context.get("check_actor_partner_ids") != [GUQI_PARTNER_ID]:
            return
        base = context.get("check_base_modifiers") or {}
        if GUQI_PARTNER_ID not in base:
            return
        definition = self.partner_catalog_loader().partner_map.get(GUQI_PARTNER_ID)
        if definition is None:
            return
        base[GUQI_PARTNER_ID] = definition.exploration_stats.modifier("agility")
        context["check_base_modifiers"] = base
        context["check_attribute"] = "agility"
        context["_guqi_agility_swap"] = True

    def _preview(self, run, choice, context):
        result = original_preview(self, run, choice, context)
        if context.get("_guqi_agility_swap") and result.get("check_attribute"):
            if GUQI_PARTNER_ID in (context.get("check_actor_partner_ids") or []):
                result["check_attribute"] = "agility"
        return result

    def _build_loadout(self, player, partner_ids, loadout_input):
        for partner_id, entry in (loadout_input or {}).items():
            if (
                str((entry or {}).get("weapon_item_id") or "") == WEAPON_ITEM_ID
                and partner_id not in OWNER_PARTNER_IDS
            ):
                raise GameError("delve_equipment_invalid", "盛夏回响认主，只有顾祇能驾驭", 409)
        return original_loadout(self, player, partner_ids, loadout_input)

    GameService._prime_exploration_check_context = _prime
    GameService._exploration_check_preview = _preview
    GameService._build_delve_loadout = _build_loadout
    GameService._guqi_weapon_installed = True
