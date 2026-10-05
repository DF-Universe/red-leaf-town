from __future__ import annotations

import os
import random
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from math import ceil, floor
from typing import NamedTuple
from uuid import uuid4

from .facilities import FacilitiesServiceMixin
from .sailing import SailingServiceMixin
from .summer import SummerServiceMixin
from red_leaf_town.summer_content import POINT_UNITS

from red_leaf_town.achievements import (
    achievement_snapshot,
    evaluate_achievements,
    reconcile_legacy_auto_rewards,
    record_production_collection,
)
from red_leaf_town.content import (
    ExplorationChoiceDefinition,
    ExplorationExpeditionDefinition,
    GameContent,
    GatheringDrawDefinition,
    RewardDefinition,
)
from red_leaf_town.domain import (
    AnimalState,
    CommissionBoardEntry,
    CommissionPayout,
    CommissionState,
    CraftingStationState,
    ExplorationEventLog,
    ExplorationRunState,
    FishCodexEntry,
    GachaDropRecord,
    GachaPoolProgressState,
    GachaRequestRecord,
    GatheringSiteState,
    LivestockFacilityState,
    MailMessage,
    MailReceiptState,
    MiningSiteState,
    OwnedPartnerState,
    PendingBigCatchState,
    PlayerState,
    PondState,
    PortalProgressState,
    PortalTributeProgress,
    ProductionResultSnapshot,
    ProductionTaskSnapshot,
    QQIdentity,
    RedemptionCode,
    TakenCommissionRecord,
    TaskPartnerSnapshot,
    TaskInputSnapshot,
    TaskOutputSnapshot,
    TaskQualitySnapshot,
)
from red_leaf_town.domain.models import CompletedCraftingTask
from red_leaf_town.domain.aquatic import (
    PondParameters,
    SlotError,
    add_fry,
    decayed_combo,
    deposit_into_slot,
    plan_ponds,
    pond_cycle_seconds,
    settle_ponds,
    slot_runtime_seconds,
)
from red_leaf_town.domain.livestock import (
    FacilityParameters,
    SpeciesParameters,
    affection_multiplier as animal_affection_multiplier,
    is_saturated,
    overflow_cap,
    plan_facility,
    quality_ability as animal_quality_ability,
    refund_value,
    roll_gene,
    settle_livestock,
    yield_per_cycle,
)
from red_leaf_town.domain.commissions import (
    commission_day,
    commission_day_end,
    commission_identifier,
    commission_seed,
    is_lucky_day,
    lucky_weekday,
)
from red_leaf_town.domain import (
    CarriedItemSnapshot,
    DelveBattleState,
    DelveEnemyState,
    DelveLoadoutSnapshot,
    DelveMemberState,
)
from red_leaf_town.domain import delve_battle
from red_leaf_town.domain.delve_battle import BattleError, PartyMember
from red_leaf_town.domain.economy import EconomyError, add_item, grant_coins, remove_item, spend_coins
from red_leaf_town.domain.monthly_card import extend_expiry, remaining_days
from red_leaf_town.domain.redemption import generate_code, normalize_code
from red_leaf_town.domain.progression import (
    consume_stamina,
    grant_experience,
    grant_stamina,
    normalize_crafting_stations,
    normalize_gathering_sites,
    normalize_livestock,
    normalize_mining_sites,
    normalize_plot_slots,
    normalize_ponds,
    refund_stamina,
    settle_stamina,
)
from red_leaf_town.domain.production import build_results, draw_count, draw_weighted_batches
from red_leaf_town.domain.quality import QUALITY_NAMES, quality_probabilities, roll_quality
from red_leaf_town.crossover import (
    CrossoverCampaign,
    get_crossover_campaign,
    list_crossover_campaigns,
)
from red_leaf_town.gacha_pools import GachaDefinition, load_gacha_pools
from red_leaf_town.recipe_unlocks import describe_recipe_unlock, evaluate_recipe_unlock
from red_leaf_town.partner_content import (
    GROWTH_CURVE_NAMES,
    INDUSTRY_NAMES,
    PartnerCatalog,
    PartnerDefinition,
    level_cap_for_breakthrough,
    load_partner_catalog,
)
from red_leaf_town.season_content import load_seasons, SeasonDefinition
from red_leaf_town.domain.season import scale_quantities
from red_leaf_town.partner_traits import execute_partner_traits, partner_trait_catalog
from red_leaf_town.rewards import serialize_reward, validate_reward_references
from red_leaf_town.story_assets import StoryAssetCatalog, load_story_asset_catalog
from red_leaf_town.story_content import StoryCatalog, load_story_catalog, serialize_script
from red_leaf_town.story_triggers import StoryContext, validate_story_cue

from .ports import (
    CommissionBoardRepository,
    MailRepository,
    PlayerRepository,
    RedemptionCodeRepository,
)


MAIL_LIST_LIMIT = 60
MAX_REDEMPTION_CODE_BATCH = 500
MAX_REDEMPTION_CODE_LIST = 500
# 钓鱼是高频接口且已知有脚本调用，限一个最小间隔，避免重试造成重复发放。
FISHING_MIN_INTERVAL_SECONDS = 1
FISHING_MAX_DRAWS = 40
ANIMAL_NICKNAME_LIMIT = 12
_UNSEEN = MailReceiptState(mail_id="placeholder")

# 伙伴邀约函可以自选的伙伴名单：当前所有已上线、有突破 0 立绘的伙伴。
# 这份名单刻意写死——以后新伙伴上线不会自动进入可选范围，需要人工确认后手动加入。
PARTNER_SELECT_IDS = frozenset({
    "ai_xinyu",
    "aishen",
    "aiweier",
    "aorui_jin",
    "babi",
    "banniang",
    "bufeng_zhuoying",
    "daian_zeer",
    "fein",
    "guidengdeng",
    "gujian_bu",
    "gujian_miao",
    "guqi",
    "guyu_yu",
    "hei_yuchuan",
    "hongkai",
    "leilei",
    "lengyue",
    "luo_nali",
    "mami",
    "manlong",
    "manlong_2",
    "nuanyu",
    "sunfeng_liya",
    "wujian",
    "xiang_hanyang",
    "xiang_hanyuan",
    "xiaoha",
    "xixi",
    "xiyue_kanna",
    "xuanyuan",
    "ye_huanan",
    "ye_lvsu",
    "zhuoyan",
})
# 选了低星伙伴的同行印记补偿，作为自选券的贴心缓冲：五星不补偿。
PARTNER_SELECT_MARK_COMPENSATION = {3: 2000, 4: 1500, 5: 0}


class FishingBatch(NamedTuple):
    """一次抽取的结果。size 是这一批里最大的那条鱼的体型，0 表示这一项不记体型。"""

    item_id: str
    quantity: int
    codex: bool
    size: float


