"""一次性免费副本（运行时补丁包，不改动 red_leaf_town 包内文件）。

按 `ONE_TIME_DUNGEONS` 这张表驱动三件事：

  1. **免费入场**。上游有**两处** entry_fee 都要求 > 0：
     `ExplorationExpeditionDefinition.entry_fee` 与 `ExplorationRunState.entry_fee`，
     而 `spend_coins` 对 <= 0 直接抛错。所以「免费」写不进内容文件，只能加载后改定义实例，
     再补两处兜底：
       * `spend_coins` 金额恰好为 0 时放行（负数仍按上游报错）；
       * run 快照自己的 entry_fee 要求 > 0 → 构造时把 0 兜成 1（那 1 币不会被真的扣）。
     结果：路线卡片与出发按钮都显示 0，任何余额（含 0 币）都能进，存档里的 run 仍然合法。
     （存档里若真存进 entry_fee=0，读取时 `PlayerState.model_validate` 会整档校验失败，
     进而静默跳角色 —— 兜底就是为了它。）
  2. **首通后隐藏**。挂在 `_content_visible` 上：`achievement_stats.completed_expedition_ids`
     里有它就不再可见。列表（`_visible_expeditions`）与出发校验（`_require_content`）
     走的是同一个判断，所以两边一起生效。顺带 patch `_require_content`，把"已通关"的
     报错从通用的「此内容目前仅对内测玩家开放」换成「已经通关过了，不会再开」。
  3. **首通额外发引路枫叶**。引路枫叶是玩家资源（`player.guide_leaves`）而不是物品，
     探索的 `rewards` / `fixed_rewards` 都装不下它，所以在 `withdraw_exploration` 外面
     补一次原子更新。幂等靠 `crossover_claims["one_time_clear:<id>"]`（账号级一次性
     登记表，皮肤解锁用的是同一张表，前缀不撞键）。

接线（应用装配层）：`install_one_time_dungeon()`。
**必须赶在 `service = GameService(...)` 之前调**——它 patch 的是 `GameService.__init__`，
装在构造之后那个实例永远不会被归零（卡片照样报原价、出发照样扣钱）。
"""

from __future__ import annotations

CLAIM_PREFIX = "one_time_clear:"

# 副本 id -> 首通附加奖励。免费 + 首通后隐藏对所有条目生效。
ONE_TIME_DUNGEONS: dict[str, dict] = {
    "midsummer_appeal_ground": {
        "name": "盛夏应援场",
        "guide_leaves": 2,
    },
}



# ----------------------------------------------------------------- 读状态


def _completed_expedition_ids(player) -> set[str]:
    stats = getattr(player, "achievement_stats", None)
    return set(getattr(stats, "completed_expedition_ids", None) or ())


def _completed_delve_ids(player) -> set[str]:
    stats = getattr(player, "achievement_stats", None)
    return set(getattr(stats, "completed_delve_ids", None) or ())




# ----------------------------------------------------------------- 安装


def _zero_entry_fees(service) -> None:
    """把免费路线的入场费改成 0。内容模型要求 > 0，所以只能在加载后改实例。"""

    expedition_map = getattr(service.content, "exploration_expedition_map", None) or {}
    for expedition_id in ONE_TIME_DUNGEONS:
        expedition = expedition_map.get(expedition_id)
        if expedition is not None and expedition.entry_fee != 0:
            expedition.entry_fee = 0


def install_one_time_dungeon() -> None:
    """patch 进 GameService：免费入场 + 首通隐藏 + 首通补发引路枫叶。幂等。"""

    from red_leaf_town.application import service as service_module
    from red_leaf_town.domain.models import ExplorationRunState

    game_service = service_module.GameService
    if getattr(game_service, "_one_time_dungeon_installed", False):
        return

    # ---------------------------------------------------------- ① 免费入场
    original_init = game_service.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _zero_entry_fees(self)

    game_service.__init__ = __init__

    # service 里另外两处 spend_coins（商店 entry.price * quantity、突破 ascension.coins）
    # 传的都是内容校验过的正数，所以这条「0 元放行」只会作用在免费路线上。
    original_spend_coins = service_module.spend_coins

    def spend_coins(player, amount):
        if int(amount) == 0:
            return None
        return original_spend_coins(player, amount)

    service_module.spend_coins = spend_coins

    # run 快照要落盘，entry_fee 必须是正数；免费路线补一个 1，钱不会被扣。
    original_run_init = ExplorationRunState.__init__

    def run_init(self, **data):
        entry_fee = data.get("entry_fee")
        if entry_fee is not None and int(entry_fee) <= 0:
            data["entry_fee"] = 1
        original_run_init(self, **data)

    ExplorationRunState.__init__ = run_init

    # ---------------------------------------------------------- ② 首通后隐藏
    original_visible = game_service._content_visible

    def _content_visible(self, player, definition):
        expedition_id = getattr(definition, "id", "")
        if (
            expedition_id in ONE_TIME_DUNGEONS
            and expedition_id in _completed_expedition_ids(player)
        ):
            return False
        return original_visible(self, player, definition)

    game_service._content_visible = _content_visible

    # `_require_content` 对"不可见"统一报 content_locked「此内容目前仅对内测玩家开放」，
    # 对一次性副本会误导（玩家会以为是内测白名单）。这里先认出来，换成准确文案。
    original_require_content = game_service._require_content

    def _require_content(self, player, definition):
        expedition_id = getattr(definition, "id", "")
        if (
            expedition_id in ONE_TIME_DUNGEONS
            and expedition_id in _completed_expedition_ids(player)
        ):
            raise service_module.GameError(
                "content_locked", f"{ONE_TIME_DUNGEONS[expedition_id]['name']}已经通关过了，不会再开", 403
            )
        return original_require_content(self, player, definition)

    game_service._require_content = _require_content

    # ------------------------------------------------- ③ 首通补发引路枫叶
    original_withdraw = game_service.withdraw_exploration

    def withdraw_exploration(self, oauth_sub):
        payload = original_withdraw(self, oauth_sub)
        result = payload.get("result") or {}
        expedition_id = str(result.get("expedition_id") or "")
        meta = ONE_TIME_DUNGEONS.get(expedition_id)
        if meta is None:
            return payload
        # 免费路线在 run 里留的是占位价，回执里按 0 报。
        result["entry_fee"] = 0
        if not result.get("completed"):
            return payload

        key = CLAIM_PREFIX + expedition_id
        leaves = int(meta.get("guide_leaves") or 0)

        def mutation(player):
            if player.crossover_claims.get(key):
                return {"granted": 0, "guide_leaves": player.guide_leaves}
            player.crossover_claims[key] = 1
            if leaves:
                player.guide_leaves += leaves
            return {"granted": leaves, "guide_leaves": player.guide_leaves}

        player, extra = self._update_by_sub(oauth_sub, mutation)
        result["guide_leaves"] = int(extra.get("granted") or 0)
        payload["state"] = self._snapshot(player, self._now())
        return payload

    game_service.withdraw_exploration = withdraw_exploration
    game_service._one_time_dungeon_installed = True