class GameError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class GameService(FacilitiesServiceMixin, SummerServiceMixin, SailingServiceMixin):
    def __init__(
        self,
        content: GameContent,
        repository: PlayerRepository,
        *,
        commission_board: CommissionBoardRepository | None = None,
        mailbox: MailRepository | None = None,
        redemption_codes: RedemptionCodeRepository | None = None,
        clock: Callable[[], float] = time.time,
        rng: random.Random | random.SystemRandom | None = None,
        partner_catalog_loader: Callable[[], PartnerCatalog] = load_partner_catalog,
        story_catalog_loader: Callable[[], StoryCatalog] = load_story_catalog,
        story_asset_loader: Callable[[], StoryAssetCatalog] = load_story_asset_catalog,
        gacha_pool_loader: Callable[[], dict[str, GachaDefinition]] = load_gacha_pools,
        season_loader: Callable[[], dict[str, SeasonDefinition]] = load_seasons,
    ):
        self.content = content
        self.repository = repository
        # 转发池是唯一的跨玩家状态。不接的话委托照常刷新和提交，只是转发功能不开放。
        self.commission_board = commission_board
        # 信箱同样是玩家存档之外的共享存储。不接就当作小镇还没通邮，收件箱恒为空。
        self.mailbox_repository = mailbox
        # 激活码池。不接就是这个部署不发月卡码，兑换接口一律回「激活码无效」。
        self.redemption_codes = redemption_codes
        self.clock = clock
        self.rng = rng or random.SystemRandom()
        self.partner_catalog_loader = partner_catalog_loader
        self.story_catalog_loader = story_catalog_loader
        self.story_asset_loader = story_asset_loader
        self.gacha_pool_loader = gacha_pool_loader
        self.season_loader = season_loader

    def _world_snapshot(self, now: int) -> dict:
        world = self.content.world
        local_timezone = timezone(timedelta(seconds=world.utc_offset_seconds))
        local_date = datetime.fromtimestamp(now, local_timezone).date()
        day_index = (now + world.utc_offset_seconds) // 86400
        weather = world.weather_cycle[day_index % len(world.weather_cycle)]
        return {
            "day": local_date.isoformat(),
            "season": {"id": world.season_id, "name": world.season_name},
            "weather": weather.model_dump(),
        }

    def _execute_partner_trait_phase(
        self,
        partner_ids: list[str],
        phase: str,
        context: dict,
    ) -> list[str]:
        catalog = self.partner_catalog_loader()
        executed: list[str] = []
        context["phase"] = phase
        for partner_id in partner_ids:
            definition = catalog.partner_map.get(partner_id)
            if definition is None:
                continue
            context["source_partner_id"] = partner_id
            executed.extend(execute_partner_traits(definition.trait_codes, context))
        context.pop("source_partner_id", None)
        return executed

    def _gacha_pool(self, pool_id: str) -> GachaDefinition:
        pool = self.gacha_pool_loader().get(pool_id)
        if pool is None:
            raise GameError("gacha_pool_not_found", "招募池不存在", 404)
        return pool

    def ensure_player(self, oauth_sub: str, display_name: str) -> PlayerState:
        return self.repository.ensure_player(oauth_sub, display_name or "红叶镇居民", self._now())

    def snapshot_by_sub(self, oauth_sub: str) -> dict:
        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        return self._settled_snapshot(player.player_id)

    def snapshot_by_identity(self, identity: QQIdentity) -> dict:
        player_id = self.repository.player_id_for_identity(identity)
        if not player_id:
            raise GameError("identity_not_bound", "这个 QQ 身份尚未绑定红叶镇角色", 404)
        return self._settled_snapshot(player_id)

    def buy(self, oauth_sub: str, shop_id: str, quantity: int) -> dict:
        if quantity < 1 or quantity > 99:
            raise GameError("invalid_quantity", "购买数量需在 1 到 99 之间")
        entry = self.content.shop_map.get(shop_id)
        if not entry:
            raise GameError("shop_item_not_found", "商品不存在", 404)

        def mutation(player: PlayerState):
            self._settle(player, self._now())
            self._require_content(player, entry)
            if player.level < entry.min_level:
                raise GameError("content_locked", f"达到 {entry.min_level} 级后解锁")
            try:
                spend_coins(player, entry.price * quantity)
                add_item(player, entry.item_id, quantity)
            except EconomyError as exc:
                raise GameError("economy_error", str(exc)) from exc
            return {"item_id": entry.item_id, "quantity": quantity}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    # --------------------------------------------------------------- 探索（采运 / 勘探）

    def start_exploration(
        self,
        oauth_sub: str,
        expedition_id: str,
        partner_ids: list[str],
        leader_partner_id: str,
        loadout: dict[str, dict] | None = None,
        carried_items: list[dict] | None = None,
    ) -> dict:
        expedition = self.content.exploration_expedition_map.get(str(expedition_id or "").strip())
        if expedition is None:
            raise GameError("exploration_not_found", "没有这条探索路线", 404)
        party = [str(partner_id).strip() for partner_id in partner_ids if str(partner_id).strip()]
        leader_partner_id = str(leader_partner_id or "").strip()
        if not 1 <= len(party) <= 3 or len(party) != len(set(party)):
            raise GameError("exploration_party_invalid", "探索队伍需要一至三名不同伙伴")
        if expedition.kind == "delve" and len(party) != delve_battle.PARTY_SIZE:
            raise GameError(
                "exploration_party_invalid",
                f"探秘副本需要正好 {delve_battle.PARTY_SIZE} 名伙伴",
            )
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            if player.exploration_run is not None:
                raise GameError("exploration_active", "当前已有一支队伍在探索中", 409)
            if expedition.beta and not self._is_beta_player(player):
                raise GameError("content_locked", "这条路线还在内测中")
            self._require_content(player, expedition)
            if player.level < expedition.min_level:
                raise GameError("content_locked", f"达到 {expedition.min_level} 级后解锁")
            voyage = player.sailing.active_run
            if voyage and voyage.ready_at > now and set(party) & set(voyage.partner_ids):
                raise GameError("partner_locked", "伙伴正在出海，暂时不能参与探索", 409)
            if set(party) & set(player.refining.assigned_partner_ids):
                if player.refining.occupied:
                    raise GameError("partner_locked", "精制房清空后伙伴才能参与探索", 409)
                self._settle_refining(player, now)
                player.refining.assigned_partner_ids = []
            ability = self._exploration_party_ability(player, party, leader_partner_id)
            if expedition.kind == "delve":
                party_loadout = self._build_delve_loadout(player, party, loadout)
                combat_party = self._build_delve_party(player, party, party_loadout)
                frozen_items = self._freeze_carried_items(player, carried_items)
            else:
                party_loadout, combat_party, frozen_items = {}, {}, []
            try:
                spend_coins(player, expedition.entry_fee)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            run = ExplorationRunState(
                run_id=str(uuid4()),
                expedition_id=expedition.id,
                expedition_kind=expedition.kind,
                season_bonus=self._season_bonus(expedition, "leader_bonus", [leader_partner_id], now),
                partner_ids=party,
                leader_partner_id=leader_partner_id,
                exploration_ability=ability,
                talent_check_bonus=int(self._talent_modifier(player, "exploration_check_bonus")),
                entry_fee=expedition.entry_fee,
                started_at=now,
                current_event_id=self._pick_exploration_event(expedition, None, 1),
                current_rolls=[self.rng.randint(1, 20), self.rng.randint(1, 20)],
                loadout=party_loadout,
                combat_party=combat_party,
                carried_items=frozen_items,
            )
            player.exploration_run = run
            return {
                "expedition_id": expedition.id,
                "entry_fee": expedition.entry_fee,
                "exploration_ability": ability,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def resolve_exploration_event(
        self,
        oauth_sub: str,
        choice_id: str,
        actor_partner_id: str = "",
    ) -> dict:
        choice_id = str(choice_id or "").strip()
        actor_partner_id = str(actor_partner_id or "").strip()
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            run = player.exploration_run
            if run is None:
                raise GameError("exploration_inactive", "当前没有进行中的探索", 409)
            if run.status != "active":
                raise GameError("exploration_completed", "路线已经走完，请结算返程", 409)
            expedition = self.content.exploration_expedition_map.get(run.expedition_id)
            if expedition is None:
                raise GameError("exploration_content_missing", "探索内容已经失效", 409)
            event = expedition.event_map.get(run.current_event_id)
            if event is None:
                raise GameError("exploration_event_missing", "当前事件已经失效", 409)
            choice = event.choice_map.get(choice_id)
            if choice is None:
                raise GameError("exploration_choice_invalid", "这个事件没有该行动", 404)

            context = {
                "industry": "exploration",
                "exploration_type": expedition.kind,
                "expedition_id": expedition.id,
                "event_id": event.id,
                "choice_id": choice.id,
                "depth": run.depth,
                "selected_actor_partner_id": actor_partner_id,
                "check_attribute": choice.check.attribute if choice.check else None,
                "check_mode": choice.check.mode if choice.check else None,
                "check_bonus": run.talent_check_bonus,
                "dice_adjustment": 0,
                "critical_success_min": 20,
                "ordinary_failure_stamina_reduction": 0,
                "critical_failure_stamina_reduction": 0,
                "leader_partner_id": run.leader_partner_id,
                "trait_usage": run.trait_usage,
                "trait_usage_consumptions": [],
                "route_stamina_multiplier": 1.0,
                "reward_quantity_multiplier": 1.0,
                "quality_ability_bonus": 0,
                "applied_effects": [],
                "world": self._world_snapshot(now),
            }
            self._prime_exploration_check_context(run, choice, context)
            self._execute_partner_trait_phase(run.partner_ids, "exploration_event", context)
            check_result = self._resolve_exploration_check(run, choice, context)
            success = check_result["success"]
            degree = check_result["degree"]
            if degree == "critical_success":
                outcome = choice.critical_success or choice.success
            elif degree == "critical_failure":
                outcome = choice.critical_failure or choice.failure
            else:
                outcome = choice.success if success or choice.failure is None else choice.failure
            stamina_surcharge = max(
                0,
                outcome.stamina_surcharge - self._exploration_failure_stamina_reduction(context, degree),
            )
            cost, route_raw, action_total = self._exploration_choice_cost(
                run,
                choice,
                stamina_surcharge,
                float(context["route_stamina_multiplier"]),
            )
            try:
                consume_stamina(player, cost, self.content, now)
            except ValueError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            # 消耗先落地再记特性用量，行动因体力不足失败时不白烧一次限定次数。
            for usage_key in context["trait_usage_consumptions"]:
                run.trait_usage[usage_key] = run.trait_usage.get(usage_key, 0) + 1

            if choice.battle is not None:
                # 战斗节点不在这里推进深度：奖励和下一层都留到打赢之后再结算。
                run.route_stamina_raw = route_raw
                run.action_stamina_spent = action_total
                run.stamina_spent += cost
                run.season_pending_units = cost * POINT_UNITS if self._season_open(expedition, now) else 0
                outcome = self._begin_delve_battle(run, event, choice)
                if outcome != "ongoing":
                    self._finish_summer_battle(player, run, expedition, now)
                if outcome == "wiped":
                    # 敌人抢到先攻并在我方出手前就打光了队伍。不结算这一步的话，
                    # 战斗会挂在那里，之后每个行动都被判成"不是我方的回合"。
                    player.exploration_run = None
                return {
                    "event_id": event.id,
                    "choice_id": choice.id,
                    "success": True,
                    "text": (
                        "队伍还没来得及还手就被打散了。"
                        if outcome == "wiped"
                        else event.description
                    ),
                    "stamina_cost": cost,
                    "drops": [],
                    "battle_started": outcome == "ongoing",
                    "outcome": outcome,
                    "completed": False,
                }

            self._award_summer_units(player, expedition, cost * POINT_UNITS, now)
            rewards = self._settle_exploration_outcome(
                run,
                expedition,
                outcome,
                now,
                quality_bonus=int(context["quality_ability_bonus"]),
                quantity_multiplier=float(context["reward_quantity_multiplier"]),
                applied_effects=context["applied_effects"],
            )
            run.route_stamina_raw = route_raw
            run.action_stamina_spent = action_total
            run.stamina_spent += cost
            run.next_route_discount = outcome.next_route_discount
            run.depth += 1
            run.event_counts[event.id] = run.event_counts.get(event.id, 0) + 1
            run.logs.append(ExplorationEventLog(
                depth=run.depth,
                event_id=event.id,
                choice_id=choice.id,
                success=success,
                degree=degree,
                check_attribute=check_result["check_attribute"],
                check_mode=check_result["check_mode"],
                dice_mode=check_result["dice_mode"],
                rolls=check_result["rolls"],
                kept_roll=check_result["kept_roll"],
                modifier=check_result["modifier"],
                total=check_result["total"],
                actor_partner_ids=check_result["actor_partner_ids"],
                text=outcome.text,
                stamina_cost=cost,
                rewards=rewards,
                applied_effects=list(context["applied_effects"]),
            ))
            if run.depth >= expedition.max_depth:
                run.status = "completed"
            else:
                run.current_event_id = self._pick_exploration_event(expedition, run, run.depth + 1)
                run.current_rolls = [self.rng.randint(1, 20), self.rng.randint(1, 20)]
            return {
                "event_id": event.id,
                "choice_id": choice.id,
                "success": success,
                **check_result,
                "text": outcome.text,
                "stamina_cost": cost,
                "drops": [self._result_snapshot(reward) for reward in rewards],
                "completed": run.status == "completed",
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def resolve_delve_battle_action(
        self,
        oauth_sub: str,
        action: str,
        target: str = "",
        item_id: str = "",
        item_quality: int = 0,
    ) -> dict:
        """走一个我方单位的行动，然后把后面的敌方回合一口气跑到下一个我方回合。"""

        action = str(action or "").strip()
        target = str(target or "").strip()
        item_id = str(item_id or "").strip()
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            run = player.exploration_run
            if run is None:
                raise GameError("exploration_inactive", "当前没有进行中的探索", 409)
            battle = run.battle
            if battle is None:
                raise GameError("delve_battle_inactive", "当前没有进行中的战斗", 409)
            expedition = self.content.exploration_expedition_map.get(run.expedition_id)
            if expedition is None:
                raise GameError("exploration_content_missing", "探索内容已经失效", 409)
            event = expedition.event_map.get(battle.event_id)
            choice = event.choice_map.get(battle.choice_id) if event else None
            if event is None or choice is None:
                raise GameError("exploration_event_missing", "当前事件已经失效", 409)

            members = self._delve_members(run)
            # 只把这次行动新产生的日志回给前端，逐条播放才不会重播上一回合。
            log_cursor = len(battle.logs)
            actor_id = delve_battle.current_actor(battle, members)
            if actor_id not in members:
                raise GameError("delve_battle_not_your_turn", "现在不是我方的回合", 409)
            actor = members[actor_id]

            fled = False
            try:
                if action == "attack":
                    delve_battle.member_attack(self.rng, battle, actor, target)
                elif action == "advantage":
                    delve_battle.member_prepare_advantage(battle, actor)
                elif action == "item":
                    carried = next(
                        (
                            entry
                            for entry in run.carried_items
                            if entry.item_id == item_id and entry.quality == int(item_quality) and entry.quantity > 0
                        ),
                        None,
                    )
                    definition = self.content.item_map.get(item_id)
                    if carried is None or definition is None or definition.delve_use is None:
                        raise GameError("delve_item_invalid", "队伍没有带这件道具", 404)
                    receiver = members.get(target or actor_id)
                    if receiver is None:
                        raise GameError("delve_target_invalid", "这个伙伴不在队伍里", 404)
                    delve_battle.member_heal(
                        self.rng,
                        battle,
                        actor,
                        receiver,
                        definition.name,
                        definition.delve_use.dice,
                        definition.delve_use.flat
                        + max(0, carried.quality - 1) * definition.delve_use.quality_bonus,
                    )
                    carried.quantity -= 1
                    if carried.quantity <= 0:
                        run.carried_items.remove(carried)
                elif action == "flee":
                    fled = delve_battle.attempt_flee(self.rng, battle, actor, members)
                else:
                    raise GameError("delve_action_invalid", "没有这个战斗行动", 400)
            except BattleError as exc:
                raise GameError("delve_action_invalid", str(exc)) from exc

            if fled:
                self._finish_summer_battle(player, run, expedition, now)
                run.battle = None
                self._advance_exploration_node(
                    run,
                    expedition,
                    event,
                    choice,
                    text="队伍放弃了这次遭遇，绕开继续前进。",
                    rewards=[],
                )
                return {
                    "action": action,
                    "outcome": "fled",
                    "logs": [entry.model_dump() for entry in battle.logs[log_cursor:]],
                    "drops": [],
                    "completed": run.status == "completed",
                }

            delve_battle.advance_turn(battle)
            outcome = self._run_delve_enemy_turns(battle, members)
            drops: list = []
            if outcome in ("victory", "wiped"):
                self._finish_summer_battle(player, run, expedition, now)
            if outcome == "victory":
                stats = player.achievement_stats
                stats.delve_wins[run.expedition_id] = stats.delve_wins.get(run.expedition_id, 0) + 1
                run.boss_defeated = run.boss_defeated or any(enemy.boss for enemy in battle.enemies)
                run.battle = None
                drops = self._settle_exploration_outcome(run, expedition, choice.success, now)
                self._advance_exploration_node(
                    run,
                    expedition,
                    event,
                    choice,
                    text=choice.success.text,
                    rewards=drops,
                )
            elif outcome == "wiped":
                # 全灭只丢冻结的战利品，装备和伙伴都不损失。
                player.exploration_run = None
            return {
                "action": action,
                "outcome": outcome,
                "logs": [entry.model_dump() for entry in battle.logs[log_cursor:]],
                "drops": [self._result_snapshot(reward) for reward in drops],
                "completed": outcome == "victory" and run.status == "completed",
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def _begin_delve_battle(self, run, event, choice) -> str:
        """建立战斗并把开场的敌方回合跑完，返回这时候的战斗结果。"""

        definitions = self.content.delve_enemy_map
        enemies: list[DelveEnemyState] = []
        initiative: dict[str, int] = {}
        for index, enemy_id in enumerate(choice.battle.enemy_ids):
            definition = definitions.get(enemy_id)
            if definition is None:
                raise GameError("exploration_content_missing", "遭遇的敌人配置已经失效", 409)
            key = f"{enemy_id}#{index + 1}"
            suffix = f"·{index + 1}" if choice.battle.enemy_ids.count(enemy_id) > 1 else ""
            enemies.append(DelveEnemyState(
                key=key,
                enemy_id=definition.id,
                name=f"{definition.name}{suffix}",
                icon=definition.icon,
                max_hp=definition.max_hp,
                hp=definition.max_hp,
                armor_class=definition.armor_class,
                attacks_per_turn=definition.attacks_per_turn,
                boss=definition.boss,
            ))
            initiative[key] = definition.initiative_bonus

        members = self._delve_members(run)
        battle = DelveBattleState(
            battle_id=str(uuid4()),
            event_id=event.id,
            choice_id=choice.id,
            depth=run.depth + 1,
            can_flee=choice.battle.can_flee,
            flee_dc=choice.battle.flee_dc,
            enemies=enemies,
            order=delve_battle.build_initiative(self.rng, members, enemies, initiative),
            advantage_uses_left={
                partner_id: member.loadout.advantage_uses for partner_id, member in members.items()
            },
        )
        run.battle = battle
        outcome = self._run_delve_enemy_turns(battle, members)
        if outcome != "ongoing":
            run.battle = None
        return outcome

    def _run_delve_enemy_turns(self, battle, members: dict) -> str:
        """一路跑到下一个我方回合，或者战斗结束。"""

        definitions = self.content.delve_enemy_map
        for _ in range(len(battle.order) * 12):
            outcome = delve_battle.battle_outcome(battle, members)
            if outcome != "ongoing":
                return outcome
            actor_id = delve_battle.current_actor(battle, members)
            if not actor_id or actor_id in members:
                return "ongoing"
            enemy = next((entry for entry in battle.enemies if entry.key == actor_id), None)
            definition = definitions.get(enemy.enemy_id) if enemy else None
            if enemy is None or definition is None:
                raise GameError("exploration_content_missing", "遭遇的敌人配置已经失效", 409)
            delve_battle.enemy_turn(
                self.rng,
                battle,
                enemy,
                [attack.model_dump() for attack in definition.attacks],
                members,
                definition.attacks_per_turn,
            )
            delve_battle.advance_turn(battle)
        return delve_battle.battle_outcome(battle, members)

    def _advance_exploration_node(self, run, expedition, event, choice, *, text: str, rewards: list) -> None:
        """战斗解决之后补上这一节点的记录并抽下一层，和普通事件走完的收尾保持一致。"""

        run.depth += 1
        run.event_counts[event.id] = run.event_counts.get(event.id, 0) + 1
        run.logs.append(ExplorationEventLog(
            depth=run.depth,
            event_id=event.id,
            choice_id=choice.id,
            success=True,
            degree="automatic_success",
            text=text,
            stamina_cost=0,
            rewards=rewards,
        ))
        if run.depth >= expedition.max_depth:
            run.status = "completed"
        else:
            run.current_event_id = self._pick_exploration_event(expedition, run, run.depth + 1)
            run.current_rolls = [self.rng.randint(1, 20), self.rng.randint(1, 20)]

    def withdraw_exploration(self, oauth_sub: str) -> dict:
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            run = player.exploration_run
            if run is None:
                raise GameError("exploration_inactive", "当前没有进行中的探索", 409)
            if run.battle is not None:
                raise GameError("delve_battle_active", "战斗结束之前没法撤离", 409)
            rewards = list(run.pending_rewards)
            if run.season_bonus:
                for reward, quantity in zip(rewards, scale_quantities(
                    [entry.quantity for entry in rewards], run.season_bonus.multiplier,
                )):
                    reward.quantity = quantity
            if run.status == "completed":
                stats = player.achievement_stats
                stats.completed_expedition_ids = sorted(set(stats.completed_expedition_ids) | {run.expedition_id})
                if run.expedition_kind == "delve" and run.boss_defeated:
                    stats.completed_delve_ids = sorted(set(stats.completed_delve_ids) | {run.expedition_id})
            for reward in rewards:
                add_item(player, reward.item_id, reward.quantity, reward.quality)
            for fixed in run.pending_fixed_rewards:
                add_item(player, fixed.item_id, fixed.quantity, fixed.quality)
            # 没用完的道具原样退回仓库。
            for carried in run.carried_items:
                if carried.quantity > 0:
                    add_item(player, carried.item_id, carried.quantity, carried.quality)
            result = {
                "expedition_id": run.expedition_id,
                "completed": run.status == "completed",
                "depth": run.depth,
                "stamina_spent": run.stamina_spent,
                "entry_fee": run.entry_fee,
                "drops": [self._result_snapshot(reward) for reward in rewards],
                "equipment_drops": [
                    {
                        **fixed.model_dump(),
                        "item": self.content.item_map[fixed.item_id].model_dump(),
                    }
                    for fixed in run.pending_fixed_rewards
                    if fixed.item_id in self.content.item_map
                ],
                "returned_items": [
                    {
                        **carried.model_dump(),
                        "item": self.content.item_map[carried.item_id].model_dump(),
                    }
                    for carried in run.carried_items
                    if carried.quantity > 0 and carried.item_id in self.content.item_map
                ],
            }
            if run.season_bonus:
                result["leader_bonus"] = run.season_bonus.model_dump()
            player.exploration_run = None
            return result

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def plant(self, oauth_sub: str, slot: int, crop_id: str, task_item_id: str = "") -> dict:
        crop = self.content.crop_map.get(crop_id)
        if not crop:
            raise GameError("crop_not_found", "作物不存在", 404)
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            self._require_content(player, crop)
            if player.level < crop.min_level:
                raise GameError("content_locked", f"达到 {crop.min_level} 级后解锁")
            plot = self._plot(player, slot)
            if not plot.empty:
                raise GameError("plot_occupied", "这块土地已经种有作物")
            units = self._farm_units(player, slot)
            if any(not unit.empty for unit in units):
                raise GameError("plot_occupied", "这块田已经种有作物")
            if task_item_id and player.task_items.get(task_item_id, 0) < len(units):
                raise GameError("task_item_insufficient", f"本田需要 {len(units)} 份生产道具")
            try:
                remove_item(player, crop.seed_item_id, len(units))
                consume_stamina(player, crop.stamina_cost * len(units), self.content, now)
            except (EconomyError, ValueError) as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            for unit in units:
                source = unit.model_copy(update={"assigned_partner_ids": plot.assigned_partner_ids})
                task_snapshot = self._build_farming_task_snapshot(player, source, crop, now, task_item_id)
                unit.crop_id = crop.id
                unit.planted_at = now
                unit.ready_at = task_snapshot.ready_at
                unit.task_snapshot = task_snapshot
                unit.task_results = []
            return {
                "slot": slot,
                "crop_id": crop.id,
                "ready_at": plot.ready_at,
                "levels": [],
                "base_duration": task_snapshot.base_duration,
                "final_duration": task_snapshot.final_duration,
                "time_saved": task_snapshot.base_duration - task_snapshot.final_duration,
                "total_ability": task_snapshot.total_ability,
                "time_efficiency": task_snapshot.time_efficiency,
                "quality_ability": task_snapshot.quality_parameters.ability,
                "quality_probabilities": task_snapshot.quality_parameters.probabilities,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def harvest(self, oauth_sub: str, slot: int) -> dict:
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            plot = self._plot(player, slot)
            if plot.empty:
                raise GameError("plot_empty", "这块土地还没有作物")
            if plot.ready_at > now:
                raise GameError("crop_not_ready", "作物还没有成熟")
            units = self._farm_units(player, slot)
            if any(unit.ready_at > now or not unit.task_results for unit in units):
                raise GameError("crop_not_ready", "作物尚未全部成熟")
            results = [result for unit in units for result in unit.task_results]
            harvest_xp = 0
            partner_experience = []
            for unit in units:
                task = unit.task_snapshot
                crop = self.content.crop_map.get(unit.crop_id)
                harvest_xp += task.harvest_xp if task else crop.harvest_xp if crop else 0
                partner_experience.extend(self._grant_task_partner_experience(player, task))
                record_production_collection(player, "farming", unit.crop_id, unit.task_results)
                unit.crop_id = ""
                unit.planted_at = 0
                unit.ready_at = 0
                unit.task_snapshot = None
                unit.task_results = []
            for result in results:
                add_item(player, result.item_id, result.quantity, result.quality)
            levels = grant_experience(player, harvest_xp, self.content, max_level=self._player_level_cap(player))
            return {
                "slot": slot,
                "item_id": results[0].item_id,
                "quantity": sum(result.quantity for result in results),
                "drops": [self._result_snapshot(result) for result in results],
                "experience": harvest_xp,
                "levels": levels,
                "partner_experience": partner_experience,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def assign_gathering_partner(self, oauth_sub: str, site_id: str, partner_id: str = "") -> dict:
        partner_id = str(partner_id or "").strip()
        now = self._now()
        catalog = self.partner_catalog_loader()

        def mutation(player: PlayerState):
            self._settle(player, now)
            site = self._gathering_site(player, site_id, allow_closed=not partner_id)
            desired_ids = [partner_id] if partner_id else []
            if site.assigned_partner_ids == desired_ids:
                return {"site_id": site_id, "partner_id": partner_id or None, "changed": False}
            if (
                site.task_snapshot is not None
                and site.task_snapshot.ready_at > now
                and not self._task_releases_partner(site.task_snapshot)
            ):
                raise GameError("partner_assignment_locked", "采集进行中，不能调整这个采集点的伙伴", 409)

            locked_until = self._partner_lock_deadlines(player, now)
            affected_ids = set(site.assigned_partner_ids)
            if partner_id:
                affected_ids.add(partner_id)
            if any(locked_until.get(candidate, 0) > now for candidate in affected_ids):
                raise GameError("partner_locked", "伙伴正在参与进行中的任务，暂时不能移动", 409)

            if partner_id:
                owned = next((entry for entry in player.owned_partners if entry.partner_id == partner_id), None)
                definition = catalog.partner_map.get(partner_id)
                if owned is None:
                    raise GameError("partner_not_owned", "你还没有这个伙伴", 404)
                if definition is None:
                    raise GameError("partner_not_found", "伙伴配置不存在", 404)
                if not any(tendency.industry == "gathering" for tendency in definition.tendencies):
                    raise GameError("partner_tendency_mismatch", "这个伙伴没有采集倾向", 409)

            if partner_id:
                self._clear_partner_assignment(player, partner_id)
                if player.fishing.companion_partner_id == partner_id:
                    player.fishing.companion_partner_id = ""
            site.assigned_partner_ids = desired_ids
            if self._industry_assigned_count(player, "gathering") > self._industry_partner_capacity(player, "gathering"):
                raise GameError("partner_capacity_reached", "当前采集伙伴编制已满", 409)
            return {"site_id": site_id, "partner_id": partner_id or None, "changed": True}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def start_gathering(self, oauth_sub: str, site_id: str, task_id: str, task_item_id: str = "") -> dict:
        task = self.content.gathering_task_map.get(task_id)
        if task is None or task.site_id != site_id:
            raise GameError("gathering_task_not_found", "这个采集点没有该任务", 404)
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            site = self._gathering_site(player, site_id)
            self._require_content(player, task)
            if player.level < task.min_level:
                raise GameError("content_locked", f"达到 {task.min_level} 级后解锁")
            if not site.empty:
                raise GameError("gathering_site_occupied", "这个采集点已有任务")
            if len(site.assigned_partner_ids) != 1:
                raise GameError("gathering_partner_required", "必须先派一名具有采集倾向的伙伴前往", 409)
            if self._industry_assigned_count(player, "gathering") > self._industry_partner_capacity(player, "gathering"):
                raise GameError("partner_capacity_reached", "当前采集伙伴编制已满", 409)
            headline_output = task.headline_output
            snapshot = self._build_production_task_snapshot(
                player=player,
                assigned_partner_ids=site.assigned_partner_ids,
                industry="gathering",
                content_id=task.id,
                production_slot_id=f"gathering:site:{site.site_id}",
                now=now,
                base_duration=task.duration_seconds,
                minimum_duration=task.minimum_duration_seconds,
                time_difficulty=task.time_difficulty,
                produce_item_id=headline_output.item_id,
                yield_min=headline_output.quantity_min,
                yield_max=headline_output.quantity_max,
                harvest_xp=task.collect_xp,
                stamina_cost=task.stamina_cost,
                quality=task.quality,
                output_pool=[TaskOutputSnapshot.model_validate(output.model_dump()) for output in task.outputs],
                draws=task.draws,
                task_item_id=task_item_id,
            )
            try:
                consume_stamina(player, task.stamina_cost, self.content, now)
            except ValueError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            snapshot.season_bonus = self._season_bonus(
                task, "gathering_bonus", snapshot.assigned_partner_ids, now,
            )
            site.task_snapshot = snapshot
            site.task_results = []
            return {
                "site_id": site_id,
                "task_id": task_id,
                "ready_at": snapshot.ready_at,
                "base_duration": snapshot.base_duration,
                "final_duration": snapshot.final_duration,
                "total_ability": snapshot.total_ability,
                "quality_ability": snapshot.quality_parameters.ability,
                "draw_count": snapshot.draw_count,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def collect_gathering(self, oauth_sub: str, site_id: str) -> dict:
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            site = self._gathering_site(player, site_id, allow_closed=True)
            if site.task_snapshot is None:
                raise GameError("gathering_site_empty", "这个采集点没有进行中的任务")
            if site.task_snapshot.ready_at > now:
                raise GameError("gathering_not_ready", "伙伴还没有完成采集")
            results = site.task_results
            if not results:
                raise GameError("task_content_missing", "采集任务配置缺失，请联系管理员", 409)
            season_bonus = site.task_snapshot.season_bonus
            task_definition = self.content.gathering_task_map.get(site.task_snapshot.content_id)
            season = self._season(task_definition)
            if season and season.is_open(site.task_snapshot.started_at):
                self._award_summer_units(player, task_definition, site.task_snapshot.final_duration, now)
            harvest_xp = site.task_snapshot.harvest_xp
            task_id = site.task_snapshot.content_id
            for result in results:
                add_item(player, result.item_id, result.quantity, result.quality)
            levels = grant_experience(player, harvest_xp, self.content, max_level=self._player_level_cap(player))
            partner_experience = self._grant_task_partner_experience(player, site.task_snapshot)
            record_production_collection(player, "gathering", task_id, results)
            site.task_snapshot = None
            site.task_results = []
            return {
                "site_id": site_id,
                "season_bonus": season_bonus.model_dump() if season_bonus else None,
                "drops": [self._result_snapshot(result) for result in results],
                "experience": harvest_xp,
                "levels": levels,
                "partner_experience": partner_experience,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def assign_crafting_partner(self, oauth_sub: str, station_id: str, partner_id: str = "") -> dict:
        partner_id = str(partner_id or "").strip()
        now = self._now()
        catalog = self.partner_catalog_loader()

        def mutation(player: PlayerState):
            self._settle(player, now)
            station = self._crafting_station(player, station_id)
            desired_ids = [partner_id] if partner_id else []
            if station.assigned_partner_ids == desired_ids:
                return {"station_id": station_id, "partner_id": partner_id or None, "changed": False}
            if (
                station.task_snapshot is not None
                and station.task_snapshot.ready_at > now
                and not self._task_releases_partner(station.task_snapshot)
            ):
                raise GameError("partner_assignment_locked", "加工进行中，不能调整这个工位的伙伴", 409)

            locked_until = self._partner_lock_deadlines(player, now)
            affected_ids = set(station.assigned_partner_ids)
            if partner_id:
                affected_ids.add(partner_id)
            if any(locked_until.get(candidate, 0) > now for candidate in affected_ids):
                raise GameError("partner_locked", "伙伴正在参与进行中的任务，暂时不能移动", 409)

            if partner_id:
                owned = next((entry for entry in player.owned_partners if entry.partner_id == partner_id), None)
                definition = catalog.partner_map.get(partner_id)
                if owned is None:
                    raise GameError("partner_not_owned", "你还没有这个伙伴", 404)
                if definition is None:
                    raise GameError("partner_not_found", "伙伴配置不存在", 404)
                if not any(tendency.industry == "crafting" for tendency in definition.tendencies):
                    raise GameError("partner_tendency_mismatch", "这个伙伴没有加工倾向", 409)

            if partner_id:
                self._clear_partner_assignment(player, partner_id)
                if player.fishing.companion_partner_id == partner_id:
                    player.fishing.companion_partner_id = ""
            station.assigned_partner_ids = desired_ids
            if self._industry_assigned_count(player, "crafting") > self._industry_partner_capacity(player, "crafting"):
                raise GameError("partner_capacity_reached", "当前加工伙伴编制已满", 409)
            return {"station_id": station_id, "partner_id": partner_id or None, "changed": True}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def start_crafting(self, oauth_sub: str, station_id: str, recipe_id: str, task_item_id: str = "", quantity: int = 1) -> dict:
        if type(quantity) is not int or not 1 <= quantity <= 99:
            raise GameError("invalid_quantity", "加工次数必须为 1 到 99 的整数")
        recipe = self.content.recipe_map.get(recipe_id)
        if recipe is None or recipe.station_id != station_id:
            raise GameError("recipe_not_found", "这个工位没有该配方", 404)
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            station = self._crafting_station(player, station_id)
            condition = recipe.unlock_condition
            self._require_content(player, recipe)
            if not evaluate_recipe_unlock(player, condition.hook, condition.params):
                raise GameError(
                    "recipe_locked",
                    f"配方尚未解锁：{describe_recipe_unlock(condition.hook, condition.params)}",
                    409,
                )
            if not station.empty:
                raise GameError("crafting_station_occupied", "这个工位已有加工任务")
            if self._industry_assigned_count(player, "crafting") > self._industry_partner_capacity(player, "crafting"):
                raise GameError("partner_capacity_reached", "当前加工伙伴编制已满", 409)
            task_item_id_clean = str(task_item_id or "").strip()
            if task_item_id_clean:
                item = self.content.task_item_map.get(task_item_id_clean)
                if item is None or item.timing != "start":
                    raise GameError("task_item_invalid", "这个道具不能在开工时使用")
                if item.eligible_industries and "crafting" not in item.eligible_industries:
                    raise GameError("task_item_industry_mismatch", "这个道具不能用于当前产业")
            item_count = min(quantity, player.task_items.get(task_item_id_clean, 0))
            try:
                consume_stamina(player, recipe.stamina_cost * quantity, self.content, now)
            except ValueError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            tasks = []
            next_start = now
            for index in range(quantity):
                consumed_inputs = self._consume_recipe_inputs(player, recipe)
                task = self._build_production_task_snapshot(
                    player=player,
                    assigned_partner_ids=station.assigned_partner_ids,
                    industry="crafting",
                    content_id=recipe.id,
                    production_slot_id=f"crafting:station:{station.station_id}",
                    now=next_start,
                    base_duration=recipe.duration_seconds,
                    time_difficulty=recipe.time_difficulty,
                    produce_item_id=recipe.produce_item_id,
                    yield_min=recipe.produce_quantity,
                    yield_max=recipe.produce_quantity,
                    harvest_xp=recipe.collect_xp,
                    stamina_cost=recipe.stamina_cost,
                    quality=recipe.quality,
                    consumed_inputs=consumed_inputs,
                    task_item_id=task_item_id_clean if index < item_count else "",
                )
                tasks.append(task)
                next_start = task.ready_at
            snapshot = tasks[0]
            station.task_snapshot = snapshot
            station.task_results = []
            station.queued_tasks = tasks[1:]
            station.completed_tasks = []
            station.queue_total = quantity
            station.collected_count = 0
            return {
                "station_id": station_id,
                "recipe_id": recipe_id,
                "ready_at": snapshot.ready_at,
                "base_duration": snapshot.base_duration,
                "final_duration": snapshot.final_duration,
                "total_ability": snapshot.total_ability,
                "quality_ability": snapshot.quality_parameters.ability,
                "consumed_inputs": [entry.model_dump() for task in tasks for entry in task.consumed_inputs],
                "quantity": quantity,
                "task_items_reserved": item_count,
                "queue_ready_at": tasks[-1].ready_at,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def _refund_crafting_inputs(self, player: PlayerState, task) -> list[dict]:
        """「万物归元」这类特性：领取成品时按快照掷一次，命中就原样退回其中一种原料。

        掷点放在领取而不是开工，是因为退回要直接写背包；开工阶段只把概率冻进快照。
        """

        refunded: list[dict] = []
        trait_definitions = {trait.code: trait for trait in partner_trait_catalog()}
        for entry in task.applied_effects:
            if entry.get("effect") != "refund_consumed_input":
                continue
            params = entry.get("params") or {}
            chance = max(0.0, min(1.0, float(params.get("chance", 1))))
            # 同一种原料会因为品质不同被拆成几条消耗记录，这里先按 item_id 合回去：
            # 退的是「一种原料的全部消耗量」，不能取决于玩家背包里品质恰好怎么切。
            stacks_by_item: dict[str, list] = {}
            for item in task.consumed_inputs:
                if item.quantity > 0:
                    stacks_by_item.setdefault(item.item_id, []).append(item)
            if not stacks_by_item:
                continue
            item_ids = sorted(stacks_by_item)
            for _ in range(max(1, int(params.get("count", 1)))):
                if chance < 1 and self.rng.random() >= chance:
                    continue
                target_id = item_ids[self.rng.randrange(len(item_ids))]
                trait = trait_definitions.get(str(entry.get("trait_code") or ""))
                for stack in stacks_by_item[target_id]:
                    add_item(player, stack.item_id, stack.quantity, stack.quality)
                    item = self.content.item_map.get(stack.item_id)
                    refunded.append({
                        "item_id": stack.item_id,
                        "quantity": stack.quantity,
                        "quality": stack.quality,
                        "quality_name": QUALITY_NAMES[stack.quality],
                        "item": item.model_dump() if item else None,
                        "trait_code": entry.get("trait_code", ""),
                        "trait_name": trait.name if trait else "",
                    })
        return refunded

    def collect_crafting(self, oauth_sub: str, station_id: str) -> dict:
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            station = self._crafting_station(player, station_id)
            if station.task_snapshot is None:
                raise GameError("crafting_station_empty", "这个工位没有进行中的任务")
            completed = list(station.completed_tasks)
            if station.task_snapshot.ready_at <= now and station.task_results:
                completed.append(CompletedCraftingTask(
                    task_snapshot=station.task_snapshot, task_results=station.task_results,
                ))
                station.task_snapshot = None
                station.task_results = []
            if not completed:
                raise GameError("crafting_not_ready", "加工还没有完成")
            results = []
            refunded_inputs = []
            partner_experience = []
            collect_xp = 0
            refunded_stamina = 0
            for entry in completed:
                task = entry.task_snapshot
                results.extend(entry.task_results)
                for result in entry.task_results:
                    add_item(player, result.item_id, result.quantity, result.quality)
                refunded_inputs.extend(self._refund_crafting_inputs(player, task))
                chance = sum(float(effect.get("value", 0)) for effect in task.applied_effects
                             if effect.get("effect") == "crafting_stamina_refund_chance")
                if chance > 0 and self.rng.random() < min(1, chance):
                    refunded_stamina += 1
                collect_xp += task.harvest_xp
                partner_experience.extend(self._grant_task_partner_experience(player, task))
                record_production_collection(player, "crafting", task.content_id, entry.task_results)
            levels = grant_experience(player, collect_xp, self.content, max_level=self._player_level_cap(player))
            grant_stamina(player, refunded_stamina, self.content, now)
            station.completed_tasks = []
            station.collected_count += len(completed)
            return {
                "station_id": station_id,
                "item_id": results[0].item_id,
                "quantity": sum(result.quantity for result in results),
                "completed_count": len(completed),
                "refunded_stamina": refunded_stamina,
                "drops": [self._result_snapshot(result) for result in results],
                "experience": collect_xp,
                "levels": levels,
                "partner_experience": partner_experience,
                "refunded_inputs": refunded_inputs,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def assign_mining_partner(self, oauth_sub: str, site_id: str, partner_id: str = "") -> dict:
        partner_id = str(partner_id or "").strip()
        now = self._now()
        catalog = self.partner_catalog_loader()

        def mutation(player: PlayerState):
            self._settle(player, now)
            site = self._mining_site(player, site_id)
            desired_ids = [partner_id] if partner_id else []
            if site.assigned_partner_ids == desired_ids:
                return {"site_id": site_id, "partner_id": partner_id or None, "changed": False}
            if (
                site.task_snapshot is not None
                and site.task_snapshot.ready_at > now
                and not self._task_releases_partner(site.task_snapshot)
            ):
                raise GameError("partner_assignment_locked", "采矿进行中，不能调整这个矿点的伙伴", 409)

            locked_until = self._partner_lock_deadlines(player, now)
            affected_ids = set(site.assigned_partner_ids)
            if partner_id:
                affected_ids.add(partner_id)
            if any(locked_until.get(candidate, 0) > now for candidate in affected_ids):
                raise GameError("partner_locked", "伙伴正在参与进行中的任务，暂时不能移动", 409)

            if partner_id:
                owned = next((entry for entry in player.owned_partners if entry.partner_id == partner_id), None)
                definition = catalog.partner_map.get(partner_id)
                if owned is None:
                    raise GameError("partner_not_owned", "你还没有这个伙伴", 404)
                if definition is None:
                    raise GameError("partner_not_found", "伙伴配置不存在", 404)
                if not any(tendency.industry == "mining" for tendency in definition.tendencies):
                    raise GameError("partner_tendency_mismatch", "这个伙伴没有矿产倾向", 409)

            if partner_id:
                self._clear_partner_assignment(player, partner_id)
                if player.fishing.companion_partner_id == partner_id:
                    player.fishing.companion_partner_id = ""
            site.assigned_partner_ids = desired_ids
            if self._industry_assigned_count(player, "mining") > self._industry_partner_capacity(player, "mining"):
                raise GameError("partner_capacity_reached", "当前矿产伙伴编制已满", 409)
            return {"site_id": site_id, "partner_id": partner_id or None, "changed": True}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def start_mining(self, oauth_sub: str, site_id: str, task_id: str, task_item_id: str = "") -> dict:
        task = self.content.mining_task_map.get(task_id)
        if task is None or task.site_id != site_id:
            raise GameError("mining_task_not_found", "这个矿点没有该任务", 404)
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            site = self._mining_site(player, site_id)
            if player.level < task.min_level:
                raise GameError("content_locked", f"达到 {task.min_level} 级后解锁")
            if not site.empty:
                raise GameError("mining_site_occupied", "这个矿点已有任务")
            if self._industry_assigned_count(player, "mining") > self._industry_partner_capacity(player, "mining"):
                raise GameError("partner_capacity_reached", "当前矿产伙伴编制已满", 409)
            total_ability = self._production_ability(player, site.assigned_partner_ids, "mining")[0]
            yield_efficiency = 1 + task.yield_bonus * total_ability / (total_ability + task.yield_difficulty)
            snapshot = self._build_production_task_snapshot(
                player=player,
                assigned_partner_ids=site.assigned_partner_ids,
                industry="mining",
                content_id=task.id,
                production_slot_id=f"mining:site:{site.site_id}",
                now=now,
                base_duration=task.duration_seconds,
                time_difficulty=None,
                produce_item_id=task.produce_item_id,
                yield_min=task.yield_min,
                yield_max=task.yield_max,
                harvest_xp=task.collect_xp,
                stamina_cost=task.stamina_cost,
                quality=task.quality,
                fixed_duration=True,
                yield_efficiency=yield_efficiency,
                task_item_id=task_item_id,
            )
            try:
                consume_stamina(player, task.stamina_cost, self.content, now)
            except ValueError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            site.task_snapshot = snapshot
            site.task_results = []
            return {
                "site_id": site_id,
                "task_id": task_id,
                "ready_at": snapshot.ready_at,
                "base_duration": snapshot.base_duration,
                "final_duration": snapshot.final_duration,
                "total_ability": snapshot.total_ability,
                "quality_ability": snapshot.quality_parameters.ability,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def collect_mining(self, oauth_sub: str, site_id: str) -> dict:
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            site = self._mining_site(player, site_id)
            if site.task_snapshot is None:
                raise GameError("mining_site_empty", "这个矿点没有进行中的任务")
            if site.task_snapshot.ready_at > now:
                raise GameError("mining_not_ready", "采矿还没有完成")
            results = site.task_results
            if not results:
                raise GameError("task_content_missing", "采矿任务配置缺失，请联系管理员", 409)
            collect_xp = site.task_snapshot.harvest_xp
            task_id = site.task_snapshot.content_id
            for result in results:
                add_item(player, result.item_id, result.quantity, result.quality)
            levels = grant_experience(player, collect_xp, self.content, max_level=self._player_level_cap(player))
            partner_experience = self._grant_task_partner_experience(player, site.task_snapshot)
            record_production_collection(player, "mining", task_id, results)
            site.task_snapshot = None
            site.task_results = []
            return {
                "site_id": site_id,
                "item_id": results[0].item_id,
                "quantity": sum(result.quantity for result in results),
                "drops": [self._result_snapshot(result) for result in results],
                "experience": collect_xp,
                "levels": levels,
                "partner_experience": partner_experience,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    # ------------------------------------------------------------------ 水产

    def assign_fishing_companion(self, oauth_sub: str, partner_id: str = "") -> dict:
        """陪钓伙伴。钓鱼是瞬时的，不写锁定引用，玩家随时可换。"""
        partner_id = str(partner_id or "").strip()
        now = self._now()
        catalog = self.partner_catalog_loader()

        def mutation(player: PlayerState):
            self._settle(player, now)
            if player.fishing.companion_partner_id == partner_id:
                return {"partner_id": partner_id or None, "changed": False}
            if partner_id:
                owned = next((entry for entry in player.owned_partners if entry.partner_id == partner_id), None)
                definition = catalog.partner_map.get(partner_id)
                if owned is None:
                    raise GameError("partner_not_owned", "你还没有这个伙伴", 404)
                if definition is None:
                    raise GameError("partner_not_found", "伙伴配置不存在", 404)
                if not any(tendency.industry == "aquatic" for tendency in definition.tendencies):
                    raise GameError("partner_tendency_mismatch", "这个伙伴没有水产倾向", 409)
                if self._partner_lock_deadlines(player, now).get(partner_id, 0) > now:
                    raise GameError("partner_locked", "伙伴正在参与进行中的任务，暂时不能陪钓", 409)
                # 跨产业唯一派驻：驻场在鱼塘或别的生产格的伙伴不能同时陪钓。
                self._clear_partner_assignment(player, partner_id)
            player.fishing.companion_partner_id = partner_id
            return {"partner_id": partner_id or None, "changed": True}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def cast_line(self, oauth_sub: str, spot_id: str, request_id: str = "") -> dict:
        """抛一竿。扣体力、掷结果、入库在同一次原子更新里完成，重复的 request_id 不再发放。"""
        spot = self.content.fishing_spot_map.get(spot_id)
        if spot is None:
            raise GameError("fishing_spot_not_found", "这个钓点不存在", 404)
        combo_rules = self.content.fishing_combo
        request_id = str(request_id or "").strip()[:64]
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            if player.level < spot.min_level:
                raise GameError("content_locked", f"达到 {spot.min_level} 级后解锁")
            fishing = player.fishing
            if request_id and request_id in fishing.recent_request_ids:
                return {"spot_id": spot_id, "duplicate": True, "drops": [], "experience": 0}
            self._require_content(player, spot)
            if fishing.pending_big_catch is not None:
                raise GameError("big_catch_pending", "线还绷着，先处理咬钩的大物")
            if now - fishing.last_cast_at < FISHING_MIN_INTERVAL_SECONDS and fishing.last_cast_at:
                raise GameError("fishing_too_fast", "刚抛完一竿，缓一口气", 429)

            combo = self._current_combo(player, spot_id, now)
            companion_ids = self._fishing_companion_ids(player)
            trait_context = {
                "action": "fishing_cast",
                "industry": "aquatic",
                "content_id": spot.id,
                "world": self._world_snapshot(now),
                "ability_bonus": 0,
                "quality_ability_bonus": 0,
                "draw_bonus": 0,
                "stamina_multiplier": 1.0,
                "rare_weight_multiplier": 1.0,
                "applied_effects": [],
            }
            self._execute_partner_trait_phase(companion_ids, "instant_action", trait_context)
            ability = self._aquatic_ability(player, companion_ids) + int(trait_context["ability_bonus"])
            draws = max(1, min(
                FISHING_MAX_DRAWS,
                self._fishing_draw_count(spot, ability, combo) + int(trait_context["draw_bonus"]),
            ))
            pool = self._fishing_pool(spot, combo, float(trait_context["rare_weight_multiplier"]))
            stamina_cost = max(
                0,
                ceil(spot.stamina_cost * max(0.0, float(trait_context["stamina_multiplier"]))),
            )
            try:
                consume_stamina(player, stamina_cost, self.content, now)
            except ValueError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc

            probabilities = quality_probabilities(
                ability + float(trait_context["quality_ability_bonus"]),
                spot.quality.thresholds,
                spot.quality.width,
                spot.quality.miracle_probability_cap,
                spot.quality.miracle_eligible,
            )
            batches: list[FishingBatch] = []
            hooked = False
            for _ in range(draws):
                entry, big_catch = self._pick_fishing_entry(pool)
                if big_catch and not hooked:
                    hooked = True
                    continue
                if big_catch:
                    # 一竿只处理一条大物，多抽到的按退回的普通鱼算。
                    fallback = self._fishing_output(spot, spot.big_catch.fallback_item_id)
                    batches.append(self._roll_batch(fallback, 1) if fallback else FishingBatch(spot.big_catch.fallback_item_id, 1, True, 0))
                    continue
                quantity = self.rng.randint(entry.quantity_min, entry.quantity_max)
                batches.append(self._roll_batch(entry, quantity))
            season_bonus = self._season_bonus(spot, "companion_bonus", companion_ids, now)
            if season_bonus:
                batches = [
                    batch._replace(quantity=quantity)
                    for batch, quantity in zip(batches, scale_quantities(
                        [batch.quantity for batch in batches], season_bonus.multiplier,
                    ))
                ]
            drops = self._grant_fishing_batches(
                player,
                batches,
                probabilities,
                now,
                trait_context["applied_effects"],
            )
            codex = self._record_codex(player, batches, now)

            levels = grant_experience(player, spot.cast_xp, self.content, max_level=self._player_level_cap(player))
            milestones = self._claim_codex_milestones(player, now)
            partner_experience = self._grant_companion_experience(player, stamina_cost)
            self._award_summer_units(player, spot, stamina_cost * POINT_UNITS, now)
            fishing.spot_id = spot_id
            fishing.combo = min(self._combo_cap(player), combo + 1)
            fishing.combo_updated_at = now
            fishing.last_cast_at = now
            if request_id:
                fishing.recent_request_ids = [*fishing.recent_request_ids, request_id][-8:]
            if hooked:
                fishing.pending_big_catch = PendingBigCatchState(spot_id=spot_id, created_at=now)
            return {
                "spot_id": spot_id,
                "duplicate": False,
                "companion_bonus": season_bonus.model_dump() if season_bonus else None,
                "stamina_cost": stamina_cost,
                "draws": draws,
                "ability": ability,
                "combo": fishing.combo,
                "drops": drops,
                "experience": spot.cast_xp,
                "levels": levels,
                "codex_discoveries": codex,
                "codex_milestones": milestones,
                "partner_experience": partner_experience,
                "applied_effects": trait_context["applied_effects"],
                "big_catch": self._big_catch_snapshot(player, now),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def resolve_big_catch(self, oauth_sub: str, action: str) -> dict:
        """搏鱼：追加体力搏一把，或者直接放弃拿回一条普通鱼。这是演出和图鉴触发，不是决策点。"""
        action = str(action or "").strip()
        if action not in ("fight", "release"):
            raise GameError("invalid_action", "只能选择搏一把或者放弃")
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            pending = player.fishing.pending_big_catch
            if pending is None:
                raise GameError("big_catch_missing", "现在没有咬钩的大物", 404)
            spot = self.content.fishing_spot_map.get(pending.spot_id)
            if spot is None or spot.big_catch is None:
                player.fishing.pending_big_catch = None
                raise GameError("fishing_spot_not_found", "这个钓点的配置已经不存在", 409)
            big_catch = spot.big_catch
            ability = self._aquatic_ability(player, self._fishing_companion_ids(player))
            probabilities = quality_probabilities(
                ability,
                spot.quality.thresholds,
                spot.quality.width,
                spot.quality.miracle_probability_cap,
                spot.quality.miracle_eligible,
            )
            chance = big_catch.success_chance(ability)
            spent = 0
            success = False
            if action == "fight":
                try:
                    consume_stamina(player, big_catch.stamina_cost, self.content, now)
                except ValueError as exc:
                    raise GameError("resource_insufficient", str(exc)) from exc
                spent = big_catch.stamina_cost
                success = self.rng.random() < chance
            player.fishing.pending_big_catch = None
            self._award_summer_units(player, spot, spent * POINT_UNITS, now)
            partner_experience = self._grant_companion_experience(player, spent)
            if not success:
                quality = roll_quality(self.rng, probabilities)
                fallback = self._fishing_output(spot, big_catch.fallback_item_id)
                fallback_size = self._roll_size(fallback)
                add_item(player, big_catch.fallback_item_id, 1, quality)
                self._record_codex_entry(player, big_catch.fallback_item_id, now, size=fallback_size)
                return {
                    "action": action,
                    "success": False,
                    "chance": chance,
                    "stamina_cost": spent,
                    "drops": [self._fishing_drop(big_catch.fallback_item_id, 1, quality, fallback_size)],
                    "codex_milestones": self._claim_codex_milestones(player, now),
                    "partner_experience": partner_experience,
                }
            quality = max(big_catch.min_quality, roll_quality(self.rng, probabilities))
            size = round(big_catch.size_min + self.rng.random() * (big_catch.size_max - big_catch.size_min), 1)
            add_item(player, big_catch.item_id, 1, quality)
            self._record_codex_entry(player, big_catch.item_id, now, size=size)
            return {
                "action": action,
                "success": True,
                "chance": chance,
                "stamina_cost": spent,
                "size": size,
                "drops": [self._fishing_drop(big_catch.item_id, 1, quality, size)],
                "codex_milestones": self._claim_codex_milestones(player, now),
                "partner_experience": partner_experience,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def assign_pond_partner(self, oauth_sub: str, pond_id: str, partner_id: str = "") -> dict:
        """资产格的伙伴驻场即占编制。周期开头的自由窗口内随时能换，过了就排队到下个周期。"""
        partner_id = str(partner_id or "").strip()
        now = self._now()
        catalog = self.partner_catalog_loader()

        def mutation(player: PlayerState):
            self._settle(player, now)
            pond = self._pond(player, pond_id)
            desired_ids = [partner_id] if partner_id else []
            if pond.assigned_partner_ids == desired_ids and pond.pending_partner_ids is None:
                return {"pond_id": pond_id, "partner_id": partner_id or None, "changed": False}
            # 重新安排这口塘自己的队总是自由的：预约还没生效，撤销它不影响任何一个周期。
            pond.pending_partner_ids = None
            if pond.assigned_partner_ids == desired_ids:
                # 又点回在岗的那位，等于取消换人。在岗的人不用再腾一次，腾了反而会被
                # 自己这口塘的周期挡下来。
                return {
                    "pond_id": pond_id,
                    "partner_id": partner_id or None,
                    "changed": True,
                    "queued": False,
                    "cancelled": True,
                    "effective_in_seconds": 0,
                    "ability": pond.ability,
                    "cycle_seconds": pond.cycle_seconds,
                }
            if partner_id:
                owned = next((entry for entry in player.owned_partners if entry.partner_id == partner_id), None)
                definition = catalog.partner_map.get(partner_id)
                if owned is None:
                    raise GameError("partner_not_owned", "你还没有这个伙伴", 404)
                if definition is None:
                    raise GameError("partner_not_found", "伙伴配置不存在", 404)
                if not any(tendency.industry == "aquatic" for tendency in definition.tendencies):
                    raise GameError("partner_tendency_mismatch", "这个伙伴没有水产倾向", 409)
                if self._partner_lock_deadlines(player, now).get(partner_id, 0) > now:
                    raise GameError("partner_locked", "伙伴正在参与进行中的任务，暂时不能移动", 409)
                self._clear_partner_assignment(player, partner_id, now)
                if player.fishing.companion_partner_id == partner_id:
                    player.fishing.companion_partner_id = ""
            queued = not self._swap_window_is_open(self._pond_cycle_progress(pond))
            if queued:
                pond.pending_partner_ids = desired_ids
            else:
                pond.assigned_partner_ids = desired_ids
            if self._industry_assigned_count(player, "aquatic") > self._industry_partner_capacity(player, "aquatic"):
                raise GameError("partner_capacity_reached", "当前水产伙伴编制已满", 409)
            if not queued:
                # 结算已经在前面用旧能力做完，这里直接写入新的参数快照。
                self._refresh_pond_trait_snapshot(player, pond, now)
            return {
                "pond_id": pond_id,
                "partner_id": partner_id or None,
                "changed": True,
                "queued": queued,
                "cancelled": False,
                "effective_in_seconds": self._pond_next_cycle_seconds(pond) if queued else 0,
                "ability": pond.ability,
                "cycle_seconds": pond.cycle_seconds,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def build_pond(self, oauth_sub: str, pond_id: str) -> dict:
        """挖塘。鱼塘是一次性投入的资产，到等级只是解锁资格，还要付红叶币才动土。"""
        pond_id = str(pond_id or "").strip()
        definition = self.content.pond_map.get(pond_id)
        if definition is None:
            raise GameError("pond_not_found", "没有这口鱼塘", 404)
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            if any(pond.pond_id == pond_id for pond in player.ponds):
                raise GameError("pond_exists", "这口塘已经挖好了", 409)
            if player.level < definition.min_level:
                raise GameError("content_locked", f"达到 {definition.min_level} 级之后才能挖这口塘")
            if player.coins < definition.build_cost:
                raise GameError("resource_insufficient", f"挖塘需要 {definition.build_cost} 红叶币")
            player.coins -= definition.build_cost
            for material in definition.build_materials:
                self._consume_minimum_quality_items(player, material.item_id, material.quantity, 0)
            player.ponds.append(PondState(pond_id=pond_id, last_settled_at=now))
            normalize_ponds(player, self.content)
            return {
                "pond_id": pond_id,
                "name": definition.name,
                "build_cost": definition.build_cost,
                "coins": player.coins,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def stock_pond(self, oauth_sub: str, pond_id: str, species_id: str, quantity: int) -> dict:
        """投苗。空塘投苗即开塘，已有鱼群只能继续投同一个品种。"""
        quantity = int(quantity)
        if quantity < 1 or quantity > 999:
            raise GameError("invalid_quantity", "投苗数量需在 1 到 999 之间")
        species = self.content.pond_species_map.get(species_id)
        if species is None:
            raise GameError("pond_species_not_found", "这个鱼苗品种不存在", 404)
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            pond = self._pond(player, pond_id)
            if player.level < species.min_level:
                raise GameError("content_locked", f"达到 {species.min_level} 级后可以养这个品种")
            allowed = self.content.pond_map[pond_id].species_id
            if allowed and allowed != species.id:
                raise GameError("pond_species_mismatch", "这口塘不适合这个鱼种", 409)
            if pond.species_id and pond.species_id != species.id:
                raise GameError("pond_species_mismatch", "塘里已经养着别的鱼，先捞光再换品种", 409)
            capacity = self._pond_capacity(pond)
            if pond.population + quantity > capacity:
                raise GameError("pond_capacity_reached", f"这口塘最多容纳 {capacity} 尾（鱼苗也占位置）")
            try:
                remove_item(player, species.fry_item_id, quantity, 0)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            was_empty = pond.empty
            pond.species_id = species.id
            if was_empty:
                pond.settle_remainder = 0
                pond.growth_remainder = 0
            pond.last_settled_at = now
            self._refresh_pond_trait_snapshot(player, pond, now)
            # 投苗之后才知道周期，所以入池放在最后：这一批要长满 maturation_cycles 个周期。
            add_fry(pond, quantity, species.maturation_cycles)
            return {
                "pond_id": pond_id,
                "species_id": species.id,
                "quantity": quantity,
                "stock": pond.stock,
                "fry": pond.fry_total,
                "capacity": capacity,
                "cycle_seconds": pond.cycle_seconds,
                "maturation_seconds": int(species.maturation_cycles * pond.cycle_seconds),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def harvest_pond(self, oauth_sub: str, pond_id: str, quantity: int) -> dict:
        """捞鱼。不消耗体力：鱼塘的成本在鱼苗和饲料槽上，它属于资产轴。"""
        quantity = int(quantity)
        if quantity < 1:
            raise GameError("invalid_quantity", "捞鱼数量必须为正数")
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            pond = self._pond(player, pond_id)
            species = self.content.pond_species_map.get(pond.species_id)
            if species is None or pond.stock <= 0:
                raise GameError("pond_empty", "这口塘里还没有能捞的成鱼")
            if quantity > pond.stock:
                raise GameError("invalid_quantity", f"塘里只有 {pond.stock} 尾成鱼，鱼苗还没长成")
            ability = self._aquatic_ability(player, pond.assigned_partner_ids)
            quality_ability = (
                ability
                + self._pond_tier(pond).quality_bonus
                + player.feed_slot.quality_score
                + pond.generation_score
                + pond.quality_bonus
            )
            probabilities = quality_probabilities(
                quality_ability,
                species.quality.thresholds,
                species.quality.width,
                species.quality.miracle_probability_cap,
                species.quality.miracle_eligible,
            )
            qualities = [roll_quality(self.rng, probabilities) for _ in range(quantity)]
            floor_quality = int(self._talent_modifier(player, "pond_harvest_quality_floor"))
            if floor_quality:
                best = max(range(len(qualities)), key=lambda index: qualities[index])
                qualities[best] = max(qualities[best], min(5, floor_quality))
            tally: dict[int, int] = {}
            for quality in qualities:
                tally[quality] = tally.get(quality, 0) + 1
            for quality, amount in tally.items():
                add_item(player, species.produce_item_id, amount, quality)
            pond.stock -= quantity
            generation_before = pond.generation_score
            if pond.empty:
                # 竭泽而渔：连鱼苗都不剩，塘退回空塘状态，世代加值清零。
                pond.generation_score = 0
                pond.growth_remainder = 0
                pond.settle_remainder = 0
                pond.species_id = ""
                pond.stalled = False
            # 捞到门槛以下不在这里扣分：鱼群不稳定，世代加值会在之后的每个周期里自己回落。
            pond.last_settled_at = now
            player.achievement_stats.pond_harvested[species.id] = (
                player.achievement_stats.pond_harvested.get(species.id, 0) + quantity
            )
            return {
                "pond_id": pond_id,
                "species_id": species.id,
                "quantity": quantity,
                "stock": pond.stock,
                "fry": pond.fry_total,
                "steady_stock": self._pond_parameters(player, pond).steady_stock,
                "quality_ability": round(quality_ability, 2),
                "generation_before": round(generation_before, 2),
                "generation_score": round(pond.generation_score, 2),
                "drops": [
                    self._fishing_drop(species.produce_item_id, amount, quality)
                    for quality, amount in sorted(tally.items())
                ],
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def deposit_feed(self, oauth_sub: str, item_id: str, quality: int, count: int) -> dict:
        """向饲料槽投料。投料之前必须先结算，否则新料的品质会被追溯到已经过去的那段时间。"""
        item_id = str(item_id or "").strip()
        quality = int(quality or 0)
        count = int(count)
        if count < 1 or count > 999:
            raise GameError("invalid_quantity", "投料数量需在 1 到 999 之间")
        definition = self.content.item_map.get(item_id)
        if definition is None:
            raise GameError("item_not_found", "物品不存在", 404)
        if definition.feed is None:
            raise GameError("item_not_feedable", "这个东西不能当饲料")
        slot_rules = self._feed_slot_rules()
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            try:
                remove_item(player, item_id, count, quality)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            add_units = definition.feed.units * count
            unit_score = definition.feed.score * slot_rules.multiplier(quality)
            try:
                deposit_into_slot(player.feed_slot, add_units, unit_score, self._feed_slot_capacity(player))
            except SlotError as exc:
                raise GameError("feed_slot_full", str(exc)) from exc
            self._settle_aquatic(player, now)
            return {
                "item_id": item_id,
                "quality": quality or None,
                "count": count,
                "added_units": add_units,
                "unit_score": round(unit_score, 2),
                "units": round(player.feed_slot.units, 2),
                "quality_score": round(player.feed_slot.quality_score, 2),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def dump_feed(self, oauth_sub: str) -> dict:
        """倾倒饲料槽。无返还 —— 它是被稀释之后的逃生口。"""
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            dumped = round(player.feed_slot.units, 2)
            player.feed_slot.units = 0
            player.feed_slot.quality_score = 0
            player.feed_slot.updated_at = now
            return {"dumped_units": dumped}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    # --------------------------------------------------------------- 畜牧

    def build_livestock_facility(self, oauth_sub: str, facility_id: str) -> dict:
        """建鸡舍或畜栏。到等级只是拿到资格，还要付红叶币和材料才动土。"""
        facility_id = str(facility_id or "").strip()
        definition = self.content.livestock_facility_map.get(facility_id)
        if definition is None:
            raise GameError("livestock_facility_not_found", "没有这处畜牧设施", 404)
        if definition.granted:
            raise GameError("livestock_facility_granted", "这处设施到等级会自动开放", 409)
        tier = definition.tier(1)
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            if any(entry.facility_id == facility_id for entry in player.livestock_facilities):
                raise GameError("livestock_facility_exists", "这处设施已经建好了", 409)
            if player.level < definition.min_level:
                raise GameError("content_locked", f"达到 {definition.min_level} 级之后才能建这处设施")
            if player.coins < tier.build_coins:
                raise GameError("resource_insufficient", f"建造需要 {tier.build_coins} 红叶币")
            for material in tier.build_materials:
                owned = sum(player.inventory.get(material.item_id, {}).values())
                if owned < material.quantity:
                    name = self.content.item_map[material.item_id].name
                    raise GameError("resource_insufficient", f"建造还差 {material.quantity - owned} 个{name}")
            player.coins -= tier.build_coins
            for material in tier.build_materials:
                self._consume_any_quality(player, material.item_id, material.quantity)
            player.livestock_facilities.append(
                LivestockFacilityState(facility_id=facility_id, last_settled_at=now)
            )
            migrated = 0
            if definition.replaces:
                # 过渡设施在同一次原子更新里回收，栏里的动物整批迁入 —— 容量校验在内容加载时就做过。
                for animal in player.animals:
                    if animal.facility_id == definition.replaces:
                        animal.facility_id = facility_id
                        migrated += 1
            normalize_livestock(player, self.content, now)
            return {
                "facility_id": facility_id,
                "name": definition.name,
                "build_coins": tier.build_coins,
                "build_materials": [material.model_dump() for material in tier.build_materials],
                "migrated_animals": migrated,
                "replaced": definition.replaces or None,
                "coins": player.coins,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def upgrade_livestock_facility(self, oauth_sub: str, facility_id: str, target_tier: int) -> dict:
        if type(target_tier) is not int or target_tier < 2:
            raise GameError("invalid_tier", "请选择有效的扩建等级")
        now = self._now()

        def mutation(player):
            self._settle(player, now)
            facility, definition = self._livestock_facility_pair(player, facility_id)
            tier = next((entry for entry in definition.tiers if entry.level == target_tier), None)
            self._require_content(player, tier)
            if facility.tier == target_tier:
                return {"duplicate": True, "tier": facility.tier}
            if target_tier != facility.tier + 1:
                raise GameError("invalid_tier", "请逐级扩建设施", 409)
            if player.level < tier.min_level:
                raise GameError("content_locked", f"居民 {tier.min_level} 级后可以扩建")
            if player.coins < tier.build_coins:
                raise GameError("resource_insufficient", f"扩建需要 {tier.build_coins} 红叶币")
            for material in tier.build_materials:
                if sum(player.inventory.get(material.item_id, {}).values()) < material.quantity:
                    raise GameError("resource_insufficient", f"扩建材料不足：{self.content.item_map[material.item_id].name}")
            player.coins -= tier.build_coins
            for material in tier.build_materials:
                self._consume_any_quality(player, material.item_id, material.quantity)
            facility.tier = target_tier
            return {"facility_id": facility_id, "tier": target_tier, "coins": player.coins}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def _livestock_upgrade_snapshot(self, player, facility, definition):
        tier = next((entry for entry in definition.tiers if entry.level == facility.tier + 1), None)
        if not self._content_visible(player, tier):
            return None
        return {
            **tier.model_dump(),
            "unlocked": player.level >= tier.min_level,
            "affordable": player.coins >= tier.build_coins and all(
                sum(player.inventory.get(m.item_id, {}).values()) >= m.quantity for m in tier.build_materials
            ),
            "build_materials": [
                {**m.model_dump(), "item": self.content.item_map[m.item_id].model_dump(),
                 "owned": sum(player.inventory.get(m.item_id, {}).values())}
                for m in tier.build_materials
            ],
        }

    def assign_livestock_partner(self, oauth_sub: str, facility_id: str, partner_id: str = "") -> dict:
        """畜栏也是资产格：周期开头的自由窗口内随时能换，过了就排队到下个周期。"""
        partner_id = str(partner_id or "").strip()
        now = self._now()
        catalog = self.partner_catalog_loader()

        def mutation(player: PlayerState):
            self._settle(player, now)
            facility = self._livestock_facility(player, facility_id)
            desired_ids = [partner_id] if partner_id else []
            if facility.assigned_partner_ids == desired_ids and facility.pending_partner_ids is None:
                return {"facility_id": facility_id, "partner_id": partner_id or None, "changed": False}
            # 重新安排这一栏自己的队总是自由的：预约还没生效，撤销它不影响任何一个周期。
            facility.pending_partner_ids = None
            if facility.assigned_partner_ids == desired_ids:
                # 又点回在岗的那位，等于取消换人。在岗的人不用再腾一次，腾了反而会被
                # 自己这一栏的周期挡下来。
                return {
                    "facility_id": facility_id,
                    "partner_id": partner_id or None,
                    "changed": True,
                    "queued": False,
                    "cancelled": True,
                    "effective_in_seconds": 0,
                    "trait_effects": list(facility.trait_effects),
                }
            if partner_id:
                owned = next((entry for entry in player.owned_partners if entry.partner_id == partner_id), None)
                definition = catalog.partner_map.get(partner_id)
                if owned is None:
                    raise GameError("partner_not_owned", "你还没有这个伙伴", 404)
                if definition is None:
                    raise GameError("partner_not_found", "伙伴配置不存在", 404)
                if not any(tendency.industry == "livestock" for tendency in definition.tendencies):
                    raise GameError("partner_tendency_mismatch", "这个伙伴没有畜牧倾向", 409)
                if self._partner_lock_deadlines(player, now).get(partner_id, 0) > now:
                    raise GameError("partner_locked", "伙伴正在参与进行中的任务，暂时不能移动", 409)
                self._clear_partner_assignment(player, partner_id, now)
                if player.fishing.companion_partner_id == partner_id:
                    player.fishing.companion_partner_id = ""
            queued = not self._swap_window_is_open(self._facility_cycle_progress(player, facility))
            if queued:
                facility.pending_partner_ids = desired_ids
            else:
                facility.assigned_partner_ids = desired_ids
            if self._industry_assigned_count(player, "livestock") > self._industry_partner_capacity(player, "livestock"):
                raise GameError("partner_capacity_reached", "当前畜牧伙伴编制已满", 409)
            if not queued:
                # 开头已经用旧参数结算过了，这里换上新伙伴的特性快照。
                self._refresh_livestock_trait_snapshot(player, facility, now)
            return {
                "facility_id": facility_id,
                "partner_id": partner_id or None,
                "changed": True,
                "queued": queued,
                "cancelled": False,
                "effective_in_seconds": self._facility_next_cycle_seconds(facility) if queued else 0,
                "trait_effects": list(facility.trait_effects),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def buy_animal(self, oauth_sub: str, facility_id: str, species_id: str, nickname: str = "") -> dict:
        """买一只幼崽。买来的一律是幼年期，基因掷在偏低的区间，育种才有提升空间。"""
        species_id = str(species_id or "").strip()
        nickname = self._animal_nickname(nickname)
        species = self.content.livestock_species_map.get(species_id)
        if species is None:
            raise GameError("livestock_species_not_found", "没有这个物种", 404)
        rules = self._livestock_rules()
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            facility, definition = self._livestock_facility_pair(player, facility_id)
            if definition.category != species.category:
                raise GameError("livestock_category_mismatch", "这处设施养不了这种牲畜", 409)
            self._require_species(player, facility, definition, species)
            self._require_livestock_room(player, facility, definition)
            if player.coins < species.purchase_price:
                raise GameError("resource_insufficient", f"买一只{species.name}需要 {species.purchase_price} 红叶币")
            player.coins -= species.purchase_price
            gene_cap = definition.tier(facility.tier).gene_cap
            animal = self._new_animal(
                species,
                facility_id,
                now,
                quality_gene=self._purchase_gene(rules, gene_cap),
                yield_gene=self._purchase_gene(rules, gene_cap),
                nickname=nickname,
            )
            player.animals.append(animal)
            return {
                "facility_id": facility_id,
                "species_id": species_id,
                "price": species.purchase_price,
                "coins": player.coins,
                "animal": self._animal_snapshot(player, animal, facility, definition),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def collect_livestock(self, oauth_sub: str, facility_id: str, animal_id: str = "") -> dict:
        """收取畜产品。不消耗体力：畜牧的成本在幼崽和饲料槽上，它属于资产轴。

        品质在结算的每个周期就掷定了，这里只是把已经定好的东西搬进背包 —— 所以临时
        倒一槽精饲料再收取，不会追溯提升前面几个周期攒下的产出。
        """
        animal_id = str(animal_id or "").strip()
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            facility, definition = self._livestock_facility_pair(player, facility_id)
            animals = [
                animal
                for animal in player.animals
                if animal.facility_id == facility_id and (not animal_id or animal.animal_id == animal_id)
            ]
            if animal_id and not animals:
                raise GameError("animal_not_found", "找不到这只牲畜", 404)
            drops: dict[tuple[str, int], int] = {}
            for animal in animals:
                species = self.content.livestock_species_map.get(animal.species_id)
                if species is None:
                    continue
                for quality, amount in animal.pending_output.items():
                    if amount <= 0:
                        continue
                    add_item(player, species.produce_item_id, amount, quality)
                    drops[(species.produce_item_id, quality)] = drops.get((species.produce_item_id, quality), 0) + amount
                animal.pending_output = {}
                if animal.pending_special and species.special_item_id:
                    add_item(player, species.special_item_id, animal.pending_special, 0)
                    key = (species.special_item_id, 0)
                    drops[key] = drops.get(key, 0) + animal.pending_special
                    player.achievement_stats.livestock_specials += animal.pending_special
                animal.pending_special = 0
            if not drops:
                raise GameError("livestock_empty", "这里暂时没有可以收取的东西", 409)
            collected = sum(drops.values())
            player.achievement_stats.livestock_item_ids = sorted(
                set(player.achievement_stats.livestock_item_ids) | {item_id for item_id, _ in drops}
            )
            player.achievement_stats.production_collections["livestock"] = (
                player.achievement_stats.production_collections.get("livestock", 0) + collected
            )
            return {
                "facility_id": facility_id,
                "animal_id": animal_id or None,
                "collected": collected,
                "drops": [
                    self._livestock_drop(item_id, amount, quality)
                    for (item_id, quality), amount in sorted(drops.items())
                ],
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def care_animal(self, oauth_sub: str, animal_id: str) -> dict:
        """照料。不加速产出周期 —— 周期是硬常量，照料换的是亲密度、经验和金蛋。"""
        animal_id = str(animal_id or "").strip()
        rules = self._livestock_rules()
        now = self._now()
        day = self._commission_day(now)

        def mutation(player: PlayerState):
            self._settle(player, now)
            animal = self._animal(player, animal_id)
            facility, definition = self._livestock_facility_pair(player, animal.facility_id)
            if animal.stage == "incubating":
                raise GameError("animal_incubating", "还在孵化，没什么好照料的", 409)
            if animal.cared_on != day:
                animal.cared_on = day
                animal.cared_count = 0
            if animal.cared_count >= rules.care_daily_limit:
                raise GameError("care_limit_reached", f"今天已经照料过 {rules.care_daily_limit} 次了", 409)
            try:
                consume_stamina(player, rules.care_stamina_cost, self.content, now)
            except ValueError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            animal.cared_count += 1
            player.achievement_stats.animals_cared += 1
            context = self._livestock_instant_context(
                player,
                facility,
                "livestock_care",
                now,
                affection_per_care_bonus=0,
            )
            gain = rules.affection_per_care + max(0, int(context["affection_per_care_bonus"]))
            before = animal.affection
            animal.affection = min(rules.affection_cap, animal.affection + gain)
            levels = grant_experience(player, rules.care_experience, self.content, max_level=self._player_level_cap(player))
            return {
                "animal_id": animal_id,
                "facility_id": animal.facility_id,
                "stamina_cost": rules.care_stamina_cost,
                "experience": rules.care_experience,
                "affection_gain": gain,
                "affection_before": before,
                "affection": animal.affection,
                "trait_effects": list(context["applied_effects"]),
                "affection_cap": rules.affection_cap,
                "cared_today": animal.cared_count,
                "care_daily_limit": rules.care_daily_limit,
                "unlocked_levels": levels,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def incubate_egg(self, oauth_sub: str, facility_id: str, quality: int, nickname: str = "", species_id: str = "") -> dict:
        """孵蛋。蛋的品质就是种鸡的基因载体：好鸡下好蛋，好蛋孵好鸡。"""
        quality = int(quality or 0)
        nickname = self._animal_nickname(nickname)
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            facility, definition = self._livestock_facility_pair(player, facility_id)
            species = self._breeding_species(definition, "incubate", species_id)
            self._require_species(player, facility, definition, species)
            breeding = species.breeding
            if quality < 1 or quality > 5:
                raise GameError("invalid_quality", "只能挑一个具体品质的蛋来孵")
            self._require_livestock_room(player, facility, definition)
            if player.feed_slot.quality_score < breeding.min_feed_score:
                raise GameError("feed_score_too_low", f"饲料槽品质分需要达到 {breeding.min_feed_score:g}", 409)
            if player.feed_slot.units < breeding.feed_units:
                raise GameError("resource_insufficient", f"孵化需要 {breeding.feed_units:g} 份饲料")
            try:
                remove_item(player, breeding.incubate_item_id, 1, quality)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            player.feed_slot.units = max(0.0, player.feed_slot.units - breeding.feed_units)
            gene_cap = definition.tier(facility.tier).gene_cap
            base = breeding.quality_gene_base[quality - 1]
            mutation_chance = breeding.mutation_chance + self._talent_modifier(player, "livestock_mutation_chance")
            context = self._livestock_instant_context(
                player,
                facility,
                "livestock_incubate",
                now,
                gene_rerolls=0,
            )
            rerolls = max(0, int(context["gene_rerolls"]))
            animal = self._new_animal(
                species,
                facility_id,
                now,
                quality_gene=roll_gene(
                    self.rng, base, breeding.gene_sigma, gene_cap, mutation_chance, breeding.mutation_bonus, rerolls,
                ),
                yield_gene=roll_gene(
                    self.rng, base, breeding.gene_sigma, gene_cap, mutation_chance, breeding.mutation_bonus, rerolls,
                ),
                stage="incubating",
                nickname=nickname,
            )
            player.animals.append(animal)
            player.achievement_stats.animals_bred += 1
            return {
                "facility_id": facility_id,
                "quality": quality,
                "feed_units": breeding.feed_units,
                "incubate_cycles": breeding.incubate_cycles,
                "trait_effects": list(context["applied_effects"]),
                "animal": self._animal_snapshot(player, animal, facility, definition),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def breed_animals(self, oauth_sub: str, facility_id: str, parent_ids: list[str], nickname: str = "") -> dict:
        """配种。不做性别，任意两只同物种成年即可；进步来自选择，不是来自均值。"""
        parents = [str(entry or "").strip() for entry in (parent_ids or []) if str(entry or "").strip()]
        nickname = self._animal_nickname(nickname)
        if len(parents) != 2 or parents[0] == parents[1]:
            raise GameError("invalid_parents", "配种需要选两只不同的成年牲畜")
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            facility, definition = self._livestock_facility_pair(player, facility_id)
            chosen = [self._animal(player, entry) for entry in parents]
            if any(parent.facility_id != facility_id for parent in chosen):
                raise GameError("animal_elsewhere", "亲本不在这处设施里", 409)
            species = self._breeding_species(definition, "pair", chosen[0].species_id)
            self._require_species(player, facility, definition, species)
            breeding = species.breeding
            for parent in chosen:
                if parent.facility_id != facility_id:
                    raise GameError("animal_elsewhere", "亲本不在这处设施里", 409)
                if parent.species_id != species.id:
                    raise GameError("livestock_category_mismatch", "只有同物种才能配种", 409)
                if parent.stage != "adult":
                    raise GameError("animal_not_adult", "只有成年牲畜才能配种", 409)
                if parent.breeding_cooldown > 0:
                    raise GameError("animal_on_cooldown", "亲本还在配种冷却里", 409)
            self._require_livestock_room(player, facility, definition)
            if player.feed_slot.quality_score < breeding.min_feed_score:
                raise GameError("feed_score_too_low", f"饲料槽品质分需要达到 {breeding.min_feed_score:g}", 409)
            if player.feed_slot.units < breeding.feed_units:
                raise GameError("resource_insufficient", f"配种需要 {breeding.feed_units:g} 份饲料")
            player.feed_slot.units = max(0.0, player.feed_slot.units - breeding.feed_units)
            gene_cap = definition.tier(facility.tier).gene_cap
            mutation_chance = breeding.mutation_chance + self._talent_modifier(player, "livestock_mutation_chance")
            quality_base = (chosen[0].quality_gene + chosen[1].quality_gene) / 2
            yield_base = (chosen[0].yield_gene + chosen[1].yield_gene) / 2
            context = self._livestock_instant_context(
                player,
                facility,
                "livestock_breed",
                now,
                gene_rerolls=0,
            )
            rerolls = max(0, int(context["gene_rerolls"]))
            calf = self._new_animal(
                species,
                facility_id,
                now,
                quality_gene=roll_gene(
                    self.rng, quality_base, breeding.gene_sigma, gene_cap, mutation_chance, breeding.mutation_bonus, rerolls,
                ),
                yield_gene=roll_gene(
                    self.rng, yield_base, breeding.gene_sigma, gene_cap, mutation_chance, breeding.mutation_bonus, rerolls,
                ),
                nickname=nickname,
            )
            player.animals.append(calf)
            player.achievement_stats.bred_species_ids = sorted(
                set(player.achievement_stats.bred_species_ids) | {species.id}
            )
            player.achievement_stats.animals_bred += 1
            for parent in chosen:
                parent.breeding_cooldown = float(breeding.cooldown_cycles)
            return {
                "facility_id": facility_id,
                "parent_ids": parents,
                "feed_units": breeding.feed_units,
                "cooldown_cycles": breeding.cooldown_cycles,
                "trait_effects": list(context["applied_effects"]),
                "parent_quality_gene": round(quality_base, 1),
                "parent_yield_gene": round(yield_base, 1),
                "animal": self._animal_snapshot(player, calf, facility, definition),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def sell_animal(self, oauth_sub: str, animal_id: str) -> dict:
        """出售牲畜。回收价永远低于买入价，但好基因卖得贵，淘汰差的才有正反馈。"""
        animal_id = str(animal_id or "").strip()
        rules = self._livestock_rules()
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            animal = self._animal(player, animal_id)
            species = self.content.livestock_species_map.get(animal.species_id)
            if species is None:
                raise GameError("livestock_species_not_found", "没有这个物种", 404)
            if animal.pending_total or animal.pending_special:
                raise GameError("livestock_pending_output", "先把它的产出收了再说", 409)
            price = refund_value(
                species.refund_base,
                animal.quality_gene,
                animal.yield_gene,
                rules.refund_gene_coefficient,
            )
            grant_coins(player, price)
            player.animals = [entry for entry in player.animals if entry.animal_id != animal_id]
            return {
                "animal_id": animal_id,
                "species_id": species.id,
                "price": price,
                "coins": player.coins,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    # ------------------------------------------------------- 畜牧：内部实现

    def _livestock_rules(self):
        rules = self.content.livestock
        if rules is None:
            raise GameError("livestock_missing", "畜牧配置缺失，请联系管理员", 409)
        return rules

    def _livestock_facility(self, player: PlayerState, facility_id: str) -> LivestockFacilityState:
        facility = next(
            (entry for entry in player.livestock_facilities if entry.facility_id == facility_id),
            None,
        )
        if facility is None:
            raise GameError("livestock_facility_locked", "这处畜牧设施尚未开放", 404)
        return facility

    def _livestock_facility_pair(self, player: PlayerState, facility_id: str):
        facility = self._livestock_facility(player, facility_id)
        definition = self.content.livestock_facility_map.get(facility.facility_id)
        if definition is None:
            raise GameError("livestock_facility_not_found", "畜牧设施配置缺失，请联系管理员", 409)
        return facility, definition

    def _animal(self, player: PlayerState, animal_id: str) -> AnimalState:
        animal = next((entry for entry in player.animals if entry.animal_id == animal_id), None)
        if animal is None:
            raise GameError("animal_not_found", "找不到这只牲畜", 404)
        return animal

    def _animals_in(self, player: PlayerState, facility_id: str) -> list[AnimalState]:
        return [animal for animal in player.animals if animal.facility_id == facility_id]

    def _require_livestock_room(self, player: PlayerState, facility, definition) -> None:
        """幼崽和孵化中的蛋一样占容量，否则容量上限形同虚设。"""
        capacity = definition.tier(facility.tier).capacity
        if len(self._animals_in(player, facility.facility_id)) >= capacity:
            raise GameError("livestock_facility_full", f"{definition.name}已经住满了（{capacity} 位）", 409)

    def _breeding_species(self, definition, mode: str, species_id: str = ""):
        species = next(
            (
                entry
                for entry in self.content.livestock_species
                if entry.category == definition.category and entry.breeding.mode == mode
                and (not species_id or entry.id == species_id)
            ),
            None,
        )
        if species is None:
            raise GameError("livestock_breeding_unavailable", "这处设施不支持这种繁殖方式", 409)
        return species

    def _purchase_gene(self, rules, gene_cap: int) -> int:
        low = min(rules.purchase_gene_min, gene_cap)
        high = min(rules.purchase_gene_max, gene_cap)
        return self.rng.randint(low, high)

    def _animal_nickname(self, nickname: str) -> str:
        """起名是纯展示，但仍然要拦住空白名和超长名，免得列表被撑破。"""
        cleaned = " ".join(str(nickname or "").split())
        if len(cleaned) > ANIMAL_NICKNAME_LIMIT:
            raise GameError("invalid_nickname", f"名字最多 {ANIMAL_NICKNAME_LIMIT} 个字")
        return cleaned

    def _new_animal(
        self,
        species,
        facility_id: str,
        now: int,
        *,
        quality_gene: int,
        yield_gene: int,
        stage: str = "juvenile",
        nickname: str = "",
    ) -> AnimalState:
        return AnimalState(
            animal_id=uuid4().hex,
            species_id=species.id,
            facility_id=facility_id,
            nickname=nickname,
            stage=stage,
            quality_gene=quality_gene,
            yield_gene=yield_gene,
            born_at=now,
        )

    def _consume_any_quality(self, player: PlayerState, item_id: str, quantity: int) -> None:
        """建造材料不挑品质，从低品质开始扣 —— 好东西留给需要品质的地方。"""
        remaining = quantity
        for quality in sorted(player.inventory.get(item_id, {})):
            if remaining <= 0:
                break
            available = player.inventory[item_id][quality]
            take = min(available, remaining)
            if take <= 0:
                continue
            remove_item(player, item_id, take, quality)
            remaining -= take
        if remaining > 0:
            raise GameError("resource_insufficient", "材料不足")

    def _livestock_ability(self, player: PlayerState, partner_ids: list[str]) -> int:
        try:
            total, _, _ = self._production_ability(player, list(partner_ids), "livestock")
        except GameError:
            rules = self.content.industries["livestock"]
            return rules.character_base_ability + self._industry_ability_bonus(player, "livestock")
        return total

    def _species_parameters(self, species) -> SpeciesParameters:
        rules = self._livestock_rules()
        return SpeciesParameters(
            species_id=species.id,
            growth_cycles=species.growth_cycles,
            incubate_cycles=species.breeding.incubate_cycles,
            base_yield=species.base_yield,
            feed_per_cycle=species.feed_per_cycle,
            yield_coefficient=rules.yield_gene_coefficient,
            special_item_id=species.special_item_id,
            special_chance=species.special_chance,
            thresholds=tuple(species.quality.thresholds),
            width=species.quality.width,
            miracle_probability_cap=species.quality.miracle_probability_cap,
            miracle_eligible=species.quality.miracle_eligible,
        )

    def _facility_parameters(
        self,
        player: PlayerState,
        facility: LivestockFacilityState,
        feed_score: float,
    ) -> FacilityParameters:
        rules = self._livestock_rules()
        definition = self.content.livestock_facility_map.get(facility.facility_id)
        tier = definition.tier(facility.tier) if definition else None
        overflow = (tier.overflow_cycles if tier else 1) + int(
            self._talent_modifier(player, "livestock_overflow_cycles")
        )
        category = definition.category if definition else ""
        return FacilityParameters(
            cycle_seconds=rules.cycle_seconds,
            capacity=tier.capacity if tier else 0,
            quality_multiplier=tier.quality_multiplier if tier else 1,
            # 天赋和伙伴特性都抬溢出上限，允许叠加：它只减少浪费，不加快产出。
            overflow_cycles=max(1, overflow + facility.overflow_bonus),
            gene_cap=tier.gene_cap if tier else 0,
            ability=self._livestock_ability(player, facility.assigned_partner_ids),
            feed_score=feed_score,
            quality_gene_coefficient=rules.quality_gene_coefficient,
            affection_cap=rules.affection_cap,
            affection_quality_base=rules.affection_quality_base,
            affection_quality_per_point=rules.affection_quality_per_point,
            quality_bonus=facility.quality_bonus,
            feed_multiplier=facility.feed_multiplier * self._feed_cost_multiplier(player),
            special_chance_bonus=facility.special_chance_bonus,
            affection_quality_bonus=facility.affection_quality_bonus,
            species={
                species.id: self._species_parameters(species)
                for species in self.content.livestock_species
                if species.category == category
            },
        )

    def _refresh_livestock_trait_snapshot(
        self,
        player: PlayerState,
        facility: LivestockFacilityState,
        now: int,
    ) -> None:
        """把驻场伙伴的特性展开成这一栏下一段生效的参数。

        必须在结算之后调用：资产格的规矩是【先用旧参数结算，再写新参数】。
        """

        definition = self.content.livestock_facility_map.get(facility.facility_id)
        context = {
            "industry": "livestock",
            "action": "livestock_segment",
            "content_id": facility.facility_id,
            "production_slot_id": f"livestock:{facility.facility_id}",
            "world": self._world_snapshot(now),
            "content_tags": [definition.category] if definition else [],
            "quality_bonus": 0.0,
            "feed_multiplier": 1.0,
            "overflow_bonus": 0,
            "special_chance_bonus": 0.0,
            "affection_quality_bonus": 0.0,
            "applied_effects": [],
        }
        self._execute_partner_trait_phase(facility.assigned_partner_ids, "livestock_segment", context)
        facility.quality_bonus = float(context["quality_bonus"])
        facility.feed_multiplier = max(0.0, float(context["feed_multiplier"]))
        facility.overflow_bonus = max(0, int(context["overflow_bonus"]))
        facility.special_chance_bonus = max(0.0, float(context["special_chance_bonus"]))
        facility.affection_quality_bonus = max(0.0, float(context["affection_quality_bonus"]))
        facility.trait_effects = list(context["applied_effects"])

    def _livestock_instant_context(
        self,
        player: PlayerState,
        facility: LivestockFacilityState,
        action: str,
        now: int,
        **fields,
    ) -> dict:
        """照料、配种、孵化这类瞬时操作的特性上下文，来源是本栏的驻场伙伴。"""

        definition = self.content.livestock_facility_map.get(facility.facility_id)
        context = {
            "industry": "livestock",
            "action": action,
            "content_id": facility.facility_id,
            "production_slot_id": f"livestock:{facility.facility_id}",
            "world": self._world_snapshot(now),
            "content_tags": [definition.category] if definition else [],
            "applied_effects": [],
            **fields,
        }
        self._execute_partner_trait_phase(facility.assigned_partner_ids, "instant_action", context)
        return context

    def _grant_livestock_partner_experience(self, player: PlayerState, settlement) -> None:
        """驻场的伙伴按结算掉的周期数拿经验。停摆的周期没买单，也就不给经验。"""
        per_cycle = self.content.partner_growth.livestock_experience_per_cycle
        if not per_cycle:
            return
        owned_map = {entry.partner_id: entry for entry in player.owned_partners}
        for facility in player.livestock_facilities:
            advance = settlement.facilities.get(facility.facility_id)
            if advance is None or advance.paid_cycles <= 0:
                continue
            for partner_id in facility.assigned_partner_ids:
                owned = owned_map.get(partner_id)
                if owned is not None:
                    self._grant_partner_experience(owned, advance.paid_cycles * per_cycle)

    def _livestock_drop(self, item_id: str, quantity: int, quality: int) -> dict:
        item = self.content.item_map.get(item_id)
        grade = self.content.quality.grade_map.get(quality)
        return {
            "item_id": item_id,
            "name": item.name if item else item_id,
            "icon": item.icon if item else "package",
            "quantity": quantity,
            "quality": quality or None,
            "quality_name": grade.name if grade else None,
            "sell_price": self._quality_unit_price(item.sell_price if item else 0, quality),
        }

    def _animal_snapshot(
        self,
        player: PlayerState,
        animal: AnimalState,
        facility,
        definition,
        parameters: FacilityParameters | None = None,
        day: str = "",
    ) -> dict:
        rules = self._livestock_rules()
        species = self.content.livestock_species_map.get(animal.species_id)
        # 参数快照按栏算一次就够：整栏共用能力、饲料分和设施系数。
        if parameters is None:
            parameters = self._facility_parameters(player, facility, player.feed_slot.quality_score)
        species_parameters = parameters.species.get(animal.species_id)
        cycle = parameters.cycle_seconds
        remaining_stage = 0
        if species_parameters:
            if animal.stage == "incubating":
                remaining_stage = max(0, species_parameters.incubate_cycles - int(animal.stage_cycles))
            elif animal.stage == "juvenile":
                remaining_stage = max(0, species_parameters.growth_cycles - int(animal.stage_cycles))
        day = day or self._commission_day(self._now())
        return {
            "animal_id": animal.animal_id,
            "species_id": animal.species_id,
            "facility_id": animal.facility_id,
            "nickname": animal.nickname,
            "name": animal.nickname or (species.name if species else animal.species_id),
            "species_name": species.name if species else animal.species_id,
            "icon": species.icon if species else "package",
            "stage": animal.stage,
            "stage_cycles": animal.stage_cycles,
            "remaining_stage_cycles": remaining_stage,
            "remaining_stage_seconds": remaining_stage * cycle,
            "quality_gene": animal.quality_gene,
            "yield_gene": animal.yield_gene,
            "gene_cap": parameters.gene_cap,
            "affection": animal.affection,
            "affection_cap": rules.affection_cap,
            "affection_multiplier": round(animal_affection_multiplier(animal, parameters), 4),
            "pending_output": {str(quality): amount for quality, amount in sorted(animal.pending_output.items())},
            "pending_total": animal.pending_total,
            "pending_special": animal.pending_special,
            "overflow_cap": overflow_cap(animal, species_parameters, parameters) if species_parameters else 0,
            "saturated": is_saturated(animal, species_parameters, parameters) if species_parameters else False,
            "yield_per_cycle": round(yield_per_cycle(animal, species_parameters), 3) if species_parameters else 0,
            "quality_ability": round(animal_quality_ability(animal, parameters), 2),
            "breeding_cooldown": animal.breeding_cooldown,
            "breeding_cooldown_seconds": int(animal.breeding_cooldown * cycle),
            "cared_today": animal.cared_count if animal.cared_on == day else 0,
            "care_daily_limit": rules.care_daily_limit,
            "born_at": animal.born_at,
            "produce_item": (
                self.content.item_map[species.produce_item_id].model_dump()
                if species and species.produce_item_id in self.content.item_map
                else None
            ),
        }

    def _livestock_hourly_rate(self, player: PlayerState) -> float:
        rules = self.content.livestock
        if rules is None or not player.livestock_facilities:
            return 0.0
        species_map = self.content.livestock_species_map
        # 每栏的饲料乘算不同，所以按动物所在的栏各乘各的。
        multipliers = {
            facility.facility_id: facility.feed_multiplier * self._feed_cost_multiplier(player)
            for facility in player.livestock_facilities
        }
        per_cycle = sum(
            species_map[animal.species_id].feed_per_cycle * multipliers.get(animal.facility_id, 1.0)
            for animal in player.animals
            if animal.species_id in species_map and animal.stage != "incubating"
        )
        return per_cycle * 3600 / rules.cycle_seconds if rules.cycle_seconds else 0.0

    def _feed_slot_capacity(self, player: PlayerState) -> int:
        """槽的容量跟着畜牧设施走：满编一天要吃几百份，固定 500 撑不到一天。"""
        rules = self.content.feed_slot
        if rules is None:
            return 0
        bonus = 0
        for facility in player.livestock_facilities:
            definition = self.content.livestock_facility_map.get(facility.facility_id)
            if definition is None:
                continue
            bonus += definition.tier(facility.tier).feed_slot_capacity_bonus
        upgrade = self._feed_upgrade(player)
        return rules.capacity + bonus + (upgrade.capacity_bonus if upgrade else 0)

    def _livestock_snapshot(self, player: PlayerState, now: int, partner_map: dict[str, dict]) -> dict:
        rules = self.content.livestock
        items = self.content.item_map
        day = self._commission_day(now)
        built = {facility.facility_id for facility in player.livestock_facilities}
        facilities = []
        for facility in player.livestock_facilities:
            definition = self.content.livestock_facility_map.get(facility.facility_id)
            if definition is None:
                continue
            tier = definition.tier(facility.tier)
            parameters = self._facility_parameters(player, facility, player.feed_slot.quality_score)
            animals = self._animals_in(player, facility.facility_id)
            assigned_partners = [
                partner_map[partner_id]
                for partner_id in facility.assigned_partner_ids
                if partner_id in partner_map
            ]
            species_here = [
                species for species in self.content.livestock_species
                if species.category == definition.category and self._content_visible(player, species)
            ]
            facilities.append({
                "facility_id": facility.facility_id,
                "tier": facility.tier,
                "upgrade": self._livestock_upgrade_snapshot(player, facility, definition),
                "name": definition.name,
                "description": definition.description,
                "accent": definition.accent,
                "category": definition.category,
                "capacity": tier.capacity,
                "used": len(animals),
                "quality_multiplier": tier.quality_multiplier,
                "overflow_cycles": parameters.overflow_cycles,
                "gene_cap": tier.gene_cap,
                "ability": parameters.ability,
                "quality_bonus": facility.quality_bonus,
                "feed_multiplier": facility.feed_multiplier,
                "special_chance_bonus": facility.special_chance_bonus,
                "affection_quality_bonus": facility.affection_quality_bonus,
                "trait_effects": list(facility.trait_effects),
                "stalled": facility.stalled,
                "pending_partner_ids": facility.pending_partner_ids,
                "pending_partner": (
                    partner_map.get(facility.pending_partner_ids[0])
                    if facility.pending_partner_ids else None
                ),
                "swap_open": self._swap_window_is_open(self._facility_cycle_progress(player, facility)),
                "swap_window_seconds": self._swap_window_seconds(parameters.cycle_seconds),
                "settle_remainder": facility.settle_remainder,
                "last_settled_at": facility.last_settled_at,
                "next_cycle_seconds": max(0, parameters.cycle_seconds - facility.settle_remainder),
                "assigned_partners": assigned_partners,
                "animals": [
                    self._animal_snapshot(player, animal, facility, definition, parameters, day)
                    for animal in animals
                ],
                "pending_total": sum(animal.pending_total for animal in animals),
                "pending_special": sum(animal.pending_special for animal in animals),
                "species": [
                    {
                        **species.model_dump(exclude={"quality"}),
                        "produce_item": items[species.produce_item_id].model_dump(),
                        "special_item": (
                            items[species.special_item_id].model_dump()
                            if species.special_item_id in items else None
                        ),
                        "unlocked": self._species_unlocked(player, facility, species),
                        "unlock_description": f"居民 {species.min_level} 级" + (f"，设施 {species.required_facility_tier} 级" if species.required_facility_id else ""),
                        "affordable": player.coins >= species.purchase_price,
                    }
                    for species in species_here
                ],
            })
        return {
            "unlocked": bool(player.livestock_facilities) or any(
                definition.min_level <= player.level for definition in self.content.livestock_facilities
            ),
            "cycle_seconds": rules.cycle_seconds if rules else 0,
            "ability": self._livestock_ability(player, []),
            "facilities": facilities,
            "buildable_facilities": [
                {
                    "facility_id": definition.id,
                    "name": definition.name,
                    "description": definition.description,
                    "accent": definition.accent,
                    "category": definition.category,
                    "min_level": definition.min_level,
                    "unlocked": player.level >= definition.min_level,
                    "capacity": definition.tier(1).capacity,
                    "replaces": definition.replaces or None,
                    "feed_slot_capacity_bonus": definition.tier(1).feed_slot_capacity_bonus,
                    "build_coins": definition.tier(1).build_coins,
                    "affordable": player.coins >= definition.tier(1).build_coins and all(
                        sum(player.inventory.get(material.item_id, {}).values()) >= material.quantity
                        for material in definition.tier(1).build_materials
                    ),
                    "build_materials": [
                        {
                            **material.model_dump(),
                            "item": items[material.item_id].model_dump(),
                            "owned": sum(player.inventory.get(material.item_id, {}).values()),
                        }
                        for material in definition.tier(1).build_materials
                    ],
                }
                for definition in self.content.livestock_facilities
                if definition.id not in built and not definition.granted
            ],
            "next_facility_level": next(
                (
                    definition.min_level
                    for definition in self.content.livestock_facilities
                    if player.level < definition.min_level
                ),
                None,
            ),
            "rules": rules.model_dump() if rules else None,
        }

    # ------------------------------------------------------- 水产：内部实现

    def _pond(self, player: PlayerState, pond_id: str) -> PondState:
        pond = next((entry for entry in player.ponds if entry.pond_id == pond_id), None)
        if pond is None:
            raise GameError("pond_locked", "这口鱼塘尚未开放", 404)
        return pond

    def _feed_slot_rules(self):
        rules = self.content.feed_slot
        if rules is None:
            raise GameError("feed_slot_missing", "饲料槽配置缺失，请联系管理员", 409)
        return rules

    def _pond_definition(self, pond: PondState):
        return self.content.pond_map.get(pond.pond_id)

    def _pond_tier(self, pond: PondState):
        definition = self._pond_definition(pond)
        tier = self.content.pond_tier_map.get(definition.tier if definition else 1)
        if tier is None:
            raise GameError("pond_tier_missing", "鱼塘等级配置缺失，请联系管理员", 409)
        return tier

    def _pond_capacity(self, pond: PondState) -> int:
        return self._pond_tier(pond).capacity

    def _aquatic_ability(self, player: PlayerState, partner_ids: list[str]) -> int:
        """驻场伙伴数据异常时退回主角能力，避免一条坏引用把整个存档卡住。"""
        try:
            total, _, _ = self._production_ability(player, list(partner_ids), "aquatic")
        except GameError:
            rules = self.content.industries["aquatic"]
            return rules.character_base_ability + self._industry_ability_bonus(player, "aquatic")
        return total

    def _pond_cycle_seconds(self, player: PlayerState, pond: PondState) -> int:
        species = self.content.pond_species_map.get(pond.species_id)
        if species is None:
            return 0
        base_seconds = pond_cycle_seconds(
            species.base_cycle_seconds,
            self._aquatic_ability(player, pond.assigned_partner_ids),
            species.time_difficulty,
        )
        return max(1, ceil(base_seconds * pond.cycle_multiplier))

    def _refresh_pond_trait_snapshot(self, player: PlayerState, pond: PondState, now: int) -> None:
        species = self.content.pond_species_map.get(pond.species_id)
        produce_item = self.content.item_map.get(species.produce_item_id) if species else None
        context = {
            "industry": "aquatic",
            "action": "pond_segment",
            "content_id": species.id if species else "",
            "production_slot_id": f"aquatic:pond:{pond.pond_id}",
            "world": self._world_snapshot(now),
            "content_tags": list(produce_item.tags) if produce_item else [],
            "cycle_multiplier": 1.0,
            "feed_multiplier": 1.0,
            "quality_bonus": 0.0,
            "generation_gain_bonus": 0.0,
            "applied_effects": [],
        }
        self._execute_partner_trait_phase(pond.assigned_partner_ids, "asset_prepare", context)
        pond.cycle_multiplier = max(0.01, float(context["cycle_multiplier"]))
        pond.feed_multiplier = max(0.0, float(context["feed_multiplier"]))
        pond.quality_bonus = float(context["quality_bonus"])
        pond.generation_gain_bonus = float(context["generation_gain_bonus"])
        pond.trait_effects = list(context["applied_effects"])
        pond.ability = self._aquatic_ability(player, pond.assigned_partner_ids)
        pond.cycle_seconds = self._pond_cycle_seconds(player, pond)

    def _pond_parameters(self, player: PlayerState, pond: PondState) -> PondParameters:
        tier = self._pond_tier(pond)
        species = self.content.pond_species_map.get(pond.species_id)
        generation_cap = (species.generation_cap if species else 0) + self._talent_modifier(player, "pond_generation_cap")
        return PondParameters(
            # 用存档里的周期快照推进这一段，没有快照（老存档、刚开塘）才现算。
            cycle_seconds=pond.cycle_seconds or self._pond_cycle_seconds(player, pond),
            capacity=tier.capacity,
            growth_rate=species.growth_rate if species else 0,
            maturation_cycles=species.maturation_cycles if species else 1,
            steady_ratio=species.steady_ratio if species else 1,
            generation_gain=max(
                0.0,
                (species.generation_gain if species else 0) + pond.generation_gain_bonus,
            ),
            generation_decay=species.generation_decay if species else 0,
            generation_cap=generation_cap,
            feed_per_cycle=tier.feed_per_cycle * pond.feed_multiplier * self._feed_cost_multiplier(player),
        )

    def _settle_aquatic(self, player: PlayerState, now: int) -> None:
        """把资产格推进到 now，中途在周期边界上兑现排队的换人。

        排队的伙伴要在【下个周期开始】接手。离线跨了好几个周期时，如果等这一整段跑完
        再换，这几个周期就会全都记在旧伙伴头上，而排队的人白锁一夜。所以先把时间切到
        最近的那个边界、换人、再往后跑。
        """

        for _ in range(len(player.ponds) + len(player.livestock_facilities) + 1):
            boundary = self._next_asset_swap_at(player)
            if boundary is None or boundary >= now:
                break
            self._advance_aquatic(player, boundary)
            self._apply_asset_swaps(player, boundary)
        self._advance_aquatic(player, now)
        self._apply_asset_swaps(player, now)

    def _next_asset_swap_at(self, player: PlayerState) -> int | None:
        """最早一个排队换人要生效的时刻，也就是那个格子当前周期跑完的时刻。"""

        cycle = self.content.livestock.cycle_seconds if self.content.livestock else 0
        boundaries: list[int] = []
        for pond in player.ponds:
            # last_settled_at 还没落过盘时算不出边界，交给下一次结算，别推出一个过去的时刻。
            if pond.pending_partner_ids is None or pond.last_settled_at <= 0:
                continue
            if self._pond_cycle_progress(pond) > 0:
                boundaries.append(pond.last_settled_at + pond.cycle_seconds - pond.settle_remainder)
        for facility in player.livestock_facilities:
            if facility.pending_partner_ids is None or facility.last_settled_at <= 0:
                continue
            if self._facility_cycle_progress(player, facility) > 0:
                boundaries.append(facility.last_settled_at + cycle - facility.settle_remainder)
        return min(boundaries) if boundaries else None

    def _apply_asset_swaps(self, player: PlayerState, now: int) -> None:
        """周期跑完（或者这个格子本来就没在跑）的时候，把排队的伙伴换上去。"""

        for pond in player.ponds:
            if pond.pending_partner_ids is None or self._pond_cycle_progress(pond) > 0:
                continue
            pond.assigned_partner_ids = list(pond.pending_partner_ids)
            pond.pending_partner_ids = None
            self._refresh_pond_trait_snapshot(player, pond, now)
        for facility in player.livestock_facilities:
            if facility.pending_partner_ids is None or self._facility_cycle_progress(player, facility) > 0:
                continue
            facility.assigned_partner_ids = list(facility.pending_partner_ids)
            facility.pending_partner_ids = None
            self._refresh_livestock_trait_snapshot(player, facility, now)

    def _advance_aquatic(self, player: PlayerState, now: int) -> None:
        """鱼塘和畜栏共用一个饲料槽，所以两边一起结算。

        先各自报一遍需求，再按同一个比例拿预算 —— 否则谁先结算谁就把槽喝光，另一边
        无缘无故停摆。槽的品质分也要在扣料之前取：先结算的一方把槽喝到 0 会把分数清零，
        后结算的一方这一段的品质就凭空变差了。
        """

        if not player.ponds and not player.livestock_facilities:
            player.feed_slot.updated_at = now
            return
        feed_score = player.feed_slot.quality_score

        pond_parameters = {}
        for pond in player.ponds:
            if pond.last_settled_at <= 0 or pond.last_settled_at > now:
                pond.last_settled_at = now
            pond_parameters[pond.pond_id] = self._pond_parameters(player, pond)
        _, pond_demand = plan_ponds(player.ponds, pond_parameters, now)

        stock_parameters = {}
        stock_demand = 0.0
        for facility in player.livestock_facilities:
            if facility.last_settled_at <= 0 or facility.last_settled_at > now:
                facility.last_settled_at = now
            entry = self._facility_parameters(player, facility, feed_score)
            stock_parameters[facility.facility_id] = entry
            _, units = plan_facility(
                facility,
                self._animals_in(player, facility.facility_id),
                entry,
                max(0, now - facility.last_settled_at),
            )
            stock_demand += units

        demand = pond_demand + stock_demand
        ratio = 1.0 if demand <= 0 else min(1.0, player.feed_slot.units / demand)

        if player.ponds:
            settlement = settle_ponds(player.ponds, player.feed_slot, pond_parameters, now, ratio=ratio)
            self._grant_pond_partner_experience(player, settlement)
            for pond in player.ponds:
                # 推进用旧快照，推进完立刻换成当前参数：天赋和伙伴的改动从下一段开始生效。
                self._refresh_pond_trait_snapshot(player, pond, now)
        if player.livestock_facilities:
            stock = settle_livestock(
                player.livestock_facilities,
                player.animals,
                player.feed_slot,
                stock_parameters,
                now,
                self.rng,
                ratio=ratio,
            )
            self._grant_livestock_partner_experience(player, stock)
            hatched = {animal_id for advance in stock.facilities.values() for animal_id in advance.hatched}
            player.achievement_stats.bred_species_ids = sorted(
                set(player.achievement_stats.bred_species_ids)
                | {animal.species_id for animal in player.animals if animal.animal_id in hatched}
            )
            for facility in player.livestock_facilities:
                # 推进用旧快照，推进完立刻换成当前参数：换伙伴从下一段开始生效。
                self._refresh_livestock_trait_snapshot(player, facility, now)
        player.feed_slot.updated_at = now

    def _grant_pond_partner_experience(self, player: PlayerState, settlement) -> None:
        """驻场看塘的伙伴按结算掉的周期数拿经验。停摆的周期没买单，也就不给经验。"""
        per_cycle = self.content.partner_growth.pond_experience_per_cycle
        if not per_cycle:
            return
        owned_map = {entry.partner_id: entry for entry in player.owned_partners}
        for pond in player.ponds:
            advance = settlement.ponds.get(pond.pond_id)
            if advance is None or advance.paid_cycles <= 0:
                continue
            for partner_id in pond.assigned_partner_ids:
                owned = owned_map.get(partner_id)
                if owned is not None:
                    self._grant_partner_experience(owned, advance.paid_cycles * per_cycle)

    def _talent_modifier(self, player: PlayerState, key: str) -> float:
        return sum(
            node.modifiers.get(key, 0)
            for node_id in player.talent_nodes
            if (node := self.content.talent_map.get(node_id)) is not None
        )

    def _combo_cap(self, player: PlayerState) -> int:
        return int(self.content.fishing_combo.max_layers + self._talent_modifier(player, "fishing_combo_cap"))

    def _current_combo(self, player: PlayerState, spot_id: str, now: int) -> int:
        """换钓点立即清零，久不抛竿逐层衰减。"""
        fishing = player.fishing
        if fishing.spot_id != spot_id:
            return 0
        rules = self.content.fishing_combo
        combo = decayed_combo(
            fishing.combo,
            fishing.combo_updated_at,
            now,
            rules.idle_grace_seconds,
            rules.decay_seconds,
        )
        return min(combo, self._combo_cap(player))

    def _fishing_companion_ids(self, player: PlayerState) -> list[str]:
        return [player.fishing.companion_partner_id] if player.fishing.companion_partner_id else []

    def _fishing_draw_count(self, spot, ability: int, combo: int) -> int:
        rules = self.content.fishing_combo
        base = spot.draws.base_draws + combo * rules.draw_bonus_per_layer
        multiplier = 1 + spot.draws.ability_bonus * max(0, ability) / (max(0, ability) + spot.draws.difficulty)
        return max(1, min(FISHING_MAX_DRAWS, floor(base * multiplier + 0.5)))

    def _fishing_pool(
        self,
        spot,
        combo: int,
        trait_rare_multiplier: float = 1.0,
    ) -> list[tuple[object, float, bool]]:
        """产出池。稀有条目和大物吃聚鱼度的权重加成。"""
        rare_multiplier = (
            1 + self.content.fishing_combo.rare_weight_per_layer * combo
        ) * max(0.0, trait_rare_multiplier)
        pool: list[tuple[object, float, bool]] = [
            (entry, entry.weight * (rare_multiplier if entry.rare else 1), False)
            for entry in spot.outputs
        ]
        if spot.big_catch is not None:
            pool.append((spot.big_catch, spot.big_catch.weight * rare_multiplier, True))
        return pool

    def _pick_fishing_entry(self, pool: list[tuple[object, float, bool]]):
        total = sum(weight for _, weight, _ in pool)
        draw = self.rng.random() * total
        cumulative = 0.0
        for entry, weight, big_catch in pool:
            cumulative += weight
            if draw < cumulative:
                return entry, big_catch
        return pool[-1][0], pool[-1][2]

    def _fishing_output(self, spot, item_id: str):
        return next((entry for entry in spot.outputs if entry.item_id == item_id), None)

    def _roll_size(self, entry) -> float:
        """鱼有体型，杂物没有。返回 0 表示这一项不记体型。"""
        if entry is None or not entry.has_size:
            return 0.0
        return round(entry.size_min + self.rng.random() * (entry.size_max - entry.size_min), 1)

    def _roll_batch(self, entry, quantity: int) -> FishingBatch:
        """一批里每条鱼各摇一个体型，图鉴只记最大的那条。"""
        size = max((self._roll_size(entry) for _ in range(max(1, quantity))), default=0.0)
        return FishingBatch(entry.item_id, quantity, entry.codex, size)

    def _grant_fishing_batches(
        self,
        player: PlayerState,
        batches: list[FishingBatch],
        probabilities: list[float],
        now: int,
        applied_effects: list[dict] | None = None,
    ) -> list[dict]:
        """每件独立判品质。无品质的杂物（鱼苗之类）按 0 档直接进背包。"""
        items = self.content.item_map
        sizes: dict[str, float] = {}
        quality_batches: list[tuple[str, int]] = []
        quality_free: dict[str, int] = {}
        quality_free_order: list[str] = []
        for batch in batches:
            definition = items.get(batch.item_id)
            if batch.size:
                sizes[batch.item_id] = max(sizes.get(batch.item_id, 0.0), batch.size)
            if definition and definition.has_quality:
                quality_batches.append((batch.item_id, batch.quantity))
                continue
            if batch.item_id not in quality_free:
                quality_free_order.append(batch.item_id)
            quality_free[batch.item_id] = quality_free.get(batch.item_id, 0) + batch.quantity
        results = build_results(
            self.rng,
            quality_batches,
            probabilities,
            now,
            applied_effects or (),
        )
        drops = []
        for result in results:
            add_item(player, result.item_id, result.quantity, result.quality)
            drops.append(self._fishing_drop(
                result.item_id,
                result.quantity,
                result.quality,
                sizes.get(result.item_id, 0.0),
            ))
        for item_id in quality_free_order:
            add_item(player, item_id, quality_free[item_id], 0)
            drops.append(self._fishing_drop(item_id, quality_free[item_id], 0, sizes.get(item_id, 0.0)))
        return drops

    def _fishing_drop(self, item_id: str, quantity: int, quality: int, size: float = 0.0) -> dict:
        item = self.content.item_map.get(item_id)
        return {
            "item_id": item_id,
            "name": item.name if item else item_id,
            "icon": item.icon if item else "package",
            "quantity": quantity,
            "quality": quality or None,
            "quality_name": QUALITY_NAMES.get(quality),
            "size": size or None,
            "item": item.model_dump() if item else None,
        }

    def _grant_companion_experience(self, player: PlayerState, stamina_cost: int) -> list[dict]:
        """陪钓伙伴按体力拿经验，口径与其他产业的体力分量一致；没带伙伴就没有这一项。"""
        companion_id = player.fishing.companion_partner_id
        if not companion_id or stamina_cost <= 0:
            return []
        owned = next(
            (entry for entry in player.owned_partners if entry.partner_id == companion_id),
            None,
        )
        if owned is None:
            return []
        amount = stamina_cost * self.content.partner_growth.experience_per_stamina
        return [{"partner_id": owned.partner_id, **self._grant_partner_experience(owned, amount)}]

    def _record_codex(self, player: PlayerState, batches: list[FishingBatch], now: int) -> list[dict]:
        discoveries = []
        for item_id, quantity, codex, size in batches:
            if not codex or quantity <= 0:
                continue
            if self._record_codex_entry(player, item_id, now, quantity=quantity, size=size):
                item = self.content.item_map.get(item_id)
                discoveries.append({"item_id": item_id, "name": item.name if item else item_id})
        return discoveries

    def _record_codex_entry(
        self,
        player: PlayerState,
        item_id: str,
        now: int,
        quantity: int = 1,
        size: float = 0,
    ) -> bool:
        """返回是否是首次记录。大物额外记录尺寸。"""
        entry = player.fish_codex.entry(item_id)
        if entry is None:
            player.fish_codex.entries.append(FishCodexEntry(
                item_id=item_id,
                caught=quantity,
                first_caught_at=now,
                max_size=size,
            ))
            return True
        entry.caught += quantity
        entry.max_size = max(entry.max_size, size)
        return False

    def _claim_codex_milestones(self, player: PlayerState, now: int) -> list[dict]:
        species_count = len(player.fish_codex.entries)
        granted = []
        for milestone in self.content.fish_codex_milestones:
            if milestone.id in player.fish_codex.claimed_milestones:
                continue
            if species_count < milestone.required:
                continue
            player.fish_codex.claimed_milestones.append(milestone.id)
            granted.append({
                "id": milestone.id,
                "name": milestone.name,
                "required": milestone.required,
                "granted": self._grant_reward(player, milestone.reward, now),
            })
        return granted

    def _big_catch_snapshot(self, player: PlayerState, now: int) -> dict | None:
        pending = player.fishing.pending_big_catch
        if pending is None:
            return None
        spot = self.content.fishing_spot_map.get(pending.spot_id)
        if spot is None or spot.big_catch is None:
            return None
        ability = self._aquatic_ability(player, self._fishing_companion_ids(player))
        item = self.content.item_map.get(spot.big_catch.item_id)
        return {
            "spot_id": pending.spot_id,
            "spot_name": spot.name,
            "created_at": pending.created_at,
            "item_id": spot.big_catch.item_id,
            "name": item.name if item else spot.big_catch.item_id,
            "stamina_cost": spot.big_catch.stamina_cost,
            "chance": round(spot.big_catch.success_chance(ability), 3),
            "min_quality": spot.big_catch.min_quality,
        }

    def _aquatic_snapshot(self, player: PlayerState, now: int, partner_map: dict[str, dict]) -> dict:
        items = self.content.item_map
        slot_rules = self.content.feed_slot
        ability = self._aquatic_ability(player, self._fishing_companion_ids(player))
        spots = []
        for spot in self.content.fishing_spots:
            if not self._content_visible(player, spot):
                continue
            unlocked = player.level >= spot.min_level
            combo = self._current_combo(player, spot.id, now) if unlocked else 0
            # 产出表和大物都不下发：钓点该保留神秘感，见过什么去图鉴里看。
            spots.append({
                **spot.model_dump(exclude={"outputs", "big_catch", "quality"}),
                "unlocked": unlocked,
                **self._season_notes(spot, "fishing", now),
                "combo": combo,
                "draws": {
                    **spot.draws.model_dump(),
                    "expected": self._fishing_draw_count(spot, ability, combo) if unlocked else 0,
                },
            })
        ponds = []
        for pond in player.ponds:
            definition = self._pond_definition(pond)
            species = self.content.pond_species_map.get(pond.species_id)
            parameters = self._pond_parameters(player, pond)
            assigned_partners = [
                partner_map[partner_id]
                for partner_id in pond.assigned_partner_ids
                if partner_id in partner_map
            ]
            ponds.append({
                **pond.model_dump(),
                "definition": definition.model_dump() if definition else None,
                "species": species.model_dump() if species else None,
                "produce_item": items[species.produce_item_id].model_dump() if species else None,
                "capacity": parameters.capacity,
                "feed_per_cycle": parameters.feed_per_cycle,
                "generation_cap": parameters.generation_cap,
                "generation_gain": parameters.generation_gain,
                "generation_decay": parameters.generation_decay,
                "steady_stock": parameters.steady_stock if species else 0,
                "cycle_seconds": parameters.cycle_seconds,
                "next_cycle_seconds": max(0, parameters.cycle_seconds - pond.settle_remainder) if species else 0,
                "fry_total": pond.fry_total,
                "population": pond.population,
                "next_spawn": (
                    max(0, min(
                        floor(pond.growth_remainder + pond.stock * parameters.growth_rate),
                        parameters.capacity - pond.population,
                    ))
                    if species else 0
                ),
                "maturation_seconds": int(parameters.maturation_cycles * parameters.cycle_seconds) if species else 0,
                "next_maturation_seconds": (
                    int(min(batch.cycles_left for batch in pond.fry) * parameters.cycle_seconds)
                    if pond.fry and parameters.cycle_seconds
                    else 0
                ),
                "quality_ability": round(
                    self._aquatic_ability(player, pond.assigned_partner_ids)
                    + self._pond_tier(pond).quality_bonus
                    + player.feed_slot.quality_score
                    + pond.generation_score
                    + pond.quality_bonus,
                    2,
                ),
                "empty": pond.empty,
                "assigned_partners": assigned_partners,
                "pending_partner": (
                    partner_map.get(pond.pending_partner_ids[0]) if pond.pending_partner_ids else None
                ),
                "swap_open": self._swap_window_is_open(self._pond_cycle_progress(pond)),
                "swap_window_seconds": self._swap_window_seconds(parameters.cycle_seconds),
            })
        codex_species = {entry.item_id for entry in player.fish_codex.entries}
        codex_pool: list[str] = []
        for spot in self.content.fishing_spots:
            visible = self._content_visible(player, spot)
            for output in spot.outputs:
                if (output.codex and output.item_id not in codex_pool
                        and (visible or output.item_id in codex_species)):
                    codex_pool.append(output.item_id)
            if (spot.big_catch and spot.big_catch.item_id not in codex_pool
                    and (visible or spot.big_catch.item_id in codex_species)):
                codex_pool.append(spot.big_catch.item_id)
        built = {pond.pond_id for pond in player.ponds}
        return {
            "unlocked": any(entry["unlocked"] for entry in spots),
            "ability": ability,
            "buildable_ponds": [
                {
                    **definition.model_dump(),
                    "unlocked": player.level >= definition.min_level,
                    "affordable": player.coins >= definition.build_cost and all(sum(player.inventory.get(i.item_id, {}).values()) >= i.quantity for i in definition.build_materials),
                    "build_materials": [{**i.model_dump(), "item": items[i.item_id].model_dump(), "owned": sum(player.inventory.get(i.item_id, {}).values())} for i in definition.build_materials],
                    "capacity": (self.content.pond_tier_map.get(definition.tier).capacity
                                 if self.content.pond_tier_map.get(definition.tier) else 0),
                }
                for definition in self.content.ponds
                if definition.id not in built
            ],
            "companion_partner_id": player.fishing.companion_partner_id or None,
            "companion": partner_map.get(player.fishing.companion_partner_id),
            "spots": spots,
            "next_spot_level": next(
                (spot["min_level"] for spot in spots if player.level < spot["min_level"]),
                None,
            ),
            "combo_rules": self.content.fishing_combo.model_dump(),
            "combo_cap": self._combo_cap(player),
            "combo": {
                "spot_id": player.fishing.spot_id or None,
                "layers": self._current_combo(player, player.fishing.spot_id, now) if player.fishing.spot_id else 0,
                "updated_at": player.fishing.combo_updated_at,
            },
            "pending_big_catch": self._big_catch_snapshot(player, now),
            "codex": {
                "recorded": len(player.fish_codex.entries),
                "total": len(codex_pool),
                "entries": [
                    {
                        **entry.model_dump(),
                        "item": items[entry.item_id].model_dump() if entry.item_id in items else None,
                    }
                    for entry in player.fish_codex.entries
                ],
                # 没见过的那一格只回一个占位，连名字和图标都不给 —— 直接读接口也看不到谜底。
                "pool": [
                    {"item_id": item_id, "item": items[item_id].model_dump(), "recorded": True}
                    if item_id in codex_species
                    else {"item_id": None, "item": None, "recorded": False}
                    for item_id in codex_pool
                    if item_id in items
                ],
                "milestones": [
                    {
                        **milestone.model_dump(exclude={"reward"}),
                        "reward": self._reward_snapshot(milestone.reward),
                        "claimed": milestone.id in player.fish_codex.claimed_milestones,
                    }
                    for milestone in self.content.fish_codex_milestones
                ],
            },
            "ponds": ponds,
            "next_pond_level": next(
                (entry.min_level for entry in self.content.ponds if player.level < entry.min_level),
                None,
            ),
            "species": [
                {
                    **species.model_dump(),
                    "fry_item": items[species.fry_item_id].model_dump(),
                    "produce_item": items[species.produce_item_id].model_dump(),
                    "owned_fry": sum(player.inventory.get(species.fry_item_id, {}).values()),
                    "unlocked": player.level >= species.min_level,
                }
                for species in self.content.pond_species
            ],
            "feed_slot": self._feed_slot_snapshot(player),
        }

    def _feed_slot_snapshot(self, player: PlayerState) -> dict:
        """饲料槽同时喂着鱼塘和畜栏，所以「每小时几份、还能撑多久」要把两边一起算。"""

        slot_rules = self.content.feed_slot
        items = self.content.item_map
        hourly_rate = round(
            sum(
                self._pond_parameters(player, pond).feed_per_cycle * 3600 / pond.cycle_seconds
                for pond in player.ponds
                if not pond.empty and pond.cycle_seconds > 0
            )
            + self._livestock_hourly_rate(player),
            2,
        )
        return {
            "name": slot_rules.name if slot_rules else "饲料槽",
            "units": round(player.feed_slot.units, 2),
            "quality_score": round(player.feed_slot.quality_score, 2),
            "capacity": self._feed_slot_capacity(player),
            "base_capacity": slot_rules.capacity if slot_rules else 0,
            "hourly_rate": hourly_rate,
            "runtime_seconds": slot_runtime_seconds(player.feed_slot, hourly_rate),
            "quality_multipliers": slot_rules.quality_multipliers if slot_rules else [],
            "inputs": [
                {
                    "item_id": item_id,
                    "quality": quality or None,
                    "quality_name": QUALITY_NAMES.get(quality),
                    "quantity": quantity,
                    "item": items[item_id].model_dump(),
                    "units": items[item_id].feed.units,
                    "unit_score": round(
                        items[item_id].feed.score * (slot_rules.multiplier(quality) if slot_rules else 1),
                        2,
                    ),
                }
                for item_id, qualities in sorted(player.inventory.items())
                if item_id in items and items[item_id].feed is not None
                for quality, quantity in sorted(qualities.items())
                if quantity > 0
            ],
        }

    def convert_maple_flame(self, oauth_sub: str, quantity: int) -> dict:
        quantity = int(quantity)
        if quantity < 1:
            raise GameError("invalid_quantity", "兑换数量必须为正数")
        cost = quantity * self.content.gacha_economy.maple_flame_per_leaf

        def mutation(player: PlayerState):
            if player.maple_flame < cost:
                raise GameError("resource_insufficient", "枫火不足")
            player.maple_flame -= cost
            player.guide_leaves += quantity
            return {"quantity": quantity, "maple_flame_spent": cost}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    # ------------------------------------------------------------------ 月卡

    def _monthly_card_snapshot(self, player: PlayerState, now: int) -> dict:
        card = self.content.monthly_card
        day = self._commission_day(now)
        left = remaining_days(player.monthly_card_expires_on, day)
        items = self.content.item_map
        reward_item = items.get(card.daily_item_id)
        return {
            "active": left > 0,
            "days_left": left,
            "max_days": card.max_days,
            "duration_days": card.duration_days,
            "expires_on": player.monthly_card_expires_on if left else "",
            "expires_at": self._commission_refresh_at(player.monthly_card_expires_on) if left else 0,
            "claimable": left > 0 and player.monthly_card_claimed_on != day,
            "claimed_today": player.monthly_card_claimed_on == day,
            "next_refresh_at": self._commission_refresh_at(day),
            "redeemed_total": player.monthly_card_redeemed,
            "activation_maple_flame": card.activation_maple_flame,
            "daily_maple_flame": card.daily_maple_flame,
            "daily_item_amount": card.daily_item_amount,
            "daily_item": reward_item.model_dump() if reward_item else None,
        }

    def _stamina_snapshot(self, player: PlayerState, now: int) -> dict:
        stamina = self.content.stamina
        day = self._commission_day(now)
        used = player.stamina_purchase_count if player.stamina_purchase_day == day else 0
        items = self.content.item_map
        potion = items.get(stamina.potion_item_id)
        return {
            "potion_item_id": stamina.potion_item_id,
            "potion_restore": stamina.potion_restore,
            "potion_owned": sum(player.inventory.get(stamina.potion_item_id, {}).values()),
            "potion_item": potion.model_dump() if potion else None,
            "purchase_restore": stamina.purchase_restore,
            "purchase_prices": list(stamina.purchase_prices),
            "purchase_used_today": used,
            "purchase_daily_limit": stamina.purchase_daily_limit,
            "purchase_next_price": stamina.purchase_price(used),
            "purchase_resets_at": self._commission_refresh_at(day),
        }

    def redeem_code(self, oauth_sub: str, code: str) -> dict:
        """兑换激活码。先原子占码再改存档，改不动就把码放回去——玩家不该因为撞上上限丢一张码。"""
        code = normalize_code(code)
        if not code:
            raise GameError("invalid_code", "请输入激活码")
        if self.redemption_codes is None:
            raise GameError("invalid_code", "激活码无效或已被使用", 404)
        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        now = self._now()
        entry = self.redemption_codes.claim(code, player.player_id, now)
        if entry is None:
            existing = self.redemption_codes.get(code)
            if existing is not None and existing.redeemed_by == player.player_id:
                raise GameError("code_already_redeemed", "这张激活码你已经兑换过了", 409)
            if existing is not None:
                raise GameError("code_already_redeemed", "这张激活码已经被使用了", 409)
            raise GameError("invalid_code", "激活码无效或已被使用", 404)
        try:
            _, result = self._update_player(player.player_id, self._redeem_mutation(entry, now))
        except Exception:
            self.redemption_codes.release(code, player.player_id)
            raise
        return {"result": result, "state": self._settled_snapshot(player.player_id)}

    def _redeem_mutation(self, entry: RedemptionCode, now: int):
        card = self.content.monthly_card
        day = self._commission_day(now)

        def mutation(player: PlayerState):
            self._settle(player, now)
            expires_on = extend_expiry(player.monthly_card_expires_on, day, card.duration_days)
            days_left = remaining_days(expires_on, day)
            if days_left > card.max_days:
                raise GameError(
                    "monthly_card_capped",
                    f"月卡剩余天数最多 {card.max_days} 天，这张激活码留着以后再用",
                    409,
                )
            player.monthly_card_expires_on = expires_on
            player.monthly_card_redeemed += 1
            player.maple_flame += card.activation_maple_flame
            return {
                "code": entry.code,
                "maple_flame": card.activation_maple_flame,
                "duration_days": card.duration_days,
                "monthly_card": self._monthly_card_snapshot(player, now),
            }

        return mutation

    def claim_monthly_card(self, oauth_sub: str) -> dict:
        """领当天那份月卡奖励。没领就过期，不补发。"""
        card = self.content.monthly_card
        now = self._now()
        day = self._commission_day(now)

        def mutation(player: PlayerState):
            self._settle(player, now)
            if remaining_days(player.monthly_card_expires_on, day) <= 0:
                raise GameError("monthly_card_inactive", "月卡还没激活或者已经到期了", 409)
            if player.monthly_card_claimed_on == day:
                raise GameError("monthly_card_claimed", "今天的月卡奖励已经领过了", 409)
            player.monthly_card_claimed_on = day
            player.maple_flame += card.daily_maple_flame
            if card.daily_item_amount:
                add_item(player, card.daily_item_id, card.daily_item_amount)
            return {
                "maple_flame": card.daily_maple_flame,
                "item_id": card.daily_item_id,
                "item_amount": card.daily_item_amount,
                "monthly_card": self._monthly_card_snapshot(player, now),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    # --------------------------------------------------------------- 体力补给

    def use_stamina_potion(self, oauth_sub: str) -> dict:
        stamina = self.content.stamina
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            try:
                remove_item(player, stamina.potion_item_id, 1)
            except EconomyError as exc:
                raise GameError("resource_insufficient", "没有绯恩特调了") from exc
            gained = grant_stamina(player, stamina.potion_restore, self.content, now)
            return {
                "item_id": stamina.potion_item_id,
                "stamina_gained": gained,
                "stamina": player.stamina,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def buy_stamina(self, oauth_sub: str) -> dict:
        stamina = self.content.stamina
        now = self._now()
        day = self._commission_day(now)

        def mutation(player: PlayerState):
            self._settle(player, now)
            if player.stamina_purchase_day != day:
                player.stamina_purchase_day = day
                player.stamina_purchase_count = 0
            price = stamina.purchase_price(player.stamina_purchase_count)
            if price is None:
                raise GameError(
                    "stamina_purchase_limit",
                    f"今天已经买满 {stamina.purchase_daily_limit} 次体力了",
                    409,
                )
            if player.maple_flame < price:
                raise GameError("resource_insufficient", "枫火不足")
            player.maple_flame -= price
            player.stamina_purchase_count += 1
            gained = grant_stamina(player, stamina.purchase_restore, self.content, now)
            return {
                "maple_flame_spent": price,
                "stamina_gained": gained,
                "stamina": player.stamina,
                "purchase_used_today": player.stamina_purchase_count,
                "purchase_next_price": stamina.purchase_price(player.stamina_purchase_count),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    # ------------------------------------------------------- 激活码（管理侧）

    def generate_redemption_codes(self, count: int, batch: str = "", note: str = "") -> list[str]:
        """批量造码。撞号极其罕见，真撞上就少造一张，不重试。"""
        count = int(count or 0)
        if count < 1 or count > MAX_REDEMPTION_CODE_BATCH:
            raise GameError(
                "invalid_count",
                f"一次最多生成 {MAX_REDEMPTION_CODE_BATCH} 个激活码",
            )
        if self.redemption_codes is None:
            raise GameError("codes_unavailable", "激活码存储没有接入", 503)
        now = self._now()
        batch = str(batch or "").strip()[:40] or datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d-%H%M%S")
        entries = [
            RedemptionCode(
                code=generate_code(),
                kind="monthly_card",
                batch=batch,
                note=str(note or "").strip()[:120],
                created_at=now,
            )
            for _ in range(count)
        ]
        unique = {entry.code: entry for entry in entries}
        self.redemption_codes.create(list(unique.values()))
        return sorted(unique)

    def redeem_code_by_identity(self, identity: QQIdentity, code: str) -> dict:
        return self.redeem_code(self._sub_for_identity(identity), code)

    def claim_monthly_card_by_identity(self, identity: QQIdentity) -> dict:
        return self.claim_monthly_card(self._sub_for_identity(identity))

    def list_redemption_codes(self, limit: int = 200) -> list[dict]:
        if self.redemption_codes is None:
            return []
        limit = max(1, min(int(limit or 200), MAX_REDEMPTION_CODE_LIST))
        records = self.redemption_codes.list_recent(limit)
        names = {}
        for record in records:
            if record.redeemed_by and record.redeemed_by not in names:
                owner = self.repository.get(record.redeemed_by)
                names[record.redeemed_by] = owner.display_name if owner else ""
        return [
            {
                **record.model_dump(),
                "redeemed_by_name": names.get(record.redeemed_by, ""),
            }
            for record in records
        ]

    def recruit(self, oauth_sub: str, count: int, request_id: str, pool_id: str) -> dict:
        if count not in (1, 10):
            raise GameError("invalid_pull_count", "只能单次或十次招募")
        request_id = str(request_id or "").strip()
        if len(request_id) < 8 or len(request_id) > 128:
            raise GameError("invalid_request_id", "招募请求标识不正确")
        now = self._now()
        gacha = self._gacha_pool(pool_id)
        economy = self.content.gacha_economy
        catalog = self.partner_catalog_loader()
        partners_by_rarity = self._pool_partner_candidates(gacha, catalog)

        def mutation(player: PlayerState):
            self._settle(player, now)
            if player.level < gacha.min_level:
                raise GameError("content_locked", f"达到 {gacha.min_level} 级后开放招募")
            previous = next((entry for entry in player.gacha_history if entry.request_id == request_id), None)
            if previous:
                return {**previous.model_dump(), "replayed": True}
            self._require_content(player, gacha)
            if not self._pool_is_open(partners_by_rarity):
                raise GameError("gacha_pool_invalid", "这个招募池暂时无法招募", 500)
            if player.guide_leaves < count:
                raise GameError("resource_insufficient", "引路枫叶不足")
            progress = player.gacha_progress.get(pool_id)
            if progress is None:
                progress = GachaPoolProgressState()
                player.gacha_progress[pool_id] = progress
            if gacha.max_pulls_per_player is not None and progress.total_pulls + count > gacha.max_pulls_per_player:
                remaining = max(0, gacha.max_pulls_per_player - progress.total_pulls)
                raise GameError("gacha_pool_exhausted", f"这个招募池最多能招募 {gacha.max_pulls_per_player} 次，你还剩 {remaining} 次")

            player.guide_leaves -= count
            featured_guaranteed = self._gacha_featured_guaranteed(player, gacha)
            results: list[GachaDropRecord] = []
            for _ in range(count):
                force_five = progress.five_pity + 1 >= gacha.five_star_pity
                force_four = progress.four_pity + 1 >= gacha.four_star_guarantee
                rarity = 5 if force_five else 4 if force_four else self._roll_gacha_rarity(gacha, progress)
                if rarity is None:
                    drop = self._roll_gacha_item(gacha, player)
                    results.append(drop)
                    progress.four_pity += 1
                    progress.five_pity += 1
                else:
                    candidates = partners_by_rarity[rarity]
                    if not candidates:
                        raise GameError("gacha_pool_invalid", f"招募池没有 {rarity} 星伙伴", 500)
                    definition = self._pick_gacha_partner(gacha, candidates, rarity, featured_guaranteed)
                    if rarity == 5 and gacha.featured_partner_id:
                        featured_guaranteed = definition.id != gacha.featured_partner_id
                    owned = next(
                        (entry for entry in player.owned_partners if entry.partner_id == definition.id),
                        None,
                    )
                    marks = economy.duplicate_marks[rarity] if owned else 0
                    if owned:
                        player.companion_marks += marks
                    else:
                        player.owned_partners.append(OwnedPartnerState(
                            partner_id=definition.id,
                            stars=definition.rarity,
                            acquired_at=now,
                        ))
                    results.append(GachaDropRecord(
                        kind="partner",
                        content_id=definition.id,
                        rarity=rarity,
                        duplicate=owned is not None,
                        companion_marks=marks,
                    ))
                    progress.four_pity = 0 if rarity >= 4 else progress.four_pity + 1
                    progress.five_pity = 0 if rarity == 5 else progress.five_pity + 1
                progress.total_pulls += 1

            record = GachaRequestRecord(
                request_id=request_id,
                pool_id=pool_id,
                count=count,
                created_at=now,
                results=results,
            )
            player.gacha_history.append(record)
            return {**record.model_dump(), "replayed": False}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def recruit_by_identity(self, identity: QQIdentity, count: int, request_id: str, pool_id: str) -> dict:
        player_id = self.repository.player_id_for_identity(identity)
        player = self.repository.get(player_id) if player_id else None
        if player is None:
            raise GameError("identity_not_bound", "这个 QQ 身份尚未绑定红叶镇角色", 404)
        return self.recruit(player.oauth_sub, count, request_id, pool_id)

    def train_partner(self, oauth_sub: str, partner_id: str, item_id: str, quantity: int) -> dict:
        quantity = int(quantity)
        if quantity < 1 or quantity > 99:
            raise GameError("invalid_quantity", "使用数量需在 1 到 99 之间")
        book_experience = self.content.partner_growth.experience_books.get(item_id)
        if book_experience is None:
            raise GameError("experience_item_invalid", "这个物品不能用于伙伴升级")

        def mutation(player: PlayerState):
            owned = self._owned_partner(player, partner_id)
            try:
                remove_item(player, item_id, quantity)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            progress = self._grant_partner_experience(owned, book_experience * quantity)
            return {"partner_id": partner_id, "item_id": item_id, "quantity": quantity, **progress}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    def star_up_partner(self, oauth_sub: str, partner_id: str) -> dict:
        def mutation(player: PlayerState):
            owned = self._owned_partner(player, partner_id)
            if owned.stars >= 5:
                raise GameError("partner_max_stars", "伙伴已经达到五星")
            cost = self.content.gacha_economy.star_up_costs[owned.stars]
            if player.companion_marks < cost:
                raise GameError("resource_insufficient", "同行印记不足")
            player.companion_marks -= cost
            owned.stars += 1
            return {"partner_id": partner_id, "stars": owned.stars, "companion_marks_spent": cost}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    def select_partner_artwork(self, oauth_sub: str, partner_id: str, stage: int) -> dict:
        catalog = self.partner_catalog_loader()

        def mutation(player: PlayerState):
            owned = self._owned_partner(player, partner_id)
            definition = catalog.partner_map.get(partner_id)
            if definition is None:
                raise GameError("partner_not_found", "伙伴配置不存在", 404)
            if type(stage) is not int or not 0 <= stage <= owned.breakthrough:
                raise GameError("partner_artwork_locked", "尚未解锁这张立绘", 409)
            if definition.artwork_for(stage) is None:
                raise GameError("partner_artwork_missing", "这张立绘尚未配置", 409)
            owned.artwork_stage = stage
            return {"partner_id": partner_id, "artwork_stage": stage}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    def breakthrough_partner(self, oauth_sub: str, partner_id: str) -> dict:
        catalog = self.partner_catalog_loader()

        def mutation(player: PlayerState):
            self._settle(player, self._now())
            owned = self._owned_partner(player, partner_id)
            definition = catalog.partner_map.get(partner_id)
            if definition is None:
                raise GameError("partner_not_found", "伙伴配置不存在", 404)
            if owned.breakthrough >= 2:
                raise GameError("partner_max_breakthrough", "伙伴已经完成全部突破")
            target = owned.breakthrough + 1
            ascension = next((entry for entry in definition.ascensions if entry.breakthrough == target), None)
            if ascension is None:
                raise GameError("breakthrough_unavailable", "这个伙伴暂不能突破", 409)
            if player.level < ascension.min_player_level:
                raise GameError("breakthrough_player_level", f"居民达到 {ascension.min_player_level} 级后才能突破", 409)
            required_level = level_cap_for_breakthrough(owned.breakthrough)
            if owned.level < required_level:
                raise GameError("breakthrough_partner_level", f"伙伴达到 {required_level} 级后才能突破", 409)
            try:
                spend_coins(player, ascension.coins)
                consumed = []
                for requirement in ascension.items:
                    consumed.extend(self._consume_minimum_quality_items(
                        player,
                        requirement.item_id,
                        requirement.quantity,
                        requirement.min_quality,
                    ))
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            owned.breakthrough = target
            progress = self._grant_partner_experience(owned, 0)
            return {
                "partner_id": partner_id,
                "breakthrough": target,
                "coins_spent": ascension.coins,
                "consumed": [entry.model_dump() for entry in consumed],
                **progress,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    def use_active_task_item(self, oauth_sub: str, industry: str, slot_id: str, task_item_id: str) -> dict:
        now = self._now()
        definition = self.content.task_item_map.get(task_item_id)
        if definition is None or definition.effect != "instant_finish":
            raise GameError("task_item_invalid", "这个道具不能用于进行中的任务")
        if definition.eligible_industries and industry not in definition.eligible_industries:
            raise GameError("task_item_industry_mismatch", "这个道具不能用于当前产业")

        def mutation(player: PlayerState):
            self._settle(player, now)
            production_slot = self._production_slot(player, industry, slot_id)
            units = self._farm_units(player, int(slot_id)) if industry == "farming" else [production_slot]
            if player.task_items.get(task_item_id, 0) < len(units):
                raise GameError("task_item_insufficient", f"需要 {len(units)} 份道具")
            for production_slot in units:
                task = production_slot.task_snapshot
                if task is None or task.ready_at <= now:
                    raise GameError("task_not_active", "这里没有可以加速的任务")
                if task.ready_at - now > int(definition.value):
                    raise GameError("task_item_limit", "任务剩余时间过长，暂时不能使用夜灯茶")
                player.task_items[task_item_id] -= 1
                if player.task_items[task_item_id] <= 0:
                    player.task_items.pop(task_item_id, None)
                completed_at = max(now, task.started_at + 1)
                task.final_duration = completed_at - task.started_at
                task.minimum_duration = min(task.minimum_duration, task.final_duration)
                task.ready_at = completed_at
                if hasattr(production_slot, "ready_at"):
                    production_slot.ready_at = completed_at
                task.applied_effects.append({
                    "source_type": "task_item",
                    "task_item_id": definition.id,
                    "name": definition.name,
                    "effect": definition.effect,
                    "value": definition.value,
                })
            self._settle(player, completed_at)
            return {"industry": industry, "slot_id": slot_id, "ready_at": completed_at}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def cancel_task(self, oauth_sub: str, industry: str, slot_id: str) -> dict:
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            production_slot = self._production_slot(player, industry, slot_id)
            task = production_slot.task_snapshot
            if task is None:
                raise GameError("task_not_active", "这里没有可以取消的任务")
            if task.ready_at <= now:
                raise GameError("task_already_ready", "任务已经完成，请直接收取产出")
            if industry == "crafting" and production_slot.queue_total:
                reserved = production_slot.queued_tasks
                stamina_cost = sum(entry.stamina_cost for entry in reserved)
                refund_stamina(player, stamina_cost, self.content, now)
                refunded_inputs = []
                refunded_task_items = {}
                for queued in reserved:
                    for entry in queued.consumed_inputs:
                        add_item(player, entry.item_id, entry.quantity, entry.quality)
                        refunded_inputs.append(entry.model_dump())
                    for effect in queued.applied_effects:
                        item_id = effect.get("task_item_id")
                        if item_id:
                            player.task_items[item_id] = player.task_items.get(item_id, 0) + 1
                            refunded_task_items[item_id] = refunded_task_items.get(item_id, 0) + 1
                production_slot.queued_tasks = []
                production_slot.task_snapshot = None
                production_slot.task_results = []
                if production_slot.completed_tasks:
                    last = production_slot.completed_tasks.pop()
                    production_slot.task_snapshot = last.task_snapshot
                    production_slot.task_results = last.task_results
                return {
                    "industry": industry,
                    "slot_id": slot_id,
                    "refunded_inputs": refunded_inputs,
                    "refunded_stamina": stamina_cost,
                    "refunded_task_items": refunded_task_items,
                }
            units = self._farm_units(player, int(slot_id)) if industry == "farming" else [production_slot]
            tasks = [unit.task_snapshot for unit in units]
            if any(entry is None or entry.ready_at <= now for entry in tasks):
                raise GameError("task_already_ready", "任务已完成，请领取产出")
            stamina_cost = sum(entry.stamina_cost for entry in tasks)
            refund_stamina(player, stamina_cost, self.content, now)
            refunded_inputs = []
            refunded_task_items = {}
            for unit, entry in zip(units, tasks):
                for ingredient in entry.consumed_inputs:
                    add_item(player, ingredient.item_id, ingredient.quantity, ingredient.quality)
                    refunded_inputs.append(ingredient.model_dump())
                for effect in entry.applied_effects:
                    item_id = effect.get("task_item_id")
                    if item_id:
                        player.task_items[item_id] = player.task_items.get(item_id, 0) + 1
                        refunded_task_items[item_id] = refunded_task_items.get(item_id, 0) + 1
                unit.task_snapshot = None
                unit.task_results = []
                if industry == "farming":
                    unit.crop_id = ""
                    unit.planted_at = 0
                    unit.ready_at = 0
            return {
                "industry": industry,
                "slot_id": slot_id,
                "refunded_inputs": refunded_inputs,
                "refunded_stamina": stamina_cost,
                "refunded_task_items": refunded_task_items,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def unlock_talent(self, oauth_sub: str, node_id: str) -> dict:
        node = self.content.talent_map.get(node_id)
        if node is None:
            raise GameError("talent_not_found", "天赋节点不存在", 404)

        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            self._require_content(player, node)
            if node_id in player.talent_nodes:
                raise GameError("talent_already_unlocked", "这个天赋已经点亮", 409)
            if player.level < node.min_level:
                raise GameError("talent_level_required", f"达到 {node.min_level} 级后才能点亮")
            if any(prerequisite not in player.talent_nodes for prerequisite in node.prerequisites):
                raise GameError("talent_prerequisite_required", "请先点亮前置天赋")
            if self._available_talent_points(player) < node.cost:
                raise GameError("talent_points_insufficient", "天赋点不足")
            player.talent_nodes.append(node_id)
            return {
                "node_id": node_id,
                "industry": node.industry,
                "partner_capacity": self._industry_partner_capacity(player, node.industry),
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    def assign_partner(self, oauth_sub: str, slot: int, partner_id: str = "") -> dict:
        partner_id = str(partner_id or "").strip()
        now = self._now()
        catalog = self.partner_catalog_loader()
        farming_rules = self.content.industries["farming"]

        def mutation(player: PlayerState):
            self._settle(player, now)
            plot = self._plot(player, slot)
            desired_ids = [partner_id] if partner_id else []
            if plot.assigned_partner_ids == desired_ids:
                return {"slot": slot, "partner_id": partner_id or None, "changed": False}
            if (
                not plot.empty
                and plot.ready_at > now
                and not self._task_releases_partner(plot.task_snapshot)
            ):
                raise GameError("partner_assignment_locked", "任务进行中，不能调整这块土地的伙伴", 409)

            locked_until = self._partner_lock_deadlines(player, now)
            affected_ids = set(plot.assigned_partner_ids)
            if partner_id:
                affected_ids.add(partner_id)
            locked_id = next((entry for entry in affected_ids if locked_until.get(entry, 0) > now), None)
            if locked_id:
                raise GameError("partner_locked", "伙伴正在参与进行中的任务，暂时不能移动", 409)

            if partner_id:
                owned = next((entry for entry in player.owned_partners if entry.partner_id == partner_id), None)
                if owned is None:
                    raise GameError("partner_not_owned", "你还没有这个伙伴", 404)
                definition = catalog.partner_map.get(partner_id)
                if definition is None:
                    raise GameError("partner_not_found", "伙伴配置不存在", 404)
                if not any(tendency.industry == "farming" for tendency in definition.tendencies):
                    raise GameError("partner_tendency_mismatch", "这个伙伴没有农作倾向", 409)

            if partner_id:
                self._clear_partner_assignment(player, partner_id)
                if player.fishing.companion_partner_id == partner_id:
                    player.fishing.companion_partner_id = ""
            plot.assigned_partner_ids = desired_ids
            assigned_count = self._industry_assigned_count(player, "farming")
            if assigned_count > self._industry_partner_capacity(player, "farming"):
                raise GameError("partner_capacity_reached", "当前农作伙伴编制已满", 409)
            return {"slot": slot, "partner_id": partner_id or None, "changed": True}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def sell(self, oauth_sub: str, item_id: str, quantity: int, quality: int = 0) -> dict:
        if quantity < 1:
            raise GameError("invalid_quantity", "出售数量必须为正数")
        item = self.content.item_map.get(item_id)
        if not item or item.sell_price <= 0:
            raise GameError("item_not_sellable", "该物品不能出售")
        if not item.has_quality and quality != 0:
            raise GameError("invalid_quality", "该物品不使用品质")
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            selected_quality = quality
            if item.has_quality and selected_quality == 0:
                available = [
                    candidate
                    for candidate, amount in player.inventory.get(item_id, {}).items()
                    if candidate in self.content.quality.grade_map and amount > 0
                ]
                if len(available) != 1:
                    raise GameError("invalid_quality", "请选择要出售的物品品质")
                selected_quality = available[0]
            if item.has_quality and selected_quality not in self.content.quality.grade_map:
                raise GameError("invalid_quality", "请选择要出售的物品品质")
            unit_price = self._quality_unit_price(item.sell_price, selected_quality)
            try:
                remove_item(player, item_id, quantity, selected_quality)
                coins = unit_price * quantity
                grant_coins(player, coins)
            except EconomyError as exc:
                raise GameError("economy_error", str(exc)) from exc
            return {
                "item_id": item_id,
                "quantity": quantity,
                "quality": selected_quality or None,
                "quality_name": QUALITY_NAMES.get(selected_quality),
                "unit_price": unit_price,
                "coins": coins,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    def partner_select_candidates(self, oauth_sub: str) -> dict:
        """伙伴邀约函的可选名单：写死名单里、玩家尚未拥有的伙伴，附详情供弹窗展示。"""
        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        catalog = self.partner_catalog_loader()
        trait_definitions = {trait.code: trait for trait in partner_trait_catalog()}
        owned_ids = {owned.partner_id for owned in player.owned_partners}
        candidates = []
        for partner_id in sorted(PARTNER_SELECT_IDS):
            definition = catalog.partner_map.get(partner_id)
            if definition is None or partner_id in owned_ids:
                continue
            artwork = definition.artwork_for(0)
            avatar_crop = next((crop for crop in definition.avatar_crops if crop.breakthrough == 0), None)
            candidates.append({
                "id": definition.id,
                "name": definition.name,
                "rarity": definition.rarity,
                "description": definition.description,
                "growth_curve_name": GROWTH_CURVE_NAMES[definition.growth_curve],
                "artwork": artwork.model_dump() if artwork else None,
                "avatar_crop": avatar_crop.model_dump() if avatar_crop else None,
                "tendencies": [
                    {
                        "industry": tendency.industry,
                        "name": INDUSTRY_NAMES[tendency.industry],
                        "ability": definition.ability_at(tendency.industry, 1, definition.rarity),
                    }
                    for tendency in definition.tendencies
                ],
                "traits": [
                    {
                        "code": code,
                        "name": trait_definitions[code].name if code in trait_definitions else code,
                        "description": trait_definitions[code].description if code in trait_definitions else "",
                    }
                    for code in definition.trait_codes
                ],
                "companion_marks_granted": PARTNER_SELECT_MARK_COMPENSATION[definition.rarity],
            })
        candidates.sort(key=lambda entry: (-entry["rarity"], entry["id"]))
        return {"candidates": candidates, "eligible_total": len(PARTNER_SELECT_IDS)}

    def use_partner_select_item(self, oauth_sub: str, item_id: str, partner_id: str) -> dict:
        """使用伙伴邀约函（带 usable 标签的道具），把选中的伙伴迎进小镇。"""
        item = self.content.item_map.get(item_id)
        if item is None or not item.has_tag("usable"):
            raise GameError("item_not_usable", "这个物品不能使用")
        if partner_id not in PARTNER_SELECT_IDS:
            raise GameError("partner_not_selectable", "这个伙伴不在邀约名单里")
        catalog = self.partner_catalog_loader()
        definition = catalog.partner_map.get(partner_id)
        if definition is None:
            raise GameError("partner_not_found", "伙伴配置不存在", 404)
        now = self._now()

        def mutation(player: PlayerState):
            if any(owned.partner_id == partner_id for owned in player.owned_partners):
                raise GameError("partner_already_owned", "你已经有这个伙伴了", 409)
            try:
                remove_item(player, item_id, 1)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            owned = OwnedPartnerState(partner_id=partner_id, stars=definition.rarity, acquired_at=now)
            player.owned_partners.append(owned)
            marks = PARTNER_SELECT_MARK_COMPENSATION[definition.rarity]
            player.companion_marks += marks
            return {
                "partner_id": partner_id,
                "name": definition.name,
                "stars": definition.rarity,
                "companion_marks_granted": marks,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    def deliver_tribute(self, oauth_sub: str, portal_id: str, tribute_id: str, quantity: int) -> dict:
        """往传送门交一批贡品。交满这一项就结算它的奖励，全部交满再结算全完成奖励。"""
        portal = self.content.portal_map.get(portal_id)
        tribute = next((entry for entry in portal.tributes if entry.id == tribute_id), None) if portal else None
        if portal is None or tribute is None:
            raise GameError("tribute_not_found", "这个传送门没有该贡品", 404)
        requested = int(quantity)
        if requested <= 0:
            raise GameError("invalid_quantity", "交付数量必须为正数")
        now = self._now()

        def mutation(player: PlayerState):
            self._settle(player, now)
            if not self._portal_unlocked(player, portal):
                raise GameError("portal_locked", f"传送门尚未开启：{self._portal_locked_reason(player, portal)}", 409)
            progress = self._portal_progress(player, portal)
            entry = progress.tribute(tribute_id)
            if entry.completed_at:
                raise GameError("tribute_completed", "这项贡品已经交齐了", 409)

            remaining = tribute.quantity - entry.delivered
            accepted = min(requested, remaining)
            available = self._eligible_quantity(player, tribute)
            if available < accepted:
                item = self.content.item_map.get(tribute.item_id)
                name = item.name if item else tribute.item_id
                requirement = f"{QUALITY_NAMES[tribute.min_quality]}以上的" if tribute.min_quality else ""
                raise GameError("resource_insufficient", f"{requirement}{name}数量不足")

            consumed = self._consume_tribute(player, tribute, accepted)
            entry.delivered += accepted
            rewards = []
            if entry.delivered >= tribute.quantity:
                entry.completed_at = now
                rewards.append({"source": "tribute", "label": self._tribute_label(tribute), **self._grant_reward(player, tribute.reward, now)})
            portal_completed = False
            unlocked_portals: list[dict] = []
            if not progress.completed_at and all(item.completed_at for item in progress.tributes):
                progress.completed_at = now
                portal_completed = True
                rewards.append({"source": "portal", "label": portal.name, **self._grant_reward(player, portal.completion_reward, now)})
                unlocked_portals = self._newly_unlocked_portals(player, portal)
            return {
                "portal_id": portal.id,
                "portal_name": portal.name,
                "tribute_id": tribute_id,
                "delivered": accepted,
                "total_delivered": entry.delivered,
                "required": tribute.quantity,
                "tribute_completed": bool(entry.completed_at),
                "portal_completed": portal_completed,
                "consumed": [snapshot.model_dump() for snapshot in consumed],
                "rewards": rewards,
                "unlocked_portals": unlocked_portals,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def _portal_unlocked(self, player: PlayerState, portal) -> bool:
        if player.level < portal.min_level:
            return False
        completed = self._completed_portal_ids(player)
        return all(entry in completed for entry in portal.prerequisites)

    def _portal_locked_reason(self, player: PlayerState, portal) -> str:
        if player.level < portal.min_level:
            return f"居民等级达到 {portal.min_level} 级"
        completed = self._completed_portal_ids(player)
        missing = [
            self.content.portal_map[entry].name
            for entry in portal.prerequisites
            if entry not in completed and entry in self.content.portal_map
        ]
        return f"先完成{'、'.join(missing)}" if missing else "条件未满足"

    @staticmethod
    def _completed_portal_ids(player: PlayerState) -> set[str]:
        return {entry.portal_id for entry in player.portals if entry.completed_at}

    def _portal_progress(self, player: PlayerState, portal) -> PortalProgressState:
        """进度按需建档，并补上内容里新增的贡品条目。"""
        progress = next((entry for entry in player.portals if entry.portal_id == portal.id), None)
        if progress is None:
            progress = PortalProgressState(portal_id=portal.id)
            player.portals.append(progress)
        recorded = {entry.tribute_id for entry in progress.tributes}
        progress.tributes.extend(
            PortalTributeProgress(tribute_id=tribute.id)
            for tribute in portal.tributes
            if tribute.id not in recorded
        )
        return progress

    def _tribute_label(self, tribute) -> str:
        item = self.content.item_map.get(tribute.item_id)
        prefix = f"{QUALITY_NAMES[tribute.min_quality]}以上的" if tribute.min_quality else ""
        return f"{prefix}{item.name if item else tribute.item_id} ×{tribute.quantity}"

    def _eligible_quantity(self, player: PlayerState, tribute) -> int:
        return sum(
            quantity
            for quality, quantity in player.inventory.get(tribute.item_id, {}).items()
            if quality >= tribute.min_quality
        )

    def _consume_tribute(self, player: PlayerState, tribute, amount: int) -> list[TaskInputSnapshot]:
        """够格的品质里优先扣最低的，玩家不会被贡品吃掉臻品。"""
        consumed: list[TaskInputSnapshot] = []
        remaining = amount
        qualities = player.inventory.get(tribute.item_id, {})
        for quality, owned in sorted(qualities.items()):
            if quality < tribute.min_quality or remaining <= 0:
                continue
            taken = min(owned, remaining)
            remove_item(player, tribute.item_id, taken, quality)
            consumed.append(TaskInputSnapshot(item_id=tribute.item_id, quality=quality, quantity=taken))
            remaining -= taken
        return consumed

    def _grant_reward(self, player: PlayerState, reward, now: int) -> dict:
        granted_items = []
        for entry in reward.items:
            add_item(player, entry.item_id, entry.quantity, entry.quality)
            item = self.content.item_map.get(entry.item_id)
            granted_items.append({
                "item_id": entry.item_id,
                "name": item.name if item else entry.item_id,
                "quantity": entry.quantity,
                "quality": entry.quality or None,
                "quality_name": QUALITY_NAMES.get(entry.quality),
            })
        granted_partners = []
        partner_map = self.partner_catalog_loader().partner_map
        for partner_id in reward.partner_ids:
            if partner_id not in partner_map:
                continue
            if any(owned.partner_id == partner_id for owned in player.owned_partners):
                continue
            player.owned_partners.append(OwnedPartnerState(
                partner_id=partner_id,
                stars=partner_map[partner_id].rarity,
                acquired_at=now,
            ))
            granted_partners.append({"partner_id": partner_id, "name": partner_map[partner_id].name})
        if reward.coins:
            grant_coins(player, reward.coins)
        if reward.talent_points:
            player.bonus_talent_points += reward.talent_points
        if reward.maple_flame:
            player.maple_flame += reward.maple_flame
        if reward.guide_leaves:
            player.guide_leaves += reward.guide_leaves
        levels = grant_experience(player, reward.experience, self.content, max_level=self._player_level_cap(player)) if reward.experience else []
        return {
            "coins": reward.coins,
            "experience": reward.experience,
            "talent_points": reward.talent_points,
            "maple_flame": reward.maple_flame,
            "guide_leaves": reward.guide_leaves,
            "items": granted_items,
            "partners": granted_partners,
            "levels": levels,
        }

    def _newly_unlocked_portals(self, player: PlayerState, completed) -> list[dict]:
        return [
            {"portal_id": portal.id, "name": portal.name}
            for portal in self.content.portals
            if completed.id in portal.prerequisites and self._portal_unlocked(player, portal)
        ]

    # ---------------------------------------------------------------- 今日委托

    def submit_commission(self, oauth_sub: str) -> dict:
        """自己交自己的委托，拿全额枫火。"""
        now = self._now()
        player = self._require_player(oauth_sub)
        self._apply_commission_payouts(player.player_id)

        def mutation(state: PlayerState):
            self._settle(state, now)
            commission = self._require_commission(state)
            if commission.status != "open":
                raise GameError("commission_not_open", self._commission_status_reason(commission), 409)
            consumed = self._consume_commission_items(state, commission)
            commission.status = "completed"
            commission.completed_at = now
            commission.completed_by_name = state.display_name
            state.maple_flame += commission.reward_maple_flame
            state.achievement_stats.own_commissions_completed += 1
            state.achievement_stats.commissions_completed += 1
            return {
                "commission_id": commission.commission_id,
                "npc_name": commission.npc_name,
                "item_id": commission.item_id,
                "quantity": commission.quantity,
                "lucky": commission.lucky,
                "maple_flame": commission.reward_maple_flame,
                "consumed": [snapshot.model_dump() for snapshot in consumed],
            }

        state, result = self._update_player(player.player_id, mutation, now)
        return {"result": result, "state": self._snapshot(state, now)}

    def forward_commission(self, oauth_sub: str) -> dict:
        """转发到公共池。别人交掉之后本人拿回配置里的那一份，剩下的归接单的人。"""
        now = self._now()
        board = self._board()
        player = self._require_player(oauth_sub)
        self._apply_commission_payouts(player.player_id)
        published: dict[str, CommissionBoardEntry] = {}

        def mutation(state: PlayerState):
            self._settle(state, now)
            commission = self._require_commission(state)
            if commission.status != "open":
                raise GameError("commission_not_open", self._commission_status_reason(commission), 409)
            commission.status = "forwarded"
            commission.forwarded_at = now
            published["entry"] = self._board_entry(state, commission)
            return {"commission_id": commission.commission_id}

        state, result = self.repository.update(player.player_id, mutation)
        entry = published["entry"]
        try:
            board.publish(entry)
        except Exception:
            # 池子里没有这一条的话，玩家会卡在“已转发”却谁也看不见的状态，必须回滚。
            self.repository.update(player.player_id, lambda rollback: self._revert_forward(rollback, entry.commission_id))
            raise
        return {
            "result": {**result, "owner_reward": entry.owner_reward, "taker_reward": entry.taker_reward},
            "state": self._snapshot(state, now),
        }

    def withdraw_commission(self, oauth_sub: str) -> dict:
        """还没被接走就能撤回，撤回之后自己交仍然是全额。"""
        now = self._now()
        board = self._board()
        player = self._require_player(oauth_sub)
        self._apply_commission_payouts(player.player_id)
        # 回款可能刚刚入账，撤回资格要看结算之后的状态。
        commission = self._require_commission(self._require_player(oauth_sub))
        if commission.status == "forward_completed":
            raise GameError("commission_already_taken", "这份委托已经有人替你完成了", 409)
        if commission.status != "forwarded":
            raise GameError("commission_not_forwarded", "这份委托没有在转发池里", 409)
        entry = board.withdraw(commission.day, commission.commission_id, player.player_id)
        if entry is None:
            raise GameError("commission_already_taken", "这份委托已经被别人接走了", 409)
        try:
            state, result = self.repository.update(
                player.player_id,
                lambda target: self._revert_forward(target, commission.commission_id),
            )
        except Exception:
            board.publish(entry)
            raise
        return {"result": result, "state": self._snapshot(state, now)}

    def take_commission(self, oauth_sub: str, commission_id: str) -> dict:
        """替别人交一份转发出来的委托。先抢占池子里的名额，再扣物品发奖励。"""
        now = self._now()
        board = self._board()
        player = self._require_player(oauth_sub)
        self._apply_commission_payouts(player.player_id)
        day = self._commission_day(now)
        entry = board.get(day, str(commission_id))
        if entry is None:
            raise GameError("commission_not_found", "这份委托不在转发池里", 404)
        if entry.owner_id == player.player_id:
            raise GameError("commission_own", "不能接自己的委托", 409)
        self._check_take_eligibility(player, entry, day)
        payout = CommissionPayout(
            commission_id=entry.commission_id,
            day=day,
            maple_flame=entry.owner_reward,
            taker_name=player.display_name,
            item_id=entry.item_id,
            quantity=entry.quantity,
            completed_at=now,
        )
        if board.take(day, entry.commission_id, player.player_id, payout) is None:
            raise GameError("commission_already_taken", "手慢了，这份委托刚被接走", 409)

        def mutation(state: PlayerState):
            self._settle(state, now)
            self._check_take_eligibility(state, entry, day)
            consumed = self._consume_commission_items(state, entry)
            state.maple_flame += entry.taker_reward
            state.commission_takes.append(TakenCommissionRecord(
                day=day,
                commission_id=entry.commission_id,
                owner_name=entry.owner_name,
                item_id=entry.item_id,
                quantity=entry.quantity,
                reward_maple_flame=entry.taker_reward,
                completed_at=now,
            ))
            del state.commission_takes[:-30]
            state.achievement_stats.commissions_completed += 1
            return {
                "commission_id": entry.commission_id,
                "owner_name": entry.owner_name,
                "npc_name": entry.npc_name,
                "item_id": entry.item_id,
                "quantity": entry.quantity,
                "lucky": entry.lucky,
                "maple_flame": entry.taker_reward,
                "consumed": [snapshot.model_dump() for snapshot in consumed],
            }

        try:
            state, result = self._update_player(player.player_id, mutation, now)
        except Exception:
            board.release(day, entry.commission_id, player.player_id, payout)
            raise
        return {"result": result, "state": self._snapshot(state, now)}

    def submit_commission_by_identity(self, identity: QQIdentity) -> dict:
        return self.submit_commission(self._sub_for_identity(identity))

    def commission_board_by_identity(self, identity: QQIdentity) -> dict:
        return self.commission_board_snapshot(self._sub_for_identity(identity))

    def _sub_for_identity(self, identity: QQIdentity) -> str:
        player_id = self.repository.player_id_for_identity(identity)
        player = self.repository.get(player_id) if player_id else None
        if player is None:
            raise GameError("identity_not_bound", "这个 QQ 身份尚未绑定红叶镇角色", 404)
        return player.oauth_sub

    def commission_board_snapshot(self, oauth_sub: str) -> dict:
        """公共转发池的当前内容。自己的那份不出现在列表里。"""
        now = self._now()
        player = self._require_player(oauth_sub)
        self._apply_commission_payouts(player.player_id)
        board = self.commission_board
        day = self._commission_day(now)
        entries = board.list_open(day, player.player_id, self.content.commissions.board_limit) if board else []
        state = self.repository.get(player.player_id) or player
        remaining = self._remaining_takes(state, day)
        items = self.content.item_map
        return {
            "day": day,
            "available": board is not None,
            "refresh_at": self._commission_refresh_at(day),
            "remaining_takes": remaining,
            "daily_take_limit": self.content.commissions.daily_take_limit,
            "entries": [
                {
                    **entry.model_dump(exclude={"owner_id"}),
                    "item": items[entry.item_id].model_dump() if entry.item_id in items else None,
                    "owned": self._owned_quantity(state, entry.item_id),
                    "can_take": remaining > 0 and self._owned_quantity(state, entry.item_id) >= entry.quantity,
                }
                for entry in entries
            ],
        }

    def _board(self) -> CommissionBoardRepository:
        if self.commission_board is None:
            raise GameError("commission_board_unavailable", "转发池暂时不可用", 503)
        return self.commission_board

    def _require_player(self, oauth_sub: str) -> PlayerState:
        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        return player

    def _require_commission(self, player: PlayerState) -> CommissionState:
        if player.commission is None:
            raise GameError("commission_unavailable", "今天还没有委托", 404)
        return player.commission

    def _commission_status_reason(self, commission: CommissionState) -> str:
        if commission.status == "forwarded":
            return "这份委托已经转发到公共池了"
        return "今天的委托已经完成了"

    def _commission_day(self, now: int) -> str:
        commissions = self.content.commissions
        return commission_day(now, commissions.reset_hour, commissions.timezone)

    def _commission_refresh_at(self, day: str) -> int:
        commissions = self.content.commissions
        return commission_day_end(day, commissions.reset_hour, commissions.timezone)

    def _owned_quantity(self, player: PlayerState, item_id: str) -> int:
        return sum(player.inventory.get(item_id, {}).values())

    def _remaining_takes(self, player: PlayerState, day: str) -> int:
        taken = sum(1 for entry in player.commission_takes if entry.day == day)
        return max(0, self.content.commissions.daily_take_limit - taken)

    def _check_take_eligibility(self, player: PlayerState, entry: CommissionBoardEntry, day: str) -> None:
        if self._remaining_takes(player, day) <= 0:
            raise GameError("commission_take_limit", "今天已经替别人跑过一趟了", 409)
        if any(record.commission_id == entry.commission_id for record in player.commission_takes):
            raise GameError("commission_already_taken", "你已经接过这份委托了", 409)
        if self._owned_quantity(player, entry.item_id) < entry.quantity:
            item = self.content.item_map.get(entry.item_id)
            raise GameError("resource_insufficient", f"{item.name if item else entry.item_id}数量不足")

    def _consume_commission_items(self, player: PlayerState, request) -> list[TaskInputSnapshot]:
        """委托暂时不看品质，所以从最低品质开始扣，不会吃掉玩家的臻品。"""
        try:
            return self._consume_minimum_quality_items(player, request.item_id, request.quantity, 0)
        except EconomyError as exc:
            item = self.content.item_map.get(request.item_id)
            raise GameError("resource_insufficient", f"{item.name if item else request.item_id}数量不足") from exc

    def _board_entry(self, player: PlayerState, commission: CommissionState) -> CommissionBoardEntry:
        commissions = self.content.commissions
        return CommissionBoardEntry(
            commission_id=commission.commission_id,
            day=commission.day,
            owner_id=player.player_id,
            owner_name=player.display_name,
            npc_name=commission.npc_name,
            npc_title=commission.npc_title,
            line=commission.line,
            item_id=commission.item_id,
            quantity=commission.quantity,
            tier=commission.tier,
            lucky=commission.lucky,
            reward_maple_flame=commission.reward_maple_flame,
            owner_reward=commissions.owner_share(commission.reward_maple_flame),
            taker_reward=commissions.taker_share(commission.reward_maple_flame),
            forwarded_at=commission.forwarded_at,
        )

    def _revert_forward(self, player: PlayerState, commission_id: str) -> dict:
        commission = player.commission
        if commission and commission.commission_id == commission_id and commission.status == "forwarded":
            commission.status = "open"
            commission.forwarded_at = 0
        return {"commission_id": commission_id}

    def _apply_commission_payouts(self, player_id: str) -> None:
        """别人替我交掉之后回给我的枫火。必须在玩家事务之外取出，否则 WATCH 重试会把它吞掉。"""
        board = self.commission_board
        if board is None:
            return
        payouts = board.drain_payouts(player_id)
        if not payouts:
            return
        try:
            self.repository.update(player_id, lambda player: self._credit_payouts(player, payouts))
        except Exception:
            board.restore_payouts(player_id, payouts)
            raise

    def _credit_payouts(self, player: PlayerState, payouts: list[CommissionPayout]) -> None:
        for payout in payouts:
            player.maple_flame += payout.maple_flame
            commission = player.commission
            if commission and commission.commission_id == payout.commission_id:
                commission.status = "forward_completed"
                commission.completed_at = payout.completed_at
                commission.completed_by_name = payout.taker_name

    def _settle_commission(self, player: PlayerState, now: int) -> None:
        """换日就重掷。掷出的内容当场冻结，玩家中途升级不会改掉今天的委托。"""
        if player.level < self.content.commissions.min_level:
            return
        day = self._commission_day(now)
        if player.commission is not None and player.commission.day == day:
            return
        player.commission = self._roll_commission(player, day)
        stale = [index for index, entry in enumerate(player.commission_takes) if entry.day < day]
        for index in reversed(stale):
            del player.commission_takes[index]

    def _unlocked_commission_items(self, player: PlayerState) -> set[str]:
        """能被要求的只有玩家已经能自己产出的东西，条件直接取自现有内容配置。"""
        unlocked: set[str] = set()
        for crop in self.content.crops:
            if crop.min_level <= player.level:
                unlocked.add(crop.produce_item_id)
        gathering_sites = self.content.gathering_site_map
        for task in self.content.gathering_tasks:
            site = gathering_sites.get(task.site_id)
            if site and task.min_level <= player.level and site.min_level <= player.level:
                unlocked.update(output.item_id for output in task.outputs)
        mining_sites = self.content.mining_site_map
        for task in self.content.mining_tasks:
            site = mining_sites.get(task.site_id)
            if site and task.min_level <= player.level and site.min_level <= player.level:
                unlocked.add(task.produce_item_id)
        stations = self.content.crafting_station_map
        for recipe in self.content.recipes:
            station = stations.get(recipe.station_id)
            if not station or station.min_level > player.level:
                continue
            if evaluate_recipe_unlock(player, recipe.unlock_condition.hook, recipe.unlock_condition.params):
                unlocked.add(recipe.produce_item_id)
        return {item_id for item_id in unlocked if not self.content.item_map[item_id].beta}

    def _roll_commission(self, player: PlayerState, day: str) -> CommissionState | None:
        commissions = self.content.commissions
        entry_map = commissions.entry_map
        candidates = [
            entry_map[item_id]
            for item_id in sorted(self._unlocked_commission_items(player))
            if item_id in entry_map
        ]
        if not candidates:
            return None
        lucky = is_lucky_day(player.player_id, day)
        tiers = commissions.tier_map
        available = sorted({entry.tier for entry in candidates})
        weights = [tiers[tier].lucky_weight if lucky else tiers[tier].weight for tier in available]
        if not any(weights):
            # 幸运日撞上低等级玩家时高难度档可能一个都没解锁，退回手上最难的一档。
            weights = [1 if tier == available[-1] else 0 for tier in available]
        rng = random.Random(commission_seed(player.player_id, day))
        tier = rng.choices(available, weights=weights, k=1)[0]
        item_id = rng.choice(sorted(entry.item_id for entry in candidates if entry.tier == tier))
        entry = entry_map[item_id]
        quantity_min, quantity_max = entry.quantity_range(tiers[tier])
        quantity = rng.randint(quantity_min, quantity_max)
        npc = commissions.npcs[rng.randrange(len(commissions.npcs))]
        item = self.content.item_map[item_id]
        return CommissionState(
            day=day,
            commission_id=commission_identifier(player.player_id, day),
            npc_id=npc.id,
            npc_name=npc.name,
            npc_title=npc.title,
            line=npc.render(item.name, quantity),
            item_id=item_id,
            quantity=quantity,
            tier=tier,
            lucky=lucky,
            reward_maple_flame=commissions.reward_for(lucky),
        )

    def _commission_snapshot(self, player: PlayerState, now: int) -> dict:
        commissions = self.content.commissions
        day = self._commission_day(now)
        items = self.content.item_map
        commission = player.commission if player.commission and player.commission.day == day else None
        tiers = commissions.tier_map
        payload = None
        if commission:
            owned = self._owned_quantity(player, commission.item_id)
            item = items.get(commission.item_id)
            tier = tiers.get(commission.tier)
            payload = {
                **commission.model_dump(),
                "item": item.model_dump() if item else None,
                "tier_name": tier.name if tier else "",
                "owned": owned,
                "settled": commission.settled,
                "can_submit": commission.status == "open" and owned >= commission.quantity,
                "can_forward": commission.status == "open",
                "can_withdraw": commission.status == "forwarded",
                "owner_reward": commissions.owner_share(commission.reward_maple_flame),
                "taker_reward": commissions.taker_share(commission.reward_maple_flame),
            }
        return {
            "day": day,
            "unlocked": player.level >= commissions.min_level,
            "min_level": commissions.min_level,
            "board_available": self.commission_board is not None,
            "refresh_at": self._commission_refresh_at(day),
            "lucky_weekday": lucky_weekday(player.player_id),
            "lucky_today": is_lucky_day(player.player_id, day),
            "reward_maple_flame": commissions.reward_maple_flame,
            "lucky_reward_maple_flame": commissions.lucky_reward_maple_flame,
            "daily_take_limit": commissions.daily_take_limit,
            "remaining_takes": self._remaining_takes(player, day),
            "takes": [entry.model_dump() for entry in player.commission_takes if entry.day == day],
            "commission": payload,
        }

    # -------------------------------------------------------------------- 镇邮局

    def mailbox(self, oauth_sub: str) -> dict:
        """收件箱全文。顺手清掉指向已删除或已过期信件的收据，存档不会越攒越厚。"""
        now = self._now()
        player = self._require_player(oauth_sub)
        letters = self._delivered_mail(player, now)
        player = self._prune_mail_receipts(player, letters)
        receipts = {entry.mail_id: entry for entry in player.mail_receipts}
        partners = self.partner_catalog_loader()
        return {
            "available": self.mailbox_repository is not None,
            "entries": [
                self._mail_snapshot(mail, receipts.get(mail.mail_id), partners)
                for mail in letters[:MAIL_LIST_LIMIT]
            ],
            **self._mail_summary(player, now, letters),
        }

    def read_mail(self, oauth_sub: str, mail_id: str) -> dict:
        now = self._now()
        player = self._require_player(oauth_sub)
        mail = self._require_mail(player, mail_id, now)

        def mutation(state: PlayerState):
            self._settle(state, now)
            receipt = self._mail_receipt(state, mail.mail_id)
            receipt.read_at = receipt.read_at or now
            return {"mail_id": mail.mail_id, "read_at": receipt.read_at}

        state, result = self.repository.update(player.player_id, mutation)
        return {"result": result, "state": self._snapshot(state, now)}

    def claim_mail(self, oauth_sub: str, mail_id: str) -> dict:
        """整封一次领完。已领标记和发放写在同一次事务里，重复点击不会领两份。"""
        now = self._now()
        player = self._require_player(oauth_sub)
        mail = self._require_mail(player, mail_id, now)
        if mail.attachments.empty:
            raise GameError("mail_without_attachment", "这封信没有附件", 409)

        def mutation(state: PlayerState):
            self._settle(state, now)
            receipt = self._mail_receipt(state, mail.mail_id)
            if receipt.claimed_at:
                raise GameError("mail_already_claimed", "这封信的附件已经领过了", 409)
            receipt.claimed_at = now
            receipt.read_at = receipt.read_at or now
            return {
                "mail_id": mail.mail_id,
                "title": mail.title,
                "granted": self._grant_reward(state, mail.attachments, now),
            }

        state, result = self.repository.update(player.player_id, mutation)
        return {"result": result, "state": self._snapshot(state, now)}

    def admin_send_mail(self, payload: dict) -> dict:
        """写一封信投进公共信箱。全服信只投给截止时刻之前注册的人，个人信指定收件人。"""
        now = self._now()
        store = self._mail_store()
        scope = str(payload.get("scope") or "global").strip()
        if scope not in ("global", "player"):
            raise GameError("invalid_mail_scope", "邮件类型只能是全服或个人")
        recipient = None
        registered_before = 0
        if scope == "player":
            recipient = self.repository.get(str(payload.get("recipient_id") or "").strip())
            if not recipient:
                raise GameError("player_not_found", "收件人不存在", 404)
        else:
            registered_before = int(payload.get("registered_before") or 0) or now
        try:
            attachments = RewardDefinition.model_validate(payload.get("attachments") or {})
            validate_reward_references(attachments, self.content, self.partner_catalog_loader(), "邮件附件")
            mail = MailMessage(
                mail_id=secrets.token_hex(8),
                scope=scope,
                recipient_id=recipient.player_id if recipient else "",
                registered_before=registered_before,
                title=str(payload.get("title") or "").strip(),
                sender=str(payload.get("sender") or "").strip(),
                body=str(payload.get("body") or "").strip(),
                attachments=attachments,
                created_at=now,
                expires_at=int(payload.get("expires_at") or 0),
            )
        except ValueError as exc:
            raise GameError("invalid_mail", str(exc)) from exc
        store.publish(mail)
        return {"mail": self._mail_admin_snapshot(mail, recipient)}

    def admin_list_mail(self, scope: str = "global", player_id: str = "") -> dict:
        """后台信箱视图。顺带把物品和伙伴清单带上，写信页面挑附件时不用再请求一次。"""
        store = self._mail_store()
        if scope == "player":
            recipient = self.repository.get(str(player_id or "").strip())
            if not recipient:
                raise GameError("player_not_found", "收件人不存在", 404)
            letters = store.list_for_player(recipient.player_id)
            entries = [self._mail_admin_snapshot(mail, recipient) for mail in letters]
        else:
            scope = "global"
            entries = [self._mail_admin_snapshot(mail) for mail in store.list_global()]
        return {
            "scope": scope,
            "entries": entries,
            "items": [item.model_dump() for item in self.content.items],
            "partners": [
                {"partner_id": partner.id, "name": partner.name, "rarity": partner.rarity}
                for partner in self.partner_catalog_loader().partners
            ],
        }

    def admin_delete_mail(self, mail_id: str, recipient_id: str = "") -> dict:
        store = self._mail_store()
        mail = store.get(mail_id, recipient_id)
        if not mail or not store.delete(mail_id, recipient_id):
            raise GameError("mail_not_found", "这封信不存在", 404)
        return {"mail_id": mail_id, "title": mail.title}

    def _mail_store(self) -> MailRepository:
        if self.mailbox_repository is None:
            raise GameError("mailbox_unavailable", "镇邮局暂时不可用", 503)
        return self.mailbox_repository

    def _delivered_mail(self, player: PlayerState, now: int) -> list[MailMessage]:
        store = self.mailbox_repository
        if store is None:
            return []
        letters = [
            mail
            for mail in (*store.list_global(), *store.list_for_player(player.player_id))
            if mail.deliverable_to(player, now)
        ]
        letters.sort(key=lambda mail: (-mail.created_at, mail.mail_id))
        return letters

    def _require_mail(self, player: PlayerState, mail_id: str, now: int) -> MailMessage:
        store = self._mail_store()
        mail = store.get(mail_id, player.player_id) or store.get(mail_id)
        if mail is None or not mail.deliverable_to(player, now):
            raise GameError("mail_not_found", "这封信不在你的信箱里", 404)
        return mail

    @staticmethod
    def _mail_receipt(player: PlayerState, mail_id: str) -> MailReceiptState:
        receipt = next((entry for entry in player.mail_receipts if entry.mail_id == mail_id), None)
        if receipt is None:
            receipt = MailReceiptState(mail_id=mail_id)
            player.mail_receipts.append(receipt)
        return receipt

    def _prune_mail_receipts(self, player: PlayerState, letters: list[MailMessage]) -> PlayerState:
        if self.mailbox_repository is None:
            return player
        live = {mail.mail_id for mail in letters}
        if all(entry.mail_id in live for entry in player.mail_receipts):
            return player

        def mutation(state: PlayerState):
            state.mail_receipts = [entry for entry in state.mail_receipts if entry.mail_id in live]

        refreshed, _ = self.repository.update(player.player_id, mutation)
        return refreshed

    def _mail_summary(self, player: PlayerState, now: int, letters: list[MailMessage] | None = None) -> dict:
        letters = self._delivered_mail(player, now) if letters is None else letters
        receipts = {entry.mail_id: entry for entry in player.mail_receipts}
        unread = sum(1 for mail in letters if not receipts.get(mail.mail_id, _UNSEEN).read_at)
        unclaimed = sum(
            1
            for mail in letters
            if not mail.attachments.empty and not receipts.get(mail.mail_id, _UNSEEN).claimed_at
        )
        return {"total": len(letters), "unread": unread, "unclaimed": unclaimed}

    def _mail_snapshot(self, mail: MailMessage, receipt: MailReceiptState | None, partners: PartnerCatalog) -> dict:
        claimed = bool(receipt and receipt.claimed_at)
        return {
            "mail_id": mail.mail_id,
            "scope": mail.scope,
            "title": mail.title,
            "sender": mail.sender,
            "body": mail.body,
            "created_at": mail.created_at,
            "expires_at": mail.expires_at or None,
            "attachments": serialize_reward(mail.attachments, self.content, partners),
            "read": bool(receipt and receipt.read_at),
            "claimed": claimed,
            "claimable": not mail.attachments.empty and not claimed,
        }

    def _mail_admin_snapshot(self, mail: MailMessage, recipient: PlayerState | None = None) -> dict:
        return {
            **mail.model_dump(exclude={"attachments"}),
            "attachments": serialize_reward(mail.attachments, self.content, self.partner_catalog_loader()),
            "recipient_name": recipient.display_name if recipient else "",
        }

    # -------------------------------------------------------------------- 联动活动

    def crossover_campaigns(self, oauth_sub: str) -> dict:
        """列出全部联动活动，带上这个账号的达成情况和领取情况。

        标了 ``hide_after_claim`` 的活动一旦领过就不再出现——一次性的联动礼物领完即撤。
        没有可展示的联动活动时返回空列表，前端据此整块隐藏入口。
        """
        player = self._require_player(oauth_sub)
        partners = self.partner_catalog_loader()
        return {
            "campaigns": [
                self._crossover_snapshot(campaign, player, oauth_sub, partners)
                for campaign in list_crossover_campaigns()
                if not (campaign.hide_after_claim and player.crossover_claims.get(campaign.campaign_id))
            ],
        }

    def claim_crossover(self, oauth_sub: str, campaign_id: str) -> dict:
        """领取联动奖励。一个 OAuth 账号每个活动只能领一次。

        达成条件在事务外判定（要去问联动那一方），但「已领取」标记和发奖写在同一次
        ``repository.update`` 里，所以重复点击、并发请求都只会发出一份。
        """
        campaign = get_crossover_campaign(campaign_id)
        if campaign is None:
            raise GameError("crossover_not_found", "这个联动活动不存在", 404)
        now = self._now()
        player = self._require_player(oauth_sub)
        if player.crossover_claims.get(campaign.campaign_id):
            raise GameError("crossover_already_claimed", "这份联动奖励已经领过了", 409)
        if not campaign.is_eligible(oauth_sub):
            raise GameError("crossover_locked", campaign.locked_hint or "还没有达成领取条件", 409)

        def mutation(state: PlayerState):
            self._settle(state, now)
            if state.crossover_claims.get(campaign.campaign_id):
                raise GameError("crossover_already_claimed", "这份联动奖励已经领过了", 409)
            state.crossover_claims[campaign.campaign_id] = now
            return {
                "campaign_id": campaign.campaign_id,
                "title": campaign.title,
                "granted": self._grant_reward(state, campaign.reward, now),
            }

        state, result = self.repository.update(player.player_id, mutation)
        return {"result": result, "state": self._snapshot(state, now)}

    def crossover_claim_record(self, oauth_sub: str, campaign_id: str) -> dict:
        """给联动那一方查的：这个账号在红叶镇有没有存档、这个活动领没领过。

        和 :meth:`crossover_campaigns` 不同，这里**不要求**存档存在——联动方需要能对
        「还没来过红叶镇」的玩家给出引导，而不是吃一个 404。
        """
        player = self.repository.get_by_sub(oauth_sub)
        if player is None:
            return {"registered": False, "claimed_at": 0, "display_name": ""}
        return {
            "registered": True,
            "claimed_at": int(player.crossover_claims.get(str(campaign_id or ""), 0)),
            "display_name": player.display_name,
        }

    def _crossover_snapshot(
        self,
        campaign: CrossoverCampaign,
        player: PlayerState,
        oauth_sub: str,
        partners: PartnerCatalog,
    ) -> dict:
        claimed_at = int(player.crossover_claims.get(campaign.campaign_id, 0))
        eligible = bool(claimed_at) or campaign.is_eligible(oauth_sub)
        return {
            "campaign_id": campaign.campaign_id,
            "title": campaign.title,
            "source": campaign.source,
            "description": campaign.description,
            "requirement": campaign.requirement,
            "home_url": campaign.home_url,
            "locked_hint": campaign.locked_hint,
            "reward": serialize_reward(campaign.reward, self.content, partners),
            "eligible": eligible,
            "claimed": bool(claimed_at),
            "claimed_at": claimed_at or None,
            "claimable": eligible and not claimed_at,
            **campaign.extra,
        }

    def story_cue(self, oauth_sub: str, cue: str) -> dict:
        """返回这个信号下应该立即播放的剧情，已经看过的一次性剧本不再返回。"""
        try:
            code = validate_story_cue(cue)
        except ValueError as exc:
            raise GameError("invalid_story_cue", "剧情信号格式不正确") from exc
        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        catalog = self.story_catalog_loader()
        context = StoryContext(player=player, cue=code, now=self._now())
        matched = catalog.matching(context, set(player.seen_story_ids))
        for script in matched:
            if script.id.startswith("summer_chapter_"):
                self._require_summer_story(player, script.id, context.now)
        assets = self.story_asset_loader()
        partners = self.partner_catalog_loader()
        return {
            "cue": code,
            "stories": [serialize_script(script, assets, partners, self.content) for script in matched],
        }

    def mark_story_seen(self, oauth_sub: str, story_id: str) -> dict:
        script = self.story_catalog_loader().script_map.get(story_id)
        if script is None:
            raise GameError("story_not_found", "剧情不存在", 404)
        now = self._now()

        def mutation(player: PlayerState):
            if script.id.startswith("summer_chapter_"):
                self._require_summer_story(player, script.id, now)
            if script.id in player.seen_story_ids:
                return {"story_id": script.id, "granted": None}
            player.seen_story_ids.append(script.id)
            # 奖励只在第一次播完时结算，重复上报不会再发一次。
            granted = None if script.rewards.empty else self._grant_reward(player, script.rewards, now)
            return {"story_id": script.id, "granted": granted}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player)}

    def story_scripts(self) -> list[dict]:
        assets = self.story_asset_loader()
        partners = self.partner_catalog_loader()
        return [serialize_script(script, assets, partners, self.content) for script in self.story_catalog_loader().scripts]

    def create_binding_code(self, oauth_sub: str) -> str:
        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        return self.repository.create_binding_code(player.player_id)

    def bind_identity(self, code: str, identity: QQIdentity) -> PlayerState:
        player_id = self.repository.consume_binding_code(code.strip().upper(), identity)
        if not player_id:
            raise GameError("binding_code_invalid", "绑定码无效或已过期", 404)
        player = self.repository.get(player_id)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        return player

    def account(self, oauth_sub: str) -> dict:
        state = self.snapshot_by_sub(oauth_sub)
        player = self.repository.get_by_sub(oauth_sub)
        return {
            "player_id": state["player"]["player_id"],
            "display_name": state["player"]["display_name"],
            "bindings": self.repository.binding_labels(player.player_id) if player else [],
            "state": state,
        }

    def claim_achievement(self, oauth_sub: str, achievement_id: str) -> dict:
        achievement_id = str(achievement_id or "").strip()
        definition = self.content.achievement_map.get(achievement_id)
        if definition is None:
            raise GameError("achievement_not_found", "这个成就不存在", 404)
        now = self._now()

        def mutation(player: PlayerState):
            evaluate_achievements(player, self.content, now)
            progress = next(
                (entry for entry in player.achievements if entry.achievement_id == achievement_id),
                None,
            )
            if progress is None:
                raise GameError("achievement_incomplete", "这个成就还没有达成", 409)
            if progress.claimed_at:
                raise GameError("achievement_claimed", "这个成就奖励已经领取", 409)
            progress.claimed_at = now
            player.maple_flame += definition.reward_maple_flame
            return {
                "claimed": [{
                    "achievement_id": definition.id,
                    "name": definition.name,
                    "reward_maple_flame": definition.reward_maple_flame,
                }],
                "maple_flame": definition.reward_maple_flame,
            }

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def claim_all_achievements(self, oauth_sub: str) -> dict:
        now = self._now()

        def mutation(player: PlayerState):
            evaluate_achievements(player, self.content, now)
            progress_map = {entry.achievement_id: entry for entry in player.achievements}
            claimed = []
            total = 0
            for definition in self.content.achievements:
                progress = progress_map.get(definition.id)
                if progress is None or progress.claimed_at:
                    continue
                progress.claimed_at = now
                total += definition.reward_maple_flame
                claimed.append({
                    "achievement_id": definition.id,
                    "name": definition.name,
                    "reward_maple_flame": definition.reward_maple_flame,
                })
            player.maple_flame += total
            return {"claimed": claimed, "maple_flame": total}

        player, result = self._update_by_sub(oauth_sub, mutation)
        return {"result": result, "state": self._snapshot(player, now)}

    def admin_search_players(self, query: str = "", limit: int = 50) -> list[dict]:
        bounded_limit = max(1, min(int(limit), 100))
        return [self._admin_player_summary(player) for player in self.repository.search(query, bounded_limit)]

    def admin_grant_partner(self, player_id: str, partner_id: str) -> dict:
        partner = self.partner_catalog_loader().partner_map.get(partner_id)
        if partner is None:
            raise GameError("partner_not_found", "伙伴不存在", 404)
        now = self._now()

        def mutation(player: PlayerState):
            if any(owned.partner_id == partner_id for owned in player.owned_partners):
                raise GameError("partner_already_owned", "玩家已经持有这个伙伴", 409)
            owned = OwnedPartnerState(partner_id=partner_id, stars=partner.rarity, acquired_at=now)
            player.owned_partners.append(owned)
            return owned

        try:
            player, owned = self.repository.update(player_id, mutation)
        except KeyError as exc:
            raise GameError("player_not_found", "玩家不存在", 404) from exc
        return {
            "owned_partner": owned.model_dump(),
            "player": self._admin_player_summary(player),
        }

    def admin_grant_resources(self, player_id: str, coins: int, experience: int, maple_flame: int) -> dict:
        if type(coins) is not int or type(experience) is not int or type(maple_flame) is not int:
            raise GameError("invalid_grant_amount", "发放数量必须是整数")
        if min(coins, experience, maple_flame) < 0 or (coins == 0 and experience == 0 and maple_flame == 0):
            raise GameError("invalid_grant_amount", "请填写要发放的正数资源")

        def mutation(player: PlayerState):
            if coins:
                grant_coins(player, coins)
            if maple_flame:
                player.maple_flame += maple_flame
            unlocked_levels = grant_experience(player, experience, self.content, max_level=self._player_level_cap(player)) if experience else []
            return unlocked_levels

        try:
            player, unlocked_levels = self.repository.update(player_id, mutation)
        except KeyError as exc:
            raise GameError("player_not_found", "玩家不存在", 404) from exc
        return {
            "coins": coins,
            "experience": experience,
            "maple_flame": maple_flame,
            "unlocked_levels": unlocked_levels,
            "player": self._admin_player_summary(player),
        }

    def admin_delete_player(self, player_id: str) -> dict:
        """彻底删除一个角色。存档不可恢复，玩家再次登录会当作新居民重新建号。"""
        player = self.repository.get(player_id)
        if not player:
            raise GameError("player_not_found", "玩家不存在", 404)
        summary = self._admin_player_summary(player)
        if self.mailbox_repository is not None:
            for mail in self.mailbox_repository.list_for_player(player_id):
                self.mailbox_repository.delete(mail.mail_id, player_id)
        if not self.repository.delete(player_id):
            raise GameError("player_not_found", "玩家不存在", 404)
        return {"player": summary}

    def _settled_snapshot(self, player_id: str) -> dict:
        now = self._now()
        self._apply_commission_payouts(player_id)

        def mutation(player: PlayerState):
            self._settle(player, now)

        player, _ = self._update_player(player_id, mutation, now)
        return self._snapshot(player, now)

    def _update_player(self, player_id: str, mutation, now: int | None = None):
        completed_at = self._now() if now is None else now

        def wrapped(player: PlayerState):
            reconcile_legacy_auto_rewards(player, self.content)
            result = mutation(player)
            achievements = evaluate_achievements(player, self.content, completed_at)
            if achievements and isinstance(result, dict):
                result = {**result, "achievements": achievements}
            return result

        return self.repository.update(player_id, wrapped)

    def _update_by_sub(self, oauth_sub: str, mutation):
        player = self.repository.get_by_sub(oauth_sub)
        if not player:
            raise GameError("player_not_found", "角色不存在", 404)
        return self._update_player(player.player_id, mutation)

    def _roll_gacha_rarity(self, gacha: GachaDefinition, progress: GachaPoolProgressState) -> int | None:
        probabilities = gacha.rarity_probabilities
        if progress.total_pulls < gacha.first_pulls_without_items:
            total = sum(probabilities.values())
            draw = self.rng.random() * total
            for rarity in (5, 4, 3):
                draw -= probabilities[rarity]
                if draw < 0:
                    return rarity
            return 3
        draw = self.rng.random()
        for rarity in (5, 4, 3):
            draw -= probabilities[rarity]
            if draw < 0:
                return rarity
        return None

    @staticmethod
    def _pool_partner_candidates(gacha: GachaDefinition, catalog: PartnerCatalog) -> dict[int, list[PartnerDefinition]]:
        """招募池候选：显式名单可纳入限定伙伴，空名单只使用常驻伙伴。"""
        allowed = set(gacha.partner_ids)
        return {
            rarity: [
                entry
                for entry in catalog.partners
                if entry.rarity == rarity
                and entry.recruitable
                and (entry.id in allowed if allowed else entry.standard_recruitable)
            ]
            for rarity in (3, 4, 5)
        }

    @staticmethod
    def _pool_is_open(candidates: dict[int, list[PartnerDefinition]]) -> bool:
        """保底会强制抽出 4★ 和 5★，所以三个星级都得有人，池子才算能开。"""
        return all(candidates[rarity] for rarity in (3, 4, 5))

    @staticmethod
    def _gacha_featured_guaranteed(player: PlayerState, gacha: GachaDefinition) -> bool:
        """按本池最后一个五星判断大保底，兼容修复前的抽卡历史。"""
        if gacha.featured_partner_id:
            for record in reversed(player.gacha_history):
                if record.pool_id == gacha.pool_id:
                    for drop in reversed(record.results):
                        if drop.kind == "partner" and drop.rarity == 5:
                            return drop.content_id != gacha.featured_partner_id
        return False

    def _pick_gacha_partner(
        self, gacha: GachaDefinition, candidates: list[PartnerDefinition], rarity: int,
        featured_guaranteed: bool = False,
    ) -> PartnerDefinition:
        """五星歪后下一次五星必出 UP，否则按 featured_rate 判定；四星 UP 只按 featured_four_star_rate 判定。"""
        if rarity == 4 and gacha.featured_four_star_partner_id:
            featured = next((entry for entry in candidates if entry.id == gacha.featured_four_star_partner_id), None)
            if featured is not None:
                if self.rng.random() < gacha.featured_four_star_rate:
                    return featured
                others = [entry for entry in candidates if entry.id != gacha.featured_four_star_partner_id]
                if others:
                    return others[self.rng.randrange(len(others))]
                return featured
        if rarity == 5 and gacha.featured_partner_id:
            featured = next((entry for entry in candidates if entry.id == gacha.featured_partner_id), None)
            if featured is not None:
                if featured_guaranteed or self.rng.random() < gacha.featured_rate:
                    return featured
                others = [entry for entry in candidates if entry.id != gacha.featured_partner_id]
                if others:
                    return others[self.rng.randrange(len(others))]
                return featured
        return candidates[self.rng.randrange(len(candidates))]

    def _roll_gacha_item(self, gacha: GachaDefinition, player: PlayerState) -> GachaDropRecord:
        drops = gacha.item_drops
        total = sum(entry.weight for entry in drops)
        draw = self.rng.random() * total
        selected = drops[-1]
        for entry in drops:
            draw -= entry.weight
            if draw < 0:
                selected = entry
                break
        player.task_items[selected.task_item_id] = (
            player.task_items.get(selected.task_item_id, 0) + selected.quantity
        )
        return GachaDropRecord(
            kind="task_item",
            content_id=selected.task_item_id,
            quantity=selected.quantity,
        )

    @staticmethod
    def _owned_partner(player: PlayerState, partner_id: str) -> OwnedPartnerState:
        owned = next((entry for entry in player.owned_partners if entry.partner_id == partner_id), None)
        if owned is None:
            raise GameError("partner_not_owned", "你还没有这个伙伴", 404)
        return owned

    def _grant_partner_experience(self, owned: OwnedPartnerState, amount: int) -> dict:
        owned.experience += max(0, int(amount))
        previous_level = owned.level
        level_cap = level_cap_for_breakthrough(owned.breakthrough)
        while owned.level < level_cap:
            cost = self.content.partner_growth.experience_for_next_level(owned.level)
            if owned.experience < cost:
                break
            owned.experience -= cost
            owned.level += 1
        return {
            "experience_gained": max(0, int(amount)),
            "previous_level": previous_level,
            "level": owned.level,
            "level_cap": level_cap,
        }

    def _grant_task_partner_experience(
        self,
        player: PlayerState,
        task: ProductionTaskSnapshot | None,
    ) -> list[dict]:
        if task is None or not task.partner_snapshots:
            return []
        amount = max(
            1,
            floor(task.final_duration / self.content.partner_growth.experience_interval_seconds)
            + task.stamina_cost * self.content.partner_growth.experience_per_stamina,
        )
        records = []
        for snapshot in task.partner_snapshots:
            owned = next(
                (entry for entry in player.owned_partners if entry.partner_id == snapshot.partner_id),
                None,
            )
            if owned:
                records.append({"partner_id": owned.partner_id, **self._grant_partner_experience(owned, amount)})
        return records

    def _consume_minimum_quality_items(
        self,
        player: PlayerState,
        item_id: str,
        quantity: int,
        min_quality: int,
    ) -> list[TaskInputSnapshot]:
        available = sum(
            amount
            for quality, amount in player.inventory.get(item_id, {}).items()
            if quality >= min_quality
        )
        if available < quantity:
            raise EconomyError("材料数量不足")
        remaining = quantity
        consumed = []
        for quality, amount in sorted(player.inventory.get(item_id, {}).items()):
            if quality < min_quality or remaining <= 0:
                continue
            taken = min(amount, remaining)
            remove_item(player, item_id, taken, quality)
            consumed.append(TaskInputSnapshot(item_id=item_id, quality=quality, quantity=taken))
            remaining -= taken
        return consumed

    def _production_slot(self, player: PlayerState, industry: str, slot_id: str):
        if industry == "farming":
            try:
                return self._plot(player, int(slot_id))
            except ValueError as exc:
                raise GameError("production_slot_not_found", "生产位置不存在", 404) from exc
        if industry == "gathering":
            return self._gathering_site(player, slot_id, allow_closed=True)
        if industry == "crafting":
            return self._crafting_station(player, slot_id)
        if industry == "mining":
            return self._mining_site(player, slot_id)
        raise GameError("industry_not_found", "产业不存在", 404)

    def _settle(self, player: PlayerState, now: int) -> None:
        self._normalize_inventory_quality(player)
        partner_map = self.partner_catalog_loader().partner_map
        for owned in player.owned_partners:
            if owned.stars == 0 and owned.partner_id in partner_map:
                owned.stars = partner_map[owned.partner_id].rarity
        level = self.content.level_for_xp(player.experience, max_level=self._player_level_cap(player))
        player.level = level.level
        normalize_plot_slots(player, self.content)
        normalize_gathering_sites(player, self.content)
        normalize_crafting_stations(player, self.content)
        normalize_mining_sites(player, self.content)
        normalize_ponds(player, self.content)
        normalize_livestock(player, self.content, now)
        settle_stamina(player, self.content, now)
        self._settle_aquatic(player, now)
        self._settle_refining(player, now)
        for plot in player.plots:
            if not plot.empty and plot.ready_at <= now and not plot.task_results:
                self._resolve_output(plot, now, self.content.crop_map.get(plot.crop_id))
        for site in player.gathering_sites:
            if site.task_snapshot and site.task_snapshot.ready_at <= now and not site.task_results:
                self._resolve_gathering_outputs(site, now)
        for station in player.crafting_stations:
            while station.task_snapshot and station.task_snapshot.ready_at <= now:
                if not station.task_results:
                    self._resolve_output(station, station.task_snapshot.ready_at)
                if not station.queued_tasks:
                    break
                finished_at = station.task_snapshot.ready_at
                station.completed_tasks.append(CompletedCraftingTask(
                    task_snapshot=station.task_snapshot, task_results=station.task_results,
                ))
                next_task = station.queued_tasks.pop(0)
                # Active acceleration can finish the preceding task ahead of its original schedule.
                next_task.started_at = finished_at
                next_task.ready_at = finished_at + next_task.final_duration
                station.task_snapshot = next_task
                station.task_results = []
        for site in player.mining_sites:
            if site.task_snapshot and site.task_snapshot.ready_at <= now and not site.task_results:
                self._resolve_output(site, now)
        self._settle_commission(player, now)

    def _plot(self, player: PlayerState, slot: int):
        if slot < 0 or slot >= len(player.plots):
            raise GameError("plot_locked", "这块土地尚未解锁")
        if slot % 2 == 1 and slot < player.farm_upgrade_stage * 2:
            raise GameError("plot_merged", "这块田已经合并，请操作对应的双倍田")
        return player.plots[slot]

    def _gathering_site(self, player: PlayerState, site_id: str, *, allow_closed: bool = False) -> GatheringSiteState:
        if not allow_closed:
            self._require_content(player, self.content.gathering_site_map.get(site_id))
        site = next((entry for entry in player.gathering_sites if entry.site_id == site_id), None)
        if site is None:
            raise GameError("gathering_site_locked", "这个采集点尚未解锁", 404)
        return site

    def _crafting_station(self, player: PlayerState, station_id: str) -> CraftingStationState:
        station = next((entry for entry in player.crafting_stations if entry.station_id == station_id), None)
        if station is None:
            raise GameError("crafting_station_locked", "这个加工工位尚未解锁", 404)
        return station

    def _mining_site(self, player: PlayerState, site_id: str) -> MiningSiteState:
        site = next((entry for entry in player.mining_sites if entry.site_id == site_id), None)
        if site is None:
            raise GameError("mining_site_locked", "这个矿点尚未解锁", 404)
        return site

    def _build_farming_task_snapshot(
        self,
        player,
        plot,
        crop,
        now: int,
        task_item_id: str = "",
    ) -> ProductionTaskSnapshot:
        if self._industry_assigned_count(player, "farming") > self._industry_partner_capacity(player, "farming"):
            raise GameError("partner_capacity_reached", "当前农作伙伴编制已满", 409)
        return self._build_production_task_snapshot(
            player=player,
            assigned_partner_ids=plot.assigned_partner_ids,
            industry="farming",
            content_id=crop.id,
            production_slot_id=f"farm:plot:{plot.slot}",
            now=now,
            base_duration=crop.growth_seconds,
            minimum_duration=crop.minimum_duration_seconds,
            time_difficulty=crop.time_difficulty,
            produce_item_id=crop.produce_item_id,
            yield_min=crop.yield_min,
            yield_max=crop.yield_max,
            harvest_xp=crop.plant_xp + crop.harvest_xp,
            stamina_cost=crop.stamina_cost,
            quality=crop.quality,
            consumed_inputs=[TaskInputSnapshot(item_id=crop.seed_item_id, quality=0, quantity=1)],
            task_item_id=task_item_id,
        )

    def _production_ability(
        self,
        player: PlayerState,
        assigned_partner_ids: list[str],
        industry: str,
    ) -> tuple[int, int, list[TaskPartnerSnapshot]]:
        rules = self.content.industries[industry]
        catalog = self.partner_catalog_loader()
        owned_map = {entry.partner_id: entry for entry in player.owned_partners}
        partner_snapshots: list[TaskPartnerSnapshot] = []
        for partner_id in assigned_partner_ids:
            owned = owned_map.get(partner_id)
            definition = catalog.partner_map.get(partner_id)
            if owned is None or definition is None:
                raise GameError("partner_assignment_invalid", "驻场伙伴数据无效，请重新安排", 409)
            if not any(entry.industry == industry for entry in definition.tendencies):
                raise GameError("partner_tendency_mismatch", "驻场伙伴没有对应产业倾向，请重新安排", 409)
            effective_level = min(
                owned.level,
                rules.partner_level_cap,
                level_cap_for_breakthrough(owned.breakthrough),
            )
            partner_snapshots.append(TaskPartnerSnapshot(
                partner_id=partner_id,
                level=owned.level,
                effective_level=effective_level,
                breakthrough=owned.breakthrough,
                ability=definition.ability_at(industry, effective_level, owned.stars),
            ))
        character_ability = rules.character_base_ability + self._industry_ability_bonus(player, industry)
        return character_ability + sum(entry.ability for entry in partner_snapshots), character_ability, partner_snapshots

    def _build_production_task_snapshot(
        self,
        *,
        player: PlayerState,
        assigned_partner_ids: list[str],
        industry: str,
        content_id: str,
        production_slot_id: str,
        now: int,
        base_duration: int,
        time_difficulty: int | None,
        produce_item_id: str,
        yield_min: int,
        yield_max: int,
        harvest_xp: int,
        stamina_cost: int,
        quality,
        minimum_duration: int = 1,
        consumed_inputs: list[TaskInputSnapshot] | None = None,
        output_pool: list[TaskOutputSnapshot] | None = None,
        draws: GatheringDrawDefinition | None = None,
        fixed_duration: bool = False,
        yield_efficiency: float = 1,
        task_item_id: str = "",
    ) -> ProductionTaskSnapshot:
        rules = self.content.industries[industry]
        if len(assigned_partner_ids) > rules.collaborator_slots:
            raise GameError("collaborator_slots_exceeded", "这个生产格的伙伴位置超过当前上限", 409)
        total_ability, character_ability, partner_snapshots = self._production_ability(
            player,
            assigned_partner_ids,
            industry,
        )
        task_item = self._consume_start_task_item(player, task_item_id, industry)
        applied_effects = [
            {
                "source_type": "task_item",
                "task_item_id": task_item.id,
                "name": task_item.name,
                "effect": task_item.effect,
                "value": task_item.value,
            }
        ] if task_item else []
        world = self._world_snapshot(now)
        content_tags = (
            list(self.content.item_map[produce_item_id].tags)
            if produce_item_id in self.content.item_map
            else []
        )
        if industry == "crafting" and content_id in self.content.recipe_map:
            content_tags = list(self.content.recipe_map[content_id].tags)
        trait_context = {
            "industry": industry,
            "content_id": content_id,
            "production_slot_id": production_slot_id,
            "world": world,
            "content_tags": content_tags,
            "input_item_tags": {
                entry.item_id: list(self.content.item_map[entry.item_id].tags)
                for entry in (consumed_inputs or [])
                if entry.item_id in self.content.item_map
            },
            "output_items": [
                {
                    "item_id": entry.item_id,
                    "tags": list(self.content.item_map[entry.item_id].tags),
                }
                for entry in (output_pool or [])
                if entry.item_id in self.content.item_map
            ],
            "duration_multiplier": 1.0,
            "quality_ability_bonus": 0.0,
            "quality_ability_multiplier": 1.0,
            "yield_multiplier": 1.0,
            "yield_bonus": 0,
            "draw_bonus": 0,
            "applied_effects": [],
        }
        if industry == "crafting":
            trait_context["duration_multiplier"] *= max(0.01, 1 - self._talent_modifier(player, "crafting_duration_reduction"))
            trait_context["quality_ability_bonus"] += self._talent_modifier(player, "crafting_quality_bonus")
            refund_chance = self._talent_modifier(player, "crafting_stamina_refund_chance")
            if refund_chance and stamina_cost:
                applied_effects.append({"source_type": "talent", "effect": "crafting_stamina_refund_chance", "value": min(1, refund_chance)})
        for phase in ("task_prepare", "output_draw", "quality_roll", "result_finalize"):
            self._execute_partner_trait_phase(assigned_partner_ids, phase, trait_context)
        applied_effects.extend(trait_context["applied_effects"])
        time_efficiency = 1 if fixed_duration else 1 + 2 * total_ability / (total_ability + int(time_difficulty))
        duration_multiplier = (
            (task_item.value if task_item and task_item.effect == "duration_multiplier" else 1)
            * max(0.01, float(trait_context["duration_multiplier"]))
        )
        final_duration = max(minimum_duration, ceil(base_duration / time_efficiency))
        if duration_multiplier != 1:
            # 缩时道具与伙伴特性压在最小时长之后结算，踩到时长下限也能吃到折扣。
            final_duration = max(1, min(base_duration, ceil(final_duration * duration_multiplier)))
            minimum_duration = min(minimum_duration, final_duration)
        quality_ability = round(
            total_ability * max(0.0, float(trait_context["quality_ability_multiplier"]))
            + float(trait_context["quality_ability_bonus"])
            + (task_item.value if task_item and task_item.effect == "quality_boost" else 0)
        )
        miracle_unlocked = bool(task_item and task_item.effect == "unlock_miracle")
        miracle_width_multiplier = task_item.value if miracle_unlocked else 1
        probabilities = quality_probabilities(
            quality_ability,
            quality.thresholds,
            quality.width,
            quality.miracle_probability_cap,
            quality.miracle_eligible or miracle_unlocked,
            miracle_width_multiplier=miracle_width_multiplier,
            ignore_miracle_cap=miracle_unlocked,
        )
        trait_yield_multiplier = max(0.0, float(trait_context["yield_multiplier"]))
        effective_yield_efficiency = max(1.0, yield_efficiency * trait_yield_multiplier)
        effective_yield_min = max(1, floor(yield_min * effective_yield_efficiency))
        effective_yield_max = max(effective_yield_min, floor(yield_max * effective_yield_efficiency))
        extra_yield = (
            (round(task_item.value) if task_item and task_item.effect == "yield_bonus" else 0)
            + int(trait_context["yield_bonus"])
        )
        effective_draw_count = draw_count(
            total_ability,
            draws.base_draws,
            draws.ability_bonus,
            draws.difficulty,
        ) if draws else 0
        effective_draw_count += int(trait_context["draw_bonus"])
        if extra_yield:
            if draws:
                effective_draw_count += extra_yield
            else:
                effective_yield_min += extra_yield
                effective_yield_max += extra_yield
        effective_draw_count = max(0, min(60, effective_draw_count))
        return ProductionTaskSnapshot(
            industry=industry,
            content_id=content_id,
            production_slot_id=production_slot_id,
            world_day=world["day"],
            weather_id=world["weather"]["id"],
            started_at=now,
            ready_at=now + final_duration,
            assigned_partner_ids=list(assigned_partner_ids),
            support_partner_ids=[],
            partner_snapshots=partner_snapshots,
            applied_effects=applied_effects,
            character_ability=character_ability,
            total_ability=total_ability,
            time_efficiency=time_efficiency,
            yield_efficiency=effective_yield_efficiency,
            base_duration=base_duration,
            minimum_duration=minimum_duration,
            final_duration=final_duration,
            stamina_cost=stamina_cost,
            produce_item_id=produce_item_id,
            yield_min=effective_yield_min,
            yield_max=effective_yield_max,
            harvest_xp=harvest_xp,
            consumed_inputs=consumed_inputs or [],
            output_pool=output_pool or [],
            draw_count=effective_draw_count,
            quality_parameters=TaskQualitySnapshot(
                ability=quality_ability,
                thresholds=quality.thresholds,
                width=quality.width,
                miracle_probability_cap=quality.miracle_probability_cap,
                miracle_eligible=quality.miracle_eligible or miracle_unlocked,
                miracle_width_multiplier=miracle_width_multiplier,
                miracle_cap_ignored=miracle_unlocked,
                probabilities=probabilities,
            ),
        )

    def _consume_start_task_item(self, player: PlayerState, task_item_id: str, industry: str):
        task_item_id = str(task_item_id or "").strip()
        if not task_item_id:
            return None
        definition = self.content.task_item_map.get(task_item_id)
        if definition is None or definition.timing != "start":
            raise GameError("task_item_invalid", "这个道具不能在开工时使用")
        if definition.eligible_industries and industry not in definition.eligible_industries:
            raise GameError("task_item_industry_mismatch", "这个道具不能用于当前产业")
        if player.task_items.get(task_item_id, 0) < 1:
            raise GameError("task_item_insufficient", "这个特殊道具数量不足")
        player.task_items[task_item_id] -= 1
        if player.task_items[task_item_id] <= 0:
            player.task_items.pop(task_item_id, None)
        return definition

    @staticmethod
    def _partner_lock_deadlines(player: PlayerState, now: int) -> dict[str, int]:
        deadlines: dict[str, int] = {}
        if player.refining.occupied:
            deadlines.update({p: 2**53 - 1 for p in player.refining.assigned_partner_ids})
        voyage = player.sailing.active_run
        if voyage and voyage.ready_at > now:
            deadlines.update({partner_id: voyage.ready_at for partner_id in voyage.partner_ids})
        for production_slot in [*player.plots, *player.gathering_sites, *player.crafting_stations, *player.mining_sites]:
            task = production_slot.task_snapshot
            if task is None or task.ready_at <= now:
                continue
            deadline = task.ready_at
            for index, queued in enumerate([task, *getattr(production_slot, "queued_tasks", [])]):
                if index:
                    deadline += queued.final_duration
                if GameService._task_releases_partner(queued):
                    continue
                for partner_id in {*queued.assigned_partner_ids, *queued.support_partner_ids}:
                    deadlines[partner_id] = max(deadlines.get(partner_id, 0), deadline)
        return deadlines

    @staticmethod
    def _task_releases_partner(task: ProductionTaskSnapshot | None) -> bool:
        return bool(task and any(effect.get("effect") == "release_partner" for effect in task.applied_effects))

    @staticmethod
    def _beta_player_ids() -> set[str]:
        """内测白名单：逗号分隔的 player_id。环境变量优先，其次读宿主 .env 里的同名配置。"""

        raw = os.environ.get("RED_LEAF_TOWN_BETA_PLAYERS", "")
        if not raw:
            try:
                from nonebot import get_driver

                raw = str(getattr(get_driver().config, "red_leaf_town_beta_players", "") or "")
            except Exception:
                raw = ""
        return {entry.strip() for entry in raw.split(",") if entry.strip()}

    def _is_beta_player(self, player: PlayerState) -> bool:
        return player.player_id in self._beta_player_ids()

    def _season(self, definition):
        season_id = getattr(definition, "season_id", "")
        if not season_id:
            return None
        try:
            return self.season_loader().get(season_id)
        except (OSError, ValueError):
            # Invalid schedules must close event content, without blocking ordinary gameplay.
            return None

    def _season_open(self, definition, now: int) -> bool:
        if not getattr(definition, "season_id", ""):
            return True
        season = self._season(definition)
        return season is not None and season.is_open(now)

    def _season_bonus(self, definition, kind: str, partner_ids: list[str], now: int):
        season = self._season(definition)
        return season.bonus_snapshot(kind, partner_ids) if season and season.is_open(now) else None

    def _season_notes(self, definition, kind: str, now: int) -> dict:
        season = self._season(definition)
        if not season or not season.is_open(now):
            return {}
        if kind == "exploration":
            return {"leader_note": season.leader_note}
        prefix = "fishing_spot" if kind == "fishing" else "gathering_site"
        return {"event_badge": getattr(season, prefix + "_badge"),
                "event_note": getattr(season, prefix + "_note")}

    def _content_visible(self, player, definition) -> bool:
        return (definition is not None
                and (not getattr(definition, "beta", False) or self._is_beta_player(player))
                and self._season_open(definition, self._now()))

    def _require_content(self, player, definition) -> None:
        if not self._season_open(definition, self._now()):
            raise GameError("content_locked", "活动尚未开放或已经结束，暂时不能开始新任务", 409)
        if not self._content_visible(player, definition):
            raise GameError("content_locked", "此内容目前仅对内测玩家开放", 403)

    def _player_level_cap(self, player) -> int:
        # 移出白名单不降级、不扣掉已经获得的成长。
        return max(player.level, max(entry.level for entry in self.content.levels if self._content_visible(player, entry)))

    def _species_unlocked(self, player, facility, species) -> bool:
        return (self._content_visible(player, species) and player.level >= species.min_level
                and (not species.required_facility_id or
                     (facility.facility_id == species.required_facility_id and facility.tier >= species.required_facility_tier)))

    def _require_species(self, player, facility, definition, species) -> None:
        if definition.category != species.category or not self._species_unlocked(player, facility, species):
            raise GameError("content_locked", f"{species.name}需要居民 {species.min_level} 级及对应设施 {species.required_facility_tier} 级", 403)

    def _visible_expeditions(self, player: PlayerState) -> list:
        return [entry for entry in self.content.exploration_expeditions if self._content_visible(player, entry)]

    def _settle_exploration_outcome(
        self,
        run,
        expedition,
        outcome,
        now: int,
        *,
        quality_bonus: int = 0,
        quantity_multiplier: float = 1.0,
        applied_effects=(),
    ) -> list[ProductionResultSnapshot]:
        """把一个结局的产出滚成冻结战利品。品质产物走五档品质，装备类走固定发放。"""

        luck_bonus = self._delve_luck_bonus(run) if expedition.kind == "delve" else 0
        probability = quality_probabilities(
            run.exploration_ability
            + (run.depth + 1) * 8
            + outcome.quality_ability_bonus
            + quality_bonus
            + luck_bonus,
            expedition.quality.thresholds,
            expedition.quality.width,
            expedition.quality.miracle_probability_cap,
            expedition.quality.miracle_eligible,
        )
        multiplier = max(0.0, quantity_multiplier)
        batches = [
            (
                reward.item_id,
                max(1, round(self.rng.randint(reward.quantity_min, reward.quantity_max) * multiplier)),
            )
            for reward in outcome.rewards
        ]
        rewards = build_results(self.rng, batches, probability, now, applied_effects)
        self._merge_exploration_rewards(run.pending_rewards, rewards)
        for fixed in outcome.fixed_rewards:
            existing = next(
                (
                    entry
                    for entry in run.pending_fixed_rewards
                    if entry.item_id == fixed.item_id and entry.quality == fixed.quality
                ),
                None,
            )
            if existing is not None:
                existing.quantity += fixed.quantity
            else:
                run.pending_fixed_rewards.append(CarriedItemSnapshot(
                    item_id=fixed.item_id,
                    quality=fixed.quality,
                    quantity=fixed.quantity,
                ))
        return rewards

    # ----------------------------------------------------------- 探秘副本（delve）

    def _build_delve_loadout(
        self,
        player: PlayerState,
        partner_ids: list[str],
        loadout_input: dict[str, dict] | None,
    ) -> dict[str, DelveLoadoutSnapshot]:
        """把玩家选的装备冻结成数值快照。装备不消耗，只在出发这一刻校验持有。"""

        selections = loadout_input or {}
        unknown = set(selections) - set(partner_ids)
        if unknown:
            raise GameError("delve_loadout_invalid", "装备配置里有不在队伍中的伙伴")
        usage: dict[str, int] = {}
        snapshots: dict[str, DelveLoadoutSnapshot] = {}
        for partner_id in partner_ids:
            entry = selections.get(partner_id) or {}
            snapshot = DelveLoadoutSnapshot()
            for slot, key in (("weapon", "weapon_item_id"), ("accessory", "accessory_item_id")):
                item_id = str(entry.get(key) or "").strip()
                if not item_id:
                    continue
                definition = self.content.item_map.get(item_id)
                if definition is None or definition.equipment is None:
                    raise GameError("delve_equipment_invalid", "选择了不存在的装备", 404)
                if definition.equipment.slot != slot:
                    raise GameError("delve_equipment_invalid", f"{definition.name}不能装备在这个槽位")
                usage[item_id] = usage.get(item_id, 0) + 1
                if usage[item_id] > sum(player.inventory.get(item_id, {}).values()):
                    raise GameError("resource_insufficient", f"{definition.name}的数量不够全队装备")
                equipment = definition.equipment
                if slot == "weapon":
                    snapshot.weapon_item_id = item_id
                    snapshot.weapon_name = definition.name
                    snapshot.attack_attribute = equipment.attribute or "strength"
                    snapshot.damage_dice = equipment.damage_dice
                else:
                    snapshot.accessory_item_id = item_id
                    snapshot.accessory_name = definition.name
                snapshot.attack_bonus += equipment.attack_bonus
                snapshot.proficiency_bonus += equipment.proficiency_bonus
                snapshot.armor_bonus += equipment.armor_bonus
                snapshot.initiative_bonus += equipment.initiative_bonus
                snapshot.max_hp_bonus += equipment.max_hp_bonus
                snapshot.advantage_uses += equipment.advantage_uses
            snapshots[partner_id] = snapshot
        return snapshots

    def _freeze_carried_items(
        self,
        player: PlayerState,
        carried_input: list[dict] | None,
    ) -> list[CarriedItemSnapshot]:
        """出发时把道具从仓库扣出来冻结在队伍身上，撤离时把没用完的退回去。"""

        frozen: list[CarriedItemSnapshot] = []
        total = 0
        for entry in carried_input or []:
            item_id = str(entry.get("item_id") or "").strip()
            quality = int(entry.get("quality") or 0)
            quantity = int(entry.get("quantity") or 0)
            if not item_id or quantity <= 0:
                continue
            definition = self.content.item_map.get(item_id)
            if definition is None or definition.delve_use is None:
                raise GameError("delve_item_invalid", "这件物品不能带进副本")
            total += quantity
            if total > delve_battle.CARRY_SLOTS:
                raise GameError("delve_item_invalid", f"最多只能携带 {delve_battle.CARRY_SLOTS} 份道具")
            try:
                remove_item(player, item_id, quantity, quality)
            except EconomyError as exc:
                raise GameError("resource_insufficient", str(exc)) from exc
            existing = next(
                (item for item in frozen if item.item_id == item_id and item.quality == quality),
                None,
            )
            if existing is not None:
                existing.quantity += quantity
            else:
                frozen.append(CarriedItemSnapshot(item_id=item_id, quality=quality, quantity=quantity))
        return frozen

    def _build_delve_party(
        self,
        player: PlayerState,
        partner_ids: list[str],
        loadout: dict[str, DelveLoadoutSnapshot],
    ) -> dict[str, DelveMemberState]:
        owned_map = {entry.partner_id: entry for entry in player.owned_partners}
        catalog = self.partner_catalog_loader().partner_map
        party: dict[str, DelveMemberState] = {}
        for partner_id in partner_ids:
            owned = owned_map[partner_id]
            stats = catalog[partner_id].exploration_stats
            snapshot = loadout.get(partner_id) or DelveLoadoutSnapshot()
            max_hp = delve_battle.member_max_hp(owned.level, stats.strength, snapshot) + int(self._talent_modifier(player, "delve_max_hp_bonus"))
            party[partner_id] = DelveMemberState(
                first_miss_reroll_ready=bool(self._talent_modifier(player, "delve_first_miss_reroll")),
                max_hp=max_hp,
                hp=max_hp,
                armor_class=delve_battle.armor_class(stats.agility, snapshot),
                initiative_bonus=snapshot.initiative_bonus,
            )
        return party

    def _delve_members(self, run) -> dict[str, PartyMember]:
        catalog = self.partner_catalog_loader().partner_map
        members: dict[str, PartyMember] = {}
        for partner_id in run.partner_ids:
            definition = catalog.get(partner_id)
            state = run.combat_party.get(partner_id)
            if definition is None or state is None:
                raise GameError("exploration_party_missing", "探索队伍已经失效", 409)
            stats = definition.exploration_stats
            members[partner_id] = PartyMember(
                partner_id=partner_id,
                name=definition.name,
                strength=stats.strength,
                agility=stats.agility,
                intelligence=stats.intelligence,
                luck=stats.luck,
                state=state,
                loadout=run.loadout.get(partner_id) or DelveLoadoutSnapshot(),
                trait_codes=list(definition.trait_codes),
            )
        return members

    def _delve_luck_bonus(self, run) -> int:
        """战利品品质按队伍里最高的幸运调整值加成。"""

        catalog = self.partner_catalog_loader().partner_map
        modifiers = [
            catalog[partner_id].exploration_stats.modifier("luck")
            for partner_id in run.partner_ids
            if partner_id in catalog
        ]
        return max(modifiers, default=0) * delve_battle.LUCK_QUALITY_BONUS

    def _exploration_party_ability(
        self,
        player: PlayerState,
        partner_ids: list[str],
        leader_partner_id: str,
    ) -> int:
        if leader_partner_id not in partner_ids:
            raise GameError("exploration_leader_invalid", "领队必须在探索队伍中")
        owned_map = {entry.partner_id: entry for entry in player.owned_partners}
        catalog = self.partner_catalog_loader().partner_map
        rules = self.content.industries["exploration"]
        abilities: dict[str, int] = {}
        for partner_id in partner_ids:
            owned = owned_map.get(partner_id)
            definition = catalog.get(partner_id)
            if owned is None:
                raise GameError("partner_not_owned", "队伍中有尚未持有的伙伴", 404)
            if definition is None:
                raise GameError("partner_not_found", "队伍中的伙伴配置不存在", 404)
            tendency = next(
                (entry for entry in definition.tendencies if entry.industry == "exploration"),
                None,
            )
            if tendency is None:
                abilities[partner_id] = 0
                continue
            effective_level = min(
                owned.level,
                rules.partner_level_cap,
                level_cap_for_breakthrough(owned.breakthrough),
            )
            abilities[partner_id] = definition.ability_at(
                "exploration",
                effective_level,
                owned.stars,
            )
        if abilities.get(leader_partner_id, 0) <= 0:
            raise GameError("exploration_leader_required", "领队必须具有探索倾向", 409)
        character_ability = rules.character_base_ability + self._industry_ability_bonus(player, "exploration")
        support = sum(ability for partner_id, ability in abilities.items() if partner_id != leader_partner_id)
        return round(character_ability + abilities[leader_partner_id] + support * 0.25)

    def _prime_exploration_check_context(self, run, choice, context: dict) -> None:
        check = choice.check
        if check is None:
            context["check_base_modifiers"] = {}
            context["check_actor_partner_ids"] = []
            context["base_dice_mode"] = None
            return
        definitions = self.partner_catalog_loader().partner_map
        candidates = [
            (partner_id, definitions[partner_id].exploration_stats.modifier(check.attribute))
            for partner_id in run.partner_ids
            if partner_id in definitions
        ]
        if not candidates:
            raise GameError("exploration_party_missing", "探索队伍已经失效", 409)
        selected_actor_id = str(context.get("selected_actor_partner_id") or "").strip()
        if selected_actor_id and selected_actor_id not in {partner_id for partner_id, _ in candidates}:
            raise GameError("exploration_actor_invalid", "该伙伴不在当前探索队伍中", 400)
        if check.mode == "sum":
            actor_partner_ids = [partner_id for partner_id, _ in candidates]
        else:
            actor_partner_id = selected_actor_id or max(candidates, key=lambda entry: entry[1])[0]
            actor_partner_ids = [actor_partner_id]
        if selected_actor_id and check.mode != "sum":
            actor_partner_ids = [selected_actor_id]
        context["check_base_modifiers"] = dict(candidates)
        context["check_actor_partner_ids"] = actor_partner_ids
        context["base_dice_mode"] = check.dice

    def _exploration_check_preview(self, run, choice, context: dict) -> dict:
        check = choice.check
        if check is None:
            return {
                "check_attribute": None,
                "check_mode": None,
                "dice_mode": None,
                "modifier": None,
                "actor_partner_ids": [],
                "success_chance": 1.0,
            }
        if "check_base_modifiers" not in context:
            self._prime_exploration_check_context(run, choice, context)
        base_modifiers = context["check_base_modifiers"]
        actor_partner_ids = context["check_actor_partner_ids"]
        if check.mode == "sum":
            modifier = sum(base_modifiers.values())
        else:
            modifier = base_modifiers[actor_partner_ids[0]]
        modifier += int(context.get("check_bonus", 0))
        dice_score = {"disadvantage": -1, "normal": 0, "advantage": 1}[check.dice]
        dice_score += int(context.get("dice_adjustment", 0))
        dice_mode = "advantage" if dice_score > 0 else "disadvantage" if dice_score < 0 else "normal"
        critical_success_min = int(context.get("critical_success_min", 20))
        successful_faces = sum(
            1
            for face in range(1, 21)
            if face >= critical_success_min or (face != 1 and face + modifier >= check.dc)
        )
        normal_chance = successful_faces / 20
        if dice_mode == "advantage":
            success_chance = 1 - (1 - normal_chance) ** 2
        elif dice_mode == "disadvantage":
            success_chance = normal_chance**2
        else:
            success_chance = normal_chance
            if context.get("failure_reroll_usage_key"):
                ordinary_failure_faces = sum(
                    1
                    for face in range(2, 20)
                    if face < critical_success_min and face + modifier < check.dc
                )
                success_chance += ordinary_failure_faces / 20 * normal_chance
        if context.get("failure_rescue_usage_key"):
            # 改判只放过普通失败，所以剩下的失败面只有大失败（自然 1）。
            critical_failure_chance = {
                "advantage": 1 / 400,
                "disadvantage": 39 / 400,
            }.get(dice_mode, 1 / 20)
            success_chance = max(success_chance, 1 - critical_failure_chance)
        return {
            "check_attribute": check.attribute,
            "check_mode": check.mode,
            "dice_mode": dice_mode,
            "modifier": modifier,
            "actor_partner_ids": actor_partner_ids,
            "success_chance": success_chance,
        }

    def _resolve_exploration_check(self, run, choice, context: dict) -> dict:
        preview = self._exploration_check_preview(run, choice, context)
        if choice.check is None:
            return {
                **preview,
                "success": True,
                "degree": "automatic_success",
                "rolls": [],
                "kept_roll": None,
                "total": None,
            }
        dice_mode = preview["dice_mode"]
        rolls = list(run.current_rolls if dice_mode != "normal" else run.current_rolls[:1])
        kept_roll = max(rolls) if dice_mode == "advantage" else min(rolls) if dice_mode == "disadvantage" else rolls[0]

        def judge(face: int) -> tuple[bool, str, int]:
            total_value = face + int(preview["modifier"])
            if face == 1:
                return False, "critical_failure", total_value
            if face >= int(context.get("critical_success_min", 20)):
                return True, "critical_success", total_value
            passed = total_value >= choice.check.dc
            return passed, "success" if passed else "failure", total_value

        success, degree, total = judge(kept_roll)
        reroll_usage_key = context.get("failure_reroll_usage_key")
        if degree == "failure" and dice_mode == "normal" and reroll_usage_key:
            rolls = list(run.current_rolls)
            kept_roll = rolls[1]
            success, degree, total = judge(kept_roll)
            if reroll_usage_key not in context["trait_usage_consumptions"]:
                context["trait_usage_consumptions"].append(reroll_usage_key)
        # 兜底改判排在重投之后：先让重投把骰子用掉，仍是普通失败才动用改判。
        rescue_usage_key = context.get("failure_rescue_usage_key")
        if degree == "failure" and rescue_usage_key:
            success, degree = True, "success"
            if rescue_usage_key not in context["trait_usage_consumptions"]:
                context["trait_usage_consumptions"].append(rescue_usage_key)
        return {
            **preview,
            "success": success,
            "degree": degree,
            "rolls": rolls,
            "kept_roll": kept_roll,
            "total": total,
        }

    @staticmethod
    def _exploration_failure_stamina_reduction(context: dict, degree: str) -> int:
        if degree == "failure":
            return max(0, int(context.get("ordinary_failure_stamina_reduction", 0)))
        if degree == "critical_failure":
            return max(0, int(context.get("critical_failure_stamina_reduction", 0)))
        return 0

    def _pick_exploration_event(
        self,
        expedition: ExplorationExpeditionDefinition,
        run: ExplorationRunState | None,
        depth: int,
    ) -> str:
        if depth >= expedition.max_depth:
            return expedition.final_event_id
        counts = run.event_counts if run else {}
        eligible = [
            event
            for event in expedition.events
            if event.id != expedition.final_event_id
            and event.min_depth <= depth <= event.max_depth
            and counts.get(event.id, 0) < event.max_occurrences
        ]
        if not eligible:
            raise GameError("exploration_route_invalid", "当前深度没有可用的探索事件", 500)
        draw = self.rng.random() * sum(event.weight for event in eligible)
        cumulative = 0.0
        for event in eligible:
            cumulative += event.weight
            if draw < cumulative:
                return event.id
        return eligible[-1].id

    @staticmethod
    def _exploration_discount_rate(ability: int) -> float:
        bounded = max(0, int(ability))
        return 0.3 * bounded / (bounded + 120)

    def _exploration_choice_cost(
        self,
        run: ExplorationRunState,
        choice: ExplorationChoiceDefinition,
        surcharge: int,
        route_multiplier: float = 1,
    ) -> tuple[int, int, int]:
        route = max(0, choice.route_stamina - run.next_route_discount)
        route = max(0, ceil(route * max(0.0, route_multiplier)))
        route_raw = run.route_stamina_raw + route
        action_total = run.action_stamina_spent + choice.action_stamina + surcharge
        projected = action_total + ceil(
            route_raw * (1 - self._exploration_discount_rate(run.exploration_ability))
        )
        return max(0, projected - run.stamina_spent), route_raw, action_total

    def _delve_battle_snapshot(self, run, partner_map: dict[str, dict]) -> dict | None:
        battle = run.battle
        if battle is None:
            return None
        members = self._delve_members(run)
        actor_id = delve_battle.current_actor(battle, members)
        return {
            **battle.model_dump(exclude={"logs"}),
            "current_actor": actor_id,
            "current_actor_is_party": actor_id in members,
            "current_actor_name": (
                partner_map.get(actor_id, {}).get("name")
                if actor_id in members
                else next((entry.name for entry in battle.enemies if entry.key == actor_id), "")
            ),
            "order": [
                {
                    "key": key,
                    "is_party": key in members,
                    "name": (
                        partner_map.get(key, {}).get("name", key)
                        if key in members
                        else next((entry.name for entry in battle.enemies if entry.key == key), key)
                    ),
                }
                for key in battle.order
            ],
            "logs": [entry.model_dump() for entry in battle.logs[-12:]],
        }

    def _exploration_actor_options(
        self,
        run: ExplorationRunState,
        choice: ExplorationChoiceDefinition,
        expedition: ExplorationExpeditionDefinition,
        event: ExplorationEventDefinition,
        now: int,
        partner_map: dict[str, dict],
    ) -> list[dict]:
        if choice.check is None or choice.check.mode == "sum":
            return []
        options: list[dict] = []
        for partner_id in run.partner_ids:
            if partner_id not in partner_map:
                continue
            context = {
                "industry": "exploration",
                "exploration_type": expedition.kind,
                "expedition_id": expedition.id,
                "event_id": event.id,
                "choice_id": choice.id,
                "depth": run.depth,
                "selected_actor_partner_id": partner_id,
                "check_attribute": choice.check.attribute,
                "check_mode": choice.check.mode,
                "check_bonus": run.talent_check_bonus,
                "dice_adjustment": 0,
                "critical_success_min": 20,
                "ordinary_failure_stamina_reduction": 0,
                "critical_failure_stamina_reduction": 0,
                "leader_partner_id": run.leader_partner_id,
                "trait_usage": run.trait_usage,
                "trait_usage_consumptions": [],
                "route_stamina_multiplier": 1.0,
                "reward_quantity_multiplier": 1.0,
                "quality_ability_bonus": 0,
                "applied_effects": [],
                "world": self._world_snapshot(now),
            }
            self._prime_exploration_check_context(run, choice, context)
            self._execute_partner_trait_phase(run.partner_ids, "exploration_event", context)
            preview = self._exploration_check_preview(run, choice, context)
            options.append({
                "partner_id": partner_id,
                "name": partner_map[partner_id].get("name", partner_id),
                "modifier": preview["modifier"],
                "dice_mode": preview["dice_mode"],
                "success_chance": preview["success_chance"],
                "applied_effects": [
                    effect for effect in context["applied_effects"]
                    if effect.get("source_partner_id") == partner_id
                ],
            })
        return options

    @staticmethod
    def _merge_exploration_rewards(
        current: list[ProductionResultSnapshot],
        additions: list[ProductionResultSnapshot],
    ) -> None:
        for addition in additions:
            existing = next(
                (
                    reward
                    for reward in current
                    if reward.item_id == addition.item_id and reward.quality == addition.quality
                ),
                None,
            )
            if existing is None:
                current.append(addition.model_copy(deep=True))
            else:
                existing.quantity += addition.quantity

    def _exploration_snapshot(self, player: PlayerState, now: int, partner_map: dict[str, dict]) -> dict:
        expedition_map = self.content.exploration_expedition_map
        visible = self._visible_expeditions(player)
        expeditions = [
            {
                **expedition.model_dump(exclude={"events"}),
                "unlocked": player.level >= expedition.min_level,
                "affordable": player.coins >= expedition.entry_fee,
                **self._season_notes(expedition, "exploration", now),
            }
            for expedition in visible
        ]
        run = player.exploration_run
        if run is None:
            return {
                "unlocked": any(player.level >= entry.min_level for entry in visible),
                "expeditions": expeditions,
                "active_run": None,
            }
        expedition = expedition_map.get(run.expedition_id)
        if expedition is None:
            return {"unlocked": True, "expeditions": expeditions, "active_run": run.model_dump()}
        event = expedition.event_map.get(run.current_event_id) if run.status == "active" else None
        choice_snapshots = []
        if event is not None:
            for choice in event.choices:
                actor_partner_id = (
                    run.leader_partner_id
                    if choice.check and run.leader_partner_id in run.partner_ids
                    else run.partner_ids[0]
                    if choice.check
                    else ""
                )
                context = {
                    "industry": "exploration",
                    "exploration_type": expedition.kind,
                    "expedition_id": expedition.id,
                    "event_id": event.id,
                    "choice_id": choice.id,
                    "depth": run.depth,
                    "selected_actor_partner_id": actor_partner_id,
                    "check_attribute": choice.check.attribute if choice.check else None,
                    "check_mode": choice.check.mode if choice.check else None,
                    "check_bonus": run.talent_check_bonus,
                    "dice_adjustment": 0,
                    "critical_success_min": 20,
                    "ordinary_failure_stamina_reduction": 0,
                    "critical_failure_stamina_reduction": 0,
                    "leader_partner_id": run.leader_partner_id,
                    "trait_usage": run.trait_usage,
                    "trait_usage_consumptions": [],
                    "route_stamina_multiplier": 1.0,
                    "reward_quantity_multiplier": 1.0,
                    "quality_ability_bonus": 0,
                    "applied_effects": [],
                    "world": self._world_snapshot(now),
                }
                self._prime_exploration_check_context(run, choice, context)
                self._execute_partner_trait_phase(run.partner_ids, "exploration_event", context)
                check_preview = self._exploration_check_preview(run, choice, context)
                possible_costs = []
                outcomes = (
                    (choice.success, "success"),
                    (choice.failure, "failure"),
                    (choice.critical_success, "critical_success"),
                    (choice.critical_failure, "critical_failure"),
                )
                for outcome, degree in outcomes:
                    if outcome is None:
                        continue
                    surcharge = max(
                        0,
                        outcome.stamina_surcharge - self._exploration_failure_stamina_reduction(context, degree),
                    )
                    cost, _, _ = self._exploration_choice_cost(
                        run,
                        choice,
                        surcharge,
                        float(context["route_stamina_multiplier"]),
                    )
                    possible_costs.append(cost)
                choice_snapshots.append({
                    **choice.model_dump(exclude={"success", "failure", "critical_success", "critical_failure"}),
                    **check_preview,
                    "actor_partner_id": actor_partner_id if choice.check and choice.check.mode != "sum" else None,
                    "actor_options": self._exploration_actor_options(
                        run,
                        choice,
                        expedition,
                        event,
                        now,
                        partner_map,
                    ),
                    "check_actor_names": [
                        partner_map[partner_id]["name"]
                        for partner_id in check_preview["actor_partner_ids"]
                        if partner_id in partner_map
                    ],
                    "stamina_cost_min": min(possible_costs),
                    "stamina_cost_max": max(possible_costs),
                    "applied_effects": list(context["applied_effects"]),
                })
        return {
            "unlocked": player.level >= expedition.min_level,
            "expeditions": expeditions,
            "active_run": {
                **run.model_dump(exclude={"pending_rewards", "logs", "current_rolls", "trait_usage"}),
                "expedition": expedition.model_dump(exclude={"events"}),
                "party": [
                    {
                        **partner_map[partner_id],
                        "loadout": run.loadout[partner_id].model_dump() if partner_id in run.loadout else None,
                        "combat": run.combat_party[partner_id].model_dump() if partner_id in run.combat_party else None,
                    }
                    for partner_id in run.partner_ids
                    if partner_id in partner_map
                ],
                "carried_items": [
                    {
                        **carried.model_dump(),
                        "item": self.content.item_map[carried.item_id].model_dump(),
                    }
                    for carried in run.carried_items
                    if carried.item_id in self.content.item_map
                ],
                "pending_fixed_rewards": [
                    {
                        **fixed.model_dump(),
                        "item": self.content.item_map[fixed.item_id].model_dump(),
                    }
                    for fixed in run.pending_fixed_rewards
                    if fixed.item_id in self.content.item_map
                ],
                "battle": self._delve_battle_snapshot(run, partner_map),
                "leader": partner_map.get(run.leader_partner_id),
                "stamina_discount_rate": self._exploration_discount_rate(run.exploration_ability),
                "pending_rewards": [self._result_snapshot(reward) for reward in run.pending_rewards],
                "current_event": {
                    **event.model_dump(exclude={"choices"}),
                    "choices": choice_snapshots,
                } if event else None,
                "logs": [
                    {
                        **log.model_dump(exclude={"rewards"}),
                        "event_name": expedition.event_map[log.event_id].name
                        if log.event_id in expedition.event_map else log.event_id,
                        "rewards": [self._result_snapshot(reward) for reward in log.rewards],
                    }
                    for log in run.logs
                ],
            },
        }

    def _snapshot(self, player: PlayerState, now: int | None = None) -> dict:
        now = self._now() if now is None else now
        level = self.content.level_definition(player.level)
        next_level = next((entry for entry in self.content.levels if entry.level > player.level and self._content_visible(player, entry)), None)
        items = self.content.item_map
        crops = self.content.crop_map
        inventory = []
        for item_id, qualities in sorted(player.inventory.items()):
            item = items.get(item_id)
            for quality, quantity in sorted(qualities.items()):
                if quantity <= 0:
                    continue
                base_sell_price = item.sell_price if item else 0
                grade = self.content.quality.grade_map.get(quality)
                inventory.append({
                    "item_id": item_id,
                    "inventory_key": f"{item_id}:{quality}",
                    "name": item.name if item else item_id,
                    "icon": item.icon if item else "package",
                    "kind": item.kind if item else "material",
                    "tags": list(item.tags) if item else [],
                    "quantity": quantity,
                    "quality": quality or None,
                    "quality_name": grade.name if grade else None,
                    "quality_sale_multiplier": grade.sale_multiplier if grade else 1,
                    "base_sell_price": base_sell_price,
                    "sell_price": self._quality_unit_price(base_sell_price, quality),
                    # 探秘副本的编队面板要按槽位筛装备、按可用性筛道具。
                    "equipment": item.equipment.model_dump() if item and item.equipment else None,
                    "delve_use": item.delve_use.model_dump() if item and item.delve_use else None,
                })
        partner_records = self._partner_snapshots(player, now)
        partner_map = {entry["partner_id"]: entry for entry in partner_records}
        lock_deadlines = self._partner_lock_deadlines(player, now)
        plots = []
        for plot in player.plots:
            if plot.slot % 2 == 1 and plot.slot < player.farm_upgrade_stage * 2:
                continue
            units = self._farm_units(player, plot.slot)
            crop = crops.get(plot.crop_id)
            assigned_partners = [
                partner_map[partner_id]
                for partner_id in plot.assigned_partner_ids
                if partner_id in partner_map
            ]
            assignment_locked_until = max(
                (lock_deadlines.get(partner_id, 0) for partner_id in plot.assigned_partner_ids),
                default=0,
            )
            plots.append({
                **plot.model_dump(),
                "task_results": [self._result_snapshot(result) for unit in units for result in unit.task_results],
                "size": len(units),
                "empty": plot.empty,
                "ready": bool(not plot.empty and plot.ready_at <= now),
                "remaining_seconds": max(0, plot.ready_at - now) if not plot.empty else 0,
                "crop": crop.model_dump() if crop else None,
                "assigned_partners": assigned_partners,
                "assignment_locked": assignment_locked_until > now,
                "assignment_locked_until": assignment_locked_until or None,
            })
        next_plot_level = next(
            (entry.level for entry in self.content.levels if entry.plot_slots > len(player.plots)),
            None,
        )
        gathering_sites = []
        gathering_task_map = self.content.gathering_task_map
        for site in player.gathering_sites:
            definition = self.content.gathering_site_map.get(site.site_id)
            if not self._content_visible(player, definition) and not (site.task_snapshot or site.assigned_partner_ids):
                continue
            task_snapshot = site.task_snapshot
            assigned_partners = [
                partner_map[partner_id]
                for partner_id in site.assigned_partner_ids
                if partner_id in partner_map
            ]
            assignment_locked_until = max(
                (lock_deadlines.get(partner_id, 0) for partner_id in site.assigned_partner_ids),
                default=0,
            )
            gathering_sites.append({
                **site.model_dump(),
                "task_results": [self._result_snapshot(result) for result in site.task_results],
                "empty": site.empty,
                "ready": bool(task_snapshot and task_snapshot.ready_at <= now),
                "remaining_seconds": max(0, task_snapshot.ready_at - now) if task_snapshot else 0,
                "definition": definition.model_dump() if definition else None,
                **self._season_notes(definition, "gathering", now),
                "event_closed": not self._season_open(definition, now),
                "task": {
                    **gathering_task_map[task_snapshot.content_id].model_dump(),
                    "item": items[gathering_task_map[task_snapshot.content_id].outputs[0].item_id].model_dump(),
                    "outputs": [
                        {**output.model_dump(), "item": items[output.item_id].model_dump()}
                        for output in gathering_task_map[task_snapshot.content_id].outputs
                    ],
                } if task_snapshot and task_snapshot.content_id in gathering_task_map else None,
                "available_tasks": [
                    {
                        **entry.model_dump(),
                        "item": items[entry.outputs[0].item_id].model_dump(),
                        "outputs": [
                            {**output.model_dump(), "item": items[output.item_id].model_dump()}
                            for output in entry.outputs
                        ],
                    }
                    for entry in self.content.gathering_tasks
                    if entry.site_id == site.site_id and entry.min_level <= player.level and self._content_visible(player, entry)
                ],
                "assigned_partners": assigned_partners,
                "assignment_locked": assignment_locked_until > now,
                "assignment_locked_until": assignment_locked_until or None,
            })
        unlocked_gathering_site_ids = {site.site_id for site in player.gathering_sites}
        next_gathering_site_level = next(
            (
                definition.min_level
                for definition in self.content.gathering_sites
                if definition.id not in unlocked_gathering_site_ids and self._content_visible(player, definition)
            ),
            None,
        )
        crafting_stations = []
        recipe_map = self.content.recipe_map
        for station in player.crafting_stations:
            definition = self.content.crafting_station_map.get(station.station_id)
            task_snapshot = station.task_snapshot
            assigned_partners = [
                partner_map[partner_id]
                for partner_id in station.assigned_partner_ids
                if partner_id in partner_map
            ]
            assignment_locked_until = max(
                (lock_deadlines.get(partner_id, 0) for partner_id in station.assigned_partner_ids),
                default=0,
            )
            crafting_stations.append({
                **station.model_dump(exclude={"queued_tasks", "completed_tasks"}),
                "queued_count": len(station.queued_tasks),
                "completed_count": len(station.completed_tasks) + int(bool(station.task_results)),
                "queue_remaining_seconds": (
                    max(0, task_snapshot.ready_at - now)
                    + sum(task.final_duration for task in station.queued_tasks)
                ) if task_snapshot else 0,
                "completed_results": [
                    self._result_snapshot(result)
                    for entry in station.completed_tasks for result in entry.task_results
                ],
                "task_results": [self._result_snapshot(result) for result in station.task_results],
                "empty": station.empty,
                "ready": bool(task_snapshot and task_snapshot.ready_at <= now),
                "remaining_seconds": max(0, task_snapshot.ready_at - now) if task_snapshot else 0,
                "definition": definition.model_dump() if definition else None,
                "recipe": self._recipe_snapshot(player, recipe_map[task_snapshot.content_id])
                if task_snapshot and task_snapshot.content_id in recipe_map else None,
                "recipes": [
                    self._recipe_snapshot(player, recipe)
                    for recipe in self.content.recipes
                    if recipe.station_id == station.station_id and self._content_visible(player, recipe)
                ],
                "assigned_partners": assigned_partners,
                "assignment_locked": assignment_locked_until > now,
                "assignment_locked_until": assignment_locked_until or None,
            })
        unlocked_crafting_station_ids = {station.station_id for station in player.crafting_stations}
        next_crafting_station_level = next(
            (
                definition.min_level
                for definition in self.content.crafting_stations
                if definition.id not in unlocked_crafting_station_ids
            ),
            None,
        )
        mining_sites = []
        mining_task_map = self.content.mining_task_map
        for site in player.mining_sites:
            definition = self.content.mining_site_map.get(site.site_id)
            task_snapshot = site.task_snapshot
            assigned_partners = [
                partner_map[partner_id]
                for partner_id in site.assigned_partner_ids
                if partner_id in partner_map
            ]
            assignment_locked_until = max(
                (lock_deadlines.get(partner_id, 0) for partner_id in site.assigned_partner_ids),
                default=0,
            )
            mining_sites.append({
                **site.model_dump(),
                "task_results": [self._result_snapshot(result) for result in site.task_results],
                "empty": site.empty,
                "ready": bool(task_snapshot and task_snapshot.ready_at <= now),
                "remaining_seconds": max(0, task_snapshot.ready_at - now) if task_snapshot else 0,
                "definition": definition.model_dump() if definition else None,
                "task": {
                    **mining_task_map[task_snapshot.content_id].model_dump(),
                    "item": items[mining_task_map[task_snapshot.content_id].produce_item_id].model_dump(),
                } if task_snapshot and task_snapshot.content_id in mining_task_map else None,
                "available_tasks": [
                    {**entry.model_dump(), "item": items[entry.produce_item_id].model_dump()}
                    for entry in self.content.mining_tasks
                    if entry.site_id == site.site_id and entry.min_level <= player.level and self._content_visible(player, entry)
                ],
                "assigned_partners": assigned_partners,
                "assignment_locked": assignment_locked_until > now,
                "assignment_locked_until": assignment_locked_until or None,
            })
        unlocked_mining_site_ids = {site.site_id for site in player.mining_sites}
        next_mining_site_level = next(
            (
                definition.min_level
                for definition in self.content.mining_sites
                if definition.id not in unlocked_mining_site_ids
            ),
            None,
        )
        return {
            "server_time": now,
            "world": self._world_snapshot(now),
            "player": {
                "player_id": player.player_id,
                "display_name": player.display_name,
                "level": player.level,
                "experience": player.experience,
                "current_level_xp": level.total_xp,
                "next_level_xp": next_level.total_xp if next_level else None,
                "coins": player.coins,
                "maple_flame": player.maple_flame,
                "guide_leaves": player.guide_leaves,
                "companion_marks": player.companion_marks,
                "stamina": player.stamina,
                "stamina_cap": level.stamina_cap,
                "stamina_restore_seconds": self.content.stamina.restore_seconds,
                "stamina_updated_at": player.stamina_updated_at,
                "unlocks": sorted({unlock for entry in self.content.levels if entry.level <= player.level for unlock in entry.unlocks}),
            },
            "plots": plots,
            "facilities": self._facility_snapshot(player, partner_map, now),
            "next_plot_level": next_plot_level,
            "gathering_sites": gathering_sites,
            "next_gathering_site_level": next_gathering_site_level,
            "crafting_stations": crafting_stations,
            "next_crafting_station_level": next_crafting_station_level,
            "mining_sites": mining_sites,
            "next_mining_site_level": next_mining_site_level,
            "aquatic": self._aquatic_snapshot(player, now, partner_map),
            "livestock": self._livestock_snapshot(player, now, partner_map),
            "exploration": self._exploration_snapshot(player, now, partner_map),
            "sailing": self._sailing_snapshot(player, now),
            "inventory": inventory,
            "task_items": [
                {
                    **definition.model_dump(),
                    "quantity": player.task_items.get(definition.id, 0),
                }
                for definition in self.content.task_items
                if player.task_items.get(definition.id, 0) > 0
            ],
            "partners": partner_records,
            "partner_count": len(partner_records),
            "partner_growth": {
                "level_experience_costs": {
                    level: self.content.partner_growth.experience_for_next_level(level)
                    for level in range(1, max((record.get("level_cap", 20) for record in partner_records), default=20))
                },
                "experience_books": [
                    {
                        "item_id": item_id,
                        "experience": experience,
                        "item": items[item_id].model_dump(),
                        "owned": sum(player.inventory.get(item_id, {}).values()),
                    }
                    for item_id, experience in self.content.partner_growth.experience_books.items()
                ],
            },
            "gacha_pools": self._gacha_pools_snapshot(player),
            "industry_rules": {
                industry: {
                    **rules.model_dump(),
                    "base_character_ability": rules.character_base_ability,
                    "global_ability_bonus": self._industry_ability_bonus(player, industry),
                    "character_base_ability": rules.character_base_ability + self._industry_ability_bonus(player, industry),
                    "base_partner_capacity": rules.partner_capacity,
                    "partner_capacity": self._industry_partner_capacity(player, industry),
                }
                for industry, rules in self.content.industries.items()
            },
            "talents": self._talent_snapshot(player),
            "portals": self._portal_snapshot(player),
            "commissions": self._commission_snapshot(player, now),
            "monthly_card": self._monthly_card_snapshot(player, now),
            "stamina_supply": self._stamina_snapshot(player, now),
            "mail": self._mail_summary(player, now),
            "achievements": achievement_snapshot(player, self.content),
            "summer_event": self._summer_snapshot(player, now),
            "crops": [
                crop.model_dump()
                for crop in self.content.crops
                if player.level >= crop.min_level and self._content_visible(player, crop)
            ],
            "shop": [
                {
                    **entry.model_dump(),
                    "item": items[entry.item_id].model_dump(),
                    "crop": next(
                        (crop.model_dump() for crop in self.content.crops if crop.seed_item_id == entry.item_id),
                        None,
                    ),
                    "locked": player.level < entry.min_level,
                }
                for entry in self.content.shop
                if self._content_visible(player, entry)
            ],
        }

    def _partner_snapshots(self, player: PlayerState, now: int | None = None) -> list[dict]:
        now = self._now() if now is None else now
        catalog = self.partner_catalog_loader()
        definitions = catalog.partner_map
        trait_definitions = {trait.code: trait for trait in partner_trait_catalog()}
        assigned_slots = {
            partner_id: plot.slot
            for plot in player.plots
            for partner_id in plot.assigned_partner_ids
        }
        assigned_gathering_sites = {
            partner_id: site.site_id
            for site in player.gathering_sites
            for partner_id in site.assigned_partner_ids
        }
        assigned_crafting_stations = {
            partner_id: station.station_id
            for station in player.crafting_stations
            for partner_id in station.assigned_partner_ids
        }
        assigned_mining_sites = {
            partner_id: site.site_id
            for site in player.mining_sites
            for partner_id in site.assigned_partner_ids
        }
        lock_deadlines = self._partner_lock_deadlines(player, now)
        records: list[dict] = []
        for owned in sorted(player.owned_partners, key=lambda entry: (entry.acquired_at, entry.partner_id)):
            definition = definitions.get(owned.partner_id)
            if definition is None:
                records.append({
                    **owned.model_dump(),
                    "missing": True,
                    "name": owned.partner_id,
                    "rarity": None,
                    "assigned_plot_slot": assigned_slots.get(owned.partner_id),
                    "assigned_gathering_site_id": assigned_gathering_sites.get(owned.partner_id),
                    "assigned_crafting_station_id": assigned_crafting_stations.get(owned.partner_id),
                    "assigned_refining": owned.partner_id in player.refining.assigned_partner_ids,
                    "assigned_mining_site_id": assigned_mining_sites.get(owned.partner_id),
                    "locked": lock_deadlines.get(owned.partner_id, 0) > now,
                    "locked_until": lock_deadlines.get(owned.partner_id) or None,
                })
                continue
            artwork_stage = owned.artwork_stage
            if (artwork_stage is None or artwork_stage > owned.breakthrough
                    or definition.artwork_for(artwork_stage) is None):
                artwork_stage = owned.breakthrough
            artwork = definition.artwork_for(artwork_stage)
            avatar_crop = next(
                (crop for crop in definition.avatar_crops if crop.breakthrough == artwork_stage),
                None,
            )
            records.append({
                **owned.model_dump(),
                "missing": False,
                "name": definition.name,
                "rarity": definition.rarity,
                "origin_rarity": definition.rarity,
                "stars": owned.stars,
                "description": definition.description,
                "growth_curve": definition.growth_curve,
                "growth_curve_name": GROWTH_CURVE_NAMES[definition.growth_curve],
                "exploration_high_stats": definition.exploration_stats.high_attributes,
                "level_cap": level_cap_for_breakthrough(owned.breakthrough),
                "experience_to_next_level": (
                    self.content.partner_growth.experience_for_next_level(owned.level)
                    if owned.level < level_cap_for_breakthrough(owned.breakthrough)
                    else None
                ),
                "artwork_stage": artwork_stage,
                "available_artworks": [
                    entry.model_dump() for entry in sorted(definition.artworks, key=lambda entry: entry.breakthrough)
                    if entry.breakthrough <= owned.breakthrough
                ],
                "artwork": artwork.model_dump() if artwork else None,
                "avatar_crop": avatar_crop.model_dump() if avatar_crop else None,
                "tendencies": [
                    self._partner_tendency_snapshot(definition, tendency, owned)
                    for tendency in definition.tendencies
                ],
                "traits": [
                    {
                        "code": code,
                        "name": trait_definitions[code].name if code in trait_definitions else code,
                        "description": trait_definitions[code].description if code in trait_definitions else "",
                        "implemented": trait_definitions[code].implemented if code in trait_definitions else False,
                        "phases": sorted(trait_definitions[code].phases) if code in trait_definitions else [],
                    }
                    for code in definition.trait_codes
                ],
                "upgrade_available": owned.level < level_cap_for_breakthrough(owned.breakthrough),
                "star_up_available": owned.stars < 5,
                "star_up_cost": self.content.gacha_economy.star_up_costs.get(owned.stars),
                **self._partner_breakthrough_snapshot(player, definition, owned),
                "assigned_plot_slot": assigned_slots.get(owned.partner_id),
                "assigned_gathering_site_id": assigned_gathering_sites.get(owned.partner_id),
                "assigned_crafting_station_id": assigned_crafting_stations.get(owned.partner_id),
                    "assigned_refining": owned.partner_id in player.refining.assigned_partner_ids,
                "assigned_mining_site_id": assigned_mining_sites.get(owned.partner_id),
                "locked": lock_deadlines.get(owned.partner_id, 0) > now,
                "locked_until": lock_deadlines.get(owned.partner_id) or None,
            })
        return records

    def _partner_tendency_snapshot(self, definition, tendency, owned: OwnedPartnerState) -> dict:
        breakthrough_cap = level_cap_for_breakthrough(owned.breakthrough)
        rules = self.content.industries.get(tendency.industry)
        effective_level = min(
            owned.level,
            breakthrough_cap,
            rules.partner_level_cap if rules else breakthrough_cap,
        )
        return {
            **tendency.model_dump(),
            "name": INDUSTRY_NAMES[tendency.industry],
            "current_ability": definition.ability_at(tendency.industry, owned.level, owned.stars),
            "effective_level": effective_level,
            "effective_ability": definition.ability_at(tendency.industry, effective_level, owned.stars),
        }

    def _partner_breakthrough_snapshot(self, player: PlayerState, definition, owned: OwnedPartnerState) -> dict:
        if owned.breakthrough >= 2:
            return {"breakthrough_available": False, "breakthrough_reason": "已完成全部突破", "ascension": None}
        target = owned.breakthrough + 1
        ascension = next((entry for entry in definition.ascensions if entry.breakthrough == target), None)
        if ascension is None:
            return {"breakthrough_available": False, "breakthrough_reason": "暂不能突破", "ascension": None}
        items = []
        affordable = player.coins >= ascension.coins
        for requirement in ascension.items:
            item = self.content.item_map.get(requirement.item_id)
            owned_quantity = sum(
                quantity
                for quality, quantity in player.inventory.get(requirement.item_id, {}).items()
                if quality >= requirement.min_quality
            )
            affordable = affordable and item is not None and owned_quantity >= requirement.quantity
            items.append({
                **requirement.model_dump(),
                "name": item.name if item else requirement.item_id,
                "icon": item.icon if item else "package",
                "owned": owned_quantity,
                "kind": item.kind if item else "material",
            })
        required_level = level_cap_for_breakthrough(owned.breakthrough)
        artwork = definition.artwork_for(target)
        reason = None
        if player.level < ascension.min_player_level:
            reason = f"居民达到 {ascension.min_player_level} 级后开放"
        elif owned.level < required_level:
            reason = f"伙伴达到 {required_level} 级后可突破"
        elif not affordable:
            reason = "突破材料或红叶币不足"
        return {
            "breakthrough_available": reason is None,
            "breakthrough_reason": reason,
            "ascension": {
                "breakthrough": target, "coins": ascension.coins, "items": items,
                "min_player_level": ascension.min_player_level,
                "required_partner_level": required_level,
                "level_cap": level_cap_for_breakthrough(target),
                "artwork": artwork.model_dump() if artwork else None,
                "tendencies": [
                    {"industry": tendency.industry, "name": INDUSTRY_NAMES[tendency.industry],
                     "current_ability": definition.ability_at(tendency.industry, owned.level, owned.stars),
                     "max_ability": definition.ability_at(tendency.industry, level_cap_for_breakthrough(target), owned.stars)}
                    for tendency in definition.tendencies
                ],
            },
        }

    def _gacha_pools_snapshot(self, player: PlayerState) -> list[dict]:
        catalog = self.partner_catalog_loader()
        catalog_payload = {
            definition.id: {
                "partner_id": definition.id,
                "name": definition.name,
                "rarity": definition.rarity,
                "artwork": (
                    definition.artwork_for(0).model_dump()
                    if definition.artwork_for(0)
                    else None
                ),
                "avatar_crop": next(
                    (entry.model_dump() for entry in definition.avatar_crops if entry.breakthrough == 0),
                    None,
                ),
            }
            for definition in catalog.partners
            if definition.recruitable
        }
        task_items_payload = [entry.model_dump() for entry in self.content.task_items]
        story_assets = self.story_asset_loader().asset_map
        pools = sorted(self.gacha_pool_loader().values(), key=lambda entry: (entry.display_order, entry.min_level, entry.pool_id))
        snapshots = []
        for gacha in pools:
            if not self._content_visible(player, gacha):
                continue
            candidates = self._pool_partner_candidates(gacha, catalog)
            # 还凑不齐三个星级的池子（比如限定池的立绘还没画完）先不摆出来。
            if not self._pool_is_open(candidates):
                continue
            progress = player.gacha_progress.get(gacha.pool_id)
            total_pulls = progress.total_pulls if progress else 0
            four_pity = progress.four_pity if progress else 0
            five_pity = progress.five_pity if progress else 0
            remaining_pulls = (
                max(0, gacha.max_pulls_per_player - total_pulls)
                if gacha.max_pulls_per_player is not None
                else None
            )
            # 限定池抽完就撤下，不再占着招募界面。
            if remaining_pulls == 0:
                continue
            background_asset = story_assets.get(gacha.background_asset_id) if gacha.background_asset_id else None
            snapshots.append({
                "pool_id": gacha.pool_id,
                "title": gacha.title,
                "unlocked": player.level >= gacha.min_level,
                "min_level": gacha.min_level,
                "maple_flame_per_leaf": self.content.gacha_economy.maple_flame_per_leaf,
                "rarity_probabilities": gacha.rarity_probabilities,
                "item_probability": gacha.item_probability,
                "four_star_guarantee": gacha.four_star_guarantee,
                "five_star_pity": gacha.five_star_pity,
                "pulls_until_four_star": gacha.four_star_guarantee - four_pity,
                "pulls_until_five_star": gacha.five_star_pity - five_pity,
                "max_pulls_per_player": gacha.max_pulls_per_player,
                "total_pulls": total_pulls,
                "remaining_pulls": remaining_pulls,
                "background": background_asset.model_dump() if background_asset else None,
                "featured_partner_id": gacha.featured_partner_id,
                "featured_rate": gacha.featured_rate,
                "featured_guaranteed": self._gacha_featured_guaranteed(player, gacha),
                "featured_four_star_partner_id": gacha.featured_four_star_partner_id,
                "featured_four_star_rate": gacha.featured_four_star_rate,
                "catalog": [
                    catalog_payload[definition.id]
                    for rarity in (5, 4, 3)
                    for definition in candidates[rarity]
                ],
                "task_items": task_items_payload,
            })
        return snapshots

    def _clear_partner_assignment(self, player: PlayerState, partner_id: str, now: int | None = None) -> None:
        """把伙伴从别的生产格上腾出来。

        任务型生产格随时可以撤，资产格（鱼塘、畜栏）不行：只有周期开头的自由窗口内才能
        抽人，过了窗口就得等这个周期跑完，否则在周期末尾把强力伙伴挪过来，整个周期都会
        按新伙伴结算。排队中的伙伴同样动不了 —— 预约期间他不能去干别的事。
        """

        now = self._now() if now is None else now
        if partner_id in player.refining.assigned_partner_ids:
            if player.refining.occupied:
                raise GameError("refining_not_empty", "精制房全部清空后才能换人", 409)
            self._settle_refining(player, now)
            player.refining.assigned_partner_ids = []
        for production_slot in [
            *player.plots,
            *player.gathering_sites,
            *player.crafting_stations,
            *player.mining_sites,
        ]:
            if partner_id in production_slot.assigned_partner_ids:
                production_slot.assigned_partner_ids = []
        for pond in player.ponds:
            if pond.pending_partner_ids and partner_id in pond.pending_partner_ids:
                raise GameError(
                    "partner_swap_reserved",
                    f"{self._partner_name(partner_id)}已经预约了{self._pond_name(pond)}，"
                    "先在那口塘换个安排再说",
                    409,
                )
            if partner_id in pond.assigned_partner_ids:
                self._require_open_swap_window(self._pond_cycle_progress(pond), self._pond_name(pond))
                pond.assigned_partner_ids = []
                pond.pending_partner_ids = None
                self._refresh_pond_trait_snapshot(player, pond, now)
        for facility in player.livestock_facilities:
            if facility.pending_partner_ids and partner_id in facility.pending_partner_ids:
                raise GameError(
                    "partner_swap_reserved",
                    f"{self._partner_name(partner_id)}已经预约了{self._facility_name(facility)}，"
                    "先在那一栏换个安排再说",
                    409,
                )
            if partner_id in facility.assigned_partner_ids:
                self._require_open_swap_window(
                    self._facility_cycle_progress(player, facility), self._facility_name(facility)
                )
                # 被别处挖走也要立刻抹掉特性快照，否则这一栏会白拿一段的加成。
                facility.assigned_partner_ids = []
                facility.pending_partner_ids = None
                self._refresh_livestock_trait_snapshot(player, facility, now)

    # ------------------------------------------------- 资产格换人：自由窗口与排队

    def _partner_name(self, partner_id: str) -> str:
        definition = self.partner_catalog_loader().partner_map.get(partner_id)
        return definition.name if definition else partner_id

    def _pond_name(self, pond: PondState) -> str:
        definition = self._pond_definition(pond)
        return definition.name if definition else pond.pond_id

    def _facility_name(self, facility: LivestockFacilityState) -> str:
        definition = self.content.livestock_facility_map.get(facility.facility_id)
        return definition.name if definition else facility.facility_id

    def _pond_cycle_progress(self, pond: PondState) -> float:
        """这口塘当前周期跑了多少。空塘和停摆的塘没有在跑的周期，随时可以换人。"""

        if pond.empty or pond.stalled or pond.cycle_seconds <= 0:
            return 0.0
        return min(1.0, pond.settle_remainder / pond.cycle_seconds)

    def _facility_cycle_progress(self, player: PlayerState, facility: LivestockFacilityState) -> float:
        """这一栏当前周期跑了多少。空栏和停摆的栏没有在跑的周期，随时可以换人。"""

        cycle = self.content.livestock.cycle_seconds if self.content.livestock else 0
        if cycle <= 0 or facility.stalled or not self._animals_in(player, facility.facility_id):
            return 0.0
        return min(1.0, facility.settle_remainder / cycle)

    def _pond_next_cycle_seconds(self, pond: PondState) -> int:
        if pond.empty or pond.cycle_seconds <= 0:
            return 0
        return max(0, pond.cycle_seconds - pond.settle_remainder)

    def _facility_next_cycle_seconds(self, facility: LivestockFacilityState) -> int:
        cycle = self.content.livestock.cycle_seconds if self.content.livestock else 0
        return max(0, cycle - facility.settle_remainder)

    def _swap_window_seconds(self, cycle_seconds: int) -> int:
        """自由窗口有多长，前端要拿它写"开工 N 分钟内还能换人"。"""

        return int(max(0, cycle_seconds) * self.content.asset_partner_free_window)

    def _swap_window_is_open(self, progress: float) -> bool:
        return progress <= self.content.asset_partner_free_window + 1e-9

    def _require_open_swap_window(self, progress: float, name: str) -> None:
        if not self._swap_window_is_open(progress):
            raise GameError(
                "partner_cycle_locked",
                f"{name}这个周期已经开工了，要等它跑完才能把人调走",
                409,
            )

    @staticmethod
    def _effective_partner_ids(slot) -> list[str]:
        """算编制时看的是【最终会在岗的人】：排队中的预约到了周期边界就会顶上去。"""

        pending = getattr(slot, "pending_partner_ids", None)
        return slot.assigned_partner_ids if pending is None else pending

    @staticmethod
    def _industry_assigned_count(player: PlayerState, industry: str) -> int:
        if industry == "farming":
            return sum(len(plot.assigned_partner_ids) for plot in player.plots)
        if industry == "gathering":
            return sum(len(site.assigned_partner_ids) for site in player.gathering_sites)
        if industry == "crafting":
            return sum(len(station.assigned_partner_ids) for station in player.crafting_stations) + len(player.refining.assigned_partner_ids)
        if industry == "mining":
            return sum(len(site.assigned_partner_ids) for site in player.mining_sites)
        if industry == "aquatic":
            # 陪钓不占编制（不锁定、瞬时完成），驻场在鱼塘的才算。
            return sum(len(GameService._effective_partner_ids(pond)) for pond in player.ponds)
        if industry == "livestock":
            return sum(
                len(GameService._effective_partner_ids(facility))
                for facility in player.livestock_facilities
            )
        return 0

    def _industry_partner_capacity(self, player: PlayerState, industry: str) -> int:
        rules = self.content.industries[industry]
        bonuses = sum(
            node.partner_capacity_bonus
            for node_id in player.talent_nodes
            if (node := self.content.talent_map.get(node_id)) is not None and node.industry == industry
        )
        return rules.partner_capacity + bonuses

    def _industry_ability_bonus(self, player: PlayerState, industry: str) -> int:
        return sum(
            node.global_ability_bonus
            for node_id in player.talent_nodes
            if (node := self.content.talent_map.get(node_id)) is not None and node.industry == industry
        )

    def _available_talent_points(self, player: PlayerState) -> int:
        spent = sum(
            node.cost
            for node_id in player.talent_nodes
            if (node := self.content.talent_map.get(node_id)) is not None
        )
        return max(0, player.level - 1 + player.bonus_talent_points - spent)

    def _reward_snapshot(self, reward) -> dict:
        return serialize_reward(reward, self.content, self.partner_catalog_loader())

    def _portal_snapshot(self, player: PlayerState) -> list[dict]:
        items = self.content.item_map
        progress_map = {entry.portal_id: entry for entry in player.portals}
        completed_ids = self._completed_portal_ids(player)
        portals = []
        for portal in self.content.portals:
            progress = progress_map.get(portal.id)
            unlocked = self._portal_unlocked(player, portal)
            tributes = []
            for tribute in portal.tributes:
                recorded = progress.tribute(tribute.id) if progress else None
                delivered = min(recorded.delivered, tribute.quantity) if recorded else 0
                item = items.get(tribute.item_id)
                owned = self._eligible_quantity(player, tribute)
                remaining = max(0, tribute.quantity - delivered)
                tributes.append({
                    "id": tribute.id,
                    "item_id": tribute.item_id,
                    "name": item.name if item else tribute.item_id,
                    "icon": item.icon if item else "package",
                    "quantity": tribute.quantity,
                    "min_quality": tribute.min_quality or None,
                    "min_quality_name": QUALITY_NAMES.get(tribute.min_quality),
                    "delivered": delivered,
                    "remaining": remaining,
                    # 一次能交多少：手里够格的和还缺的取小。
                    "deliverable": min(owned, remaining),
                    "owned": owned,
                    "completed": bool(recorded and recorded.completed_at),
                    "reward": self._reward_snapshot(tribute.reward),
                })
            completed_count = sum(1 for entry in tributes if entry["completed"])
            portals.append({
                "portal_id": portal.id,
                "name": portal.name,
                "description": portal.description,
                "accent": portal.accent,
                "min_level": portal.min_level,
                "prerequisites": [
                    {
                        "portal_id": entry,
                        "name": self.content.portal_map[entry].name if entry in self.content.portal_map else entry,
                        "completed": entry in completed_ids,
                    }
                    for entry in portal.prerequisites
                ],
                "unlocked": unlocked,
                "locked_reason": None if unlocked else self._portal_locked_reason(player, portal),
                "completed": bool(progress and progress.completed_at),
                "completed_at": progress.completed_at if progress else 0,
                "tributes": tributes,
                "tribute_count": len(tributes),
                "completed_tribute_count": completed_count,
                "completion_reward": self._reward_snapshot(portal.completion_reward),
            })
        return portals

    def _talent_snapshot(self, player: PlayerState) -> dict:
        available_points = self._available_talent_points(player)
        nodes = []
        for node in self.content.talents:
            if not self._content_visible(player, node):
                continue
            unlocked = node.id in player.talent_nodes
            prerequisites_met = all(entry in player.talent_nodes for entry in node.prerequisites)
            locked_reason = None
            if not unlocked and player.level < node.min_level:
                locked_reason = f"等级 {node.min_level} 解锁"
            elif not unlocked and not prerequisites_met:
                locked_reason = "需要前置天赋"
            elif not unlocked and available_points < node.cost:
                locked_reason = "天赋点不足"
            nodes.append({
                **node.model_dump(),
                "unlocked": unlocked,
                "can_unlock": not unlocked and locked_reason is None,
                "locked_reason": locked_reason,
            })
        return {
            "earned_points": max(0, player.level - 1),
            "available_points": available_points,
            "unlocked_node_ids": list(player.talent_nodes),
            "nodes": nodes,
        }

    def _consume_recipe_inputs(self, player: PlayerState, recipe) -> list[TaskInputSnapshot]:
        consumed: list[TaskInputSnapshot] = []
        for requirement in recipe.inputs:
            qualities = player.inventory.get(requirement.item_id, {})
            if sum(qualities.values()) < requirement.quantity:
                item = self.content.item_map.get(requirement.item_id)
                raise GameError("resource_insufficient", f"{item.name if item else requirement.item_id}数量不足")
            remaining = requirement.quantity
            for quality, owned in sorted(qualities.items()):
                amount = min(owned, remaining)
                if amount <= 0:
                    continue
                remove_item(player, requirement.item_id, amount, quality)
                consumed.append(TaskInputSnapshot(
                    item_id=requirement.item_id,
                    quality=quality,
                    quantity=amount,
                ))
                remaining -= amount
                if remaining == 0:
                    break
        return consumed

    def _recipe_snapshot(self, player: PlayerState, recipe) -> dict:
        condition = recipe.unlock_condition
        unlocked = evaluate_recipe_unlock(player, condition.hook, condition.params)
        inputs = []
        ingredients_available = True
        for requirement in recipe.inputs:
            item = self.content.item_map[requirement.item_id]
            owned_by_quality = player.inventory.get(requirement.item_id, {})
            owned_quantity = sum(owned_by_quality.values())
            ingredients_available = ingredients_available and owned_quantity >= requirement.quantity
            inputs.append({
                **requirement.model_dump(),
                "item": item.model_dump(),
                "owned_quantity": owned_quantity,
                "owned_by_quality": dict(sorted(owned_by_quality.items())),
            })
        return {
            **recipe.model_dump(),
            "item": self.content.item_map[recipe.produce_item_id].model_dump(),
            "inputs": inputs,
            "unlocked": unlocked,
            "unlock_description": describe_recipe_unlock(condition.hook, condition.params),
            "ingredients_available": ingredients_available,
        }

    def _normalize_inventory_quality(self, player: PlayerState) -> None:
        for item_id, qualities in list(player.inventory.items()):
            item = self.content.item_map.get(item_id)
            if item and item.has_quality and qualities.get(0, 0) > 0:
                qualities[1] = qualities.get(1, 0) + qualities.pop(0)
            for quality, quantity in list(qualities.items()):
                if quantity == 0:
                    qualities.pop(quality)
            if not qualities:
                player.inventory.pop(item_id)

    def _resolve_output(self, production_slot, now: int, fallback_content=None) -> None:
        task = production_slot.task_snapshot
        if task:
            item_id = task.produce_item_id
            yield_min = task.yield_min
            yield_max = task.yield_max
            probabilities = task.quality_parameters.probabilities
        elif fallback_content:
            item_id = fallback_content.produce_item_id
            yield_min = fallback_content.yield_min
            yield_max = fallback_content.yield_max
            probabilities = [1, 0, 0, 0, 0]
        else:
            return
        quantity = self.rng.randint(yield_min, yield_max)
        results = build_results(
            self.rng,
            [(item_id, quantity)],
            probabilities,
            now,
            task.applied_effects if task else (),
        )
        item = self.content.item_map.get(item_id)
        if item is not None and not item.has_quality:
            # 无品质产物（加工出来的装备）掷出来的品质不作数，统一收敛到 0 号格。
            results = [
                ProductionResultSnapshot(
                    item_id=item_id,
                    quantity=sum(result.quantity for result in results),
                    quality=0,
                    resolved_at=now,
                )
            ]
        production_slot.task_results = results

    def _resolve_gathering_outputs(self, site: GatheringSiteState, now: int) -> None:
        task = site.task_snapshot
        if task is None:
            return
        output_pool = task.output_pool or [TaskOutputSnapshot(
            item_id=task.produce_item_id,
            weight=1,
            chance=1,
            quantity_min=task.yield_min,
            quantity_max=task.yield_max,
        )]
        if task.draw_count and any(output.weight > 0 for output in output_pool):
            batches = draw_weighted_batches(
                self.rng,
                output_pool,
                task.draw_count,
                task.applied_effects,
            )
        else:
            batches = [
                (output.item_id, self.rng.randint(output.quantity_min, output.quantity_max))
                for output in output_pool
                if output.chance == 1 or self.rng.random() < output.chance
            ]
        site.task_results = build_results(
            self.rng,
            batches,
            task.quality_parameters.probabilities,
            now,
            task.applied_effects,
        )
        if task.season_bonus:
            for result, quantity in zip(site.task_results, scale_quantities(
                [entry.quantity for entry in site.task_results], task.season_bonus.multiplier,
            )):
                result.quantity = quantity

    def _result_snapshot(self, result: ProductionResultSnapshot) -> dict:
        item = self.content.item_map.get(result.item_id)
        return {
            **result.model_dump(),
            "quality_name": QUALITY_NAMES.get(result.quality),
            "item": item.model_dump() if item else None,
        }

    def _quality_unit_price(self, base_price: int, quality: int) -> int:
        grade = self.content.quality.grade_map.get(quality)
        multiplier = grade.sale_multiplier if grade else 1
        return floor(base_price * multiplier + 0.5)

    @staticmethod
    def _admin_player_summary(player: PlayerState) -> dict:
        return {
            "player_id": player.player_id,
            "display_name": player.display_name,
            "level": player.level,
            "experience": player.experience,
            "coins": player.coins,
            "maple_flame": player.maple_flame,
            "owned_partner_ids": [entry.partner_id for entry in player.owned_partners],
            "updated_at": player.updated_at,
        }

    def _now(self) -> int:
        return int(self.clock())
