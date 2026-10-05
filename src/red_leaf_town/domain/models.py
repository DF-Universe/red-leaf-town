from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .sailing import SailingState
from .refining import RefiningRoomState


RENAMED_PARTNER_IDS: dict[str, str] = {"sprite_001": "fein"}
LEGACY_PARTNER_LEVEL_COST_BASE = 20
LEGACY_PARTNER_LEVEL_COST_GROWTH = 5
LEGACY_PARTNER_EXPERIENCE_DIVISOR = 12
PARTNER_LEVEL_CAPS = (20, 40, 60)
LEGACY_TASK_STAMINA_COSTS: dict[tuple[str, str], int] = {
    ("crafting", "saw_maple_plank"): 2,
    ("crafting", "make_herbal_salve"): 2,
    ("crafting", "mill_flour"): 2,
    ("crafting", "pickle_carrot"): 2,
    ("mining", "mine_red_copper"): 1,
    ("mining", "mine_moon_silver"): 2,
}
LEGACY_MINING_DURATION_CAPS: dict[str, int] = {
    "mine_red_copper": 12 * 60,
    "mine_moon_silver": 24 * 60,
}


def _legacy_partner_total_experience(level: int, experience: int = 0) -> int:
    completed_levels = max(0, int(level) - 1)
    return (
        completed_levels * LEGACY_PARTNER_LEVEL_COST_BASE
        + LEGACY_PARTNER_LEVEL_COST_GROWTH * completed_levels * (completed_levels - 1) // 2
        + max(0, int(experience))
    )


def _partner_progress_from_experience(total_experience: int, breakthrough: int) -> tuple[int, int]:
    remaining = max(0, int(total_experience))
    level = 1
    level_cap = PARTNER_LEVEL_CAPS[max(0, min(2, int(breakthrough)))]
    while level < level_cap:
        cost = LEGACY_PARTNER_LEVEL_COST_BASE + LEGACY_PARTNER_LEVEL_COST_GROWTH * (level - 1)
        if remaining < cost:
            break
        remaining -= cost
        level += 1
    return level, remaining


def _migrate_partner_progress(partner: dict) -> None:
    total = _legacy_partner_total_experience(partner.get("level", 1), partner.get("experience", 0))
    level, experience = _partner_progress_from_experience(
        total // LEGACY_PARTNER_EXPERIENCE_DIVISOR,
        partner.get("breakthrough", 0),
    )
    partner["level"] = level
    partner["experience"] = experience


def _migrate_task_snapshot(snapshot: dict) -> None:
    industry = str(snapshot.get("industry") or "")
    content_id = str(snapshot.get("content_id") or "")
    snapshot["stamina_cost"] = LEGACY_TASK_STAMINA_COSTS.get((industry, content_id), 0)
    snapshot["rule_version"] = 2
    for partner in snapshot.get("partner_snapshots") or []:
        if not isinstance(partner, dict):
            continue
        level, _ = _partner_progress_from_experience(
            _legacy_partner_total_experience(partner.get("level", 1)) // LEGACY_PARTNER_EXPERIENCE_DIVISOR,
            partner.get("breakthrough", 0),
        )
        effective_level, _ = _partner_progress_from_experience(
            _legacy_partner_total_experience(partner.get("effective_level", 1))
            // LEGACY_PARTNER_EXPERIENCE_DIVISOR,
            partner.get("breakthrough", 0),
        )
        partner["level"] = level
        partner["effective_level"] = min(level, effective_level)
    duration_cap = LEGACY_MINING_DURATION_CAPS.get(content_id)
    if industry == "mining" and duration_cap:
        final_duration = min(int(snapshot.get("final_duration", duration_cap)), duration_cap)
        snapshot["base_duration"] = duration_cap
        snapshot["final_duration"] = final_duration
        snapshot["minimum_duration"] = min(int(snapshot.get("minimum_duration", 1)), final_duration)
        snapshot["ready_at"] = int(snapshot.get("started_at", 0)) + final_duration


class TaskPartnerSnapshot(BaseModel):
    partner_id: str = Field(min_length=1)
    level: int = Field(ge=1, le=60)
    effective_level: int = Field(ge=1, le=60)
    breakthrough: int = Field(ge=0, le=2)
    ability: int = Field(ge=0)


class TaskInputSnapshot(BaseModel):
    item_id: str = Field(min_length=1)
    quality: int = Field(ge=0, le=5)
    quantity: int = Field(ge=1)


class TaskOutputSnapshot(BaseModel):
    """产出池的一项。weight 用于加权抽取；chance 只存在于旧快照里，按独立掉率结算。"""

    item_id: str = Field(min_length=1)
    weight: float = Field(default=0, ge=0)
    chance: float = Field(default=0, ge=0, le=1)
    quantity_min: int = Field(ge=1)
    quantity_max: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_quantity(self):
        if self.quantity_max < self.quantity_min:
            raise ValueError("task output quantity_max must be greater than or equal to quantity_min")
        if self.weight <= 0 and self.chance <= 0:
            raise ValueError("task output must carry either a draw weight or a legacy chance")
        return self


class TaskQualitySnapshot(BaseModel):
    ability: int = Field(default=0, ge=0)
    thresholds: list[float] = Field(default_factory=lambda: [1, 2, 3, 4], min_length=4, max_length=4)
    width: float = Field(default=1, gt=0)
    miracle_probability_cap: float = Field(default=0, ge=0, le=0.01)
    miracle_eligible: bool = False
    miracle_width_multiplier: float = Field(default=1, ge=1)
    miracle_cap_ignored: bool = False
    probabilities: list[float] = Field(default_factory=lambda: [1, 0, 0, 0, 0], min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_quality_parameters(self):
        if self.thresholds != sorted(self.thresholds) or len(set(self.thresholds)) != 4:
            raise ValueError("quality thresholds must be strictly increasing")
        if any(probability < 0 or probability > 1 for probability in self.probabilities):
            raise ValueError("quality probabilities must be between zero and one")
        if abs(sum(self.probabilities) - 1.0) > 1e-8:
            raise ValueError("quality probabilities must sum to one")
        return self


class SeasonBonusSnapshot(BaseModel):
    season_id: str = Field(min_length=1)
    season_name: str
    partner_id: str = Field(min_length=1)
    multiplier: float = Field(ge=1, le=10, allow_inf_nan=False)
    label: str = ""


class ProductionTaskSnapshot(BaseModel):
    season_bonus: SeasonBonusSnapshot | None = None
    rule_version: int = Field(default=2, ge=1)
    industry: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    production_slot_id: str = Field(min_length=1)
    world_day: str = ""
    weather_id: str = ""
    started_at: int = Field(ge=0)
    ready_at: int = Field(ge=0)
    assigned_partner_ids: list[str] = Field(default_factory=list, max_length=2)
    support_partner_ids: list[str] = Field(default_factory=list)
    partner_snapshots: list[TaskPartnerSnapshot] = Field(default_factory=list, max_length=2)
    applied_effects: list[dict[str, object]] = Field(default_factory=list)
    character_ability: int = Field(ge=0)
    total_ability: int = Field(ge=0)
    time_efficiency: float = Field(ge=1, le=3)
    yield_efficiency: float = Field(default=1, ge=1)
    base_duration: int = Field(gt=0)
    minimum_duration: int = Field(default=1, gt=0)
    final_duration: int = Field(gt=0)
    stamina_cost: int = Field(default=0, ge=0)
    produce_item_id: str = Field(min_length=1)
    yield_min: int = Field(ge=1)
    yield_max: int = Field(ge=1)
    harvest_xp: int = Field(ge=0)
    consumed_inputs: list[TaskInputSnapshot] = Field(default_factory=list)
    output_pool: list[TaskOutputSnapshot] = Field(default_factory=list, max_length=5)
    draw_count: int = Field(default=0, ge=0, le=60)
    quality_parameters: TaskQualitySnapshot = Field(default_factory=TaskQualitySnapshot)

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.ready_at != self.started_at + self.final_duration:
            raise ValueError("task ready_at must equal started_at plus final_duration")
        if self.final_duration > self.base_duration:
            raise ValueError("task final_duration cannot exceed base_duration")
        if self.minimum_duration > self.final_duration:
            raise ValueError("task final_duration cannot be shorter than minimum_duration")
        if self.yield_max < self.yield_min:
            raise ValueError("task yield_max must be greater than or equal to yield_min")
        if self.assigned_partner_ids != [entry.partner_id for entry in self.partner_snapshots]:
            raise ValueError("task partner snapshots must match assigned partner ids")
        output_ids = [entry.item_id for entry in self.output_pool]
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("task output pool items must be unique")
        if self.draw_count and self.output_pool and not any(entry.weight > 0 for entry in self.output_pool):
            raise ValueError("weighted task must carry an output pool with positive weights")
        if self.output_pool and not self.draw_count and not any(entry.chance == 1 for entry in self.output_pool):
            raise ValueError("legacy chance pool must contain a guaranteed output")
        return self


class ProductionResultSnapshot(BaseModel):
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    # 0 表示这件产物不进品质系统（加工出来的装备），1~5 是正常的五档。
    quality: int = Field(ge=0, le=5)
    resolved_at: int = Field(ge=0)


class PlotState(BaseModel):
    slot: int = Field(ge=0)
    crop_id: str = ""
    planted_at: int = 0
    ready_at: int = 0
    assigned_partner_ids: list[str] = Field(default_factory=list, max_length=1)
    task_snapshot: ProductionTaskSnapshot | None = None
    task_results: list[ProductionResultSnapshot] = Field(default_factory=list, max_length=25)

    @property
    def empty(self) -> bool:
        return not self.crop_id


class GatheringSiteState(BaseModel):
    site_id: str = Field(min_length=1)
    assigned_partner_ids: list[str] = Field(default_factory=list, max_length=1)
    task_snapshot: ProductionTaskSnapshot | None = None
    task_results: list[ProductionResultSnapshot] = Field(default_factory=list, max_length=25)

    @property
    def empty(self) -> bool:
        return self.task_snapshot is None


class CompletedCraftingTask(BaseModel):
    task_snapshot: ProductionTaskSnapshot
    task_results: list[ProductionResultSnapshot] = Field(default_factory=list, max_length=25)


class CraftingStationState(BaseModel):
    station_id: str = Field(min_length=1)
    assigned_partner_ids: list[str] = Field(default_factory=list, max_length=1)
    task_snapshot: ProductionTaskSnapshot | None = None
    task_results: list[ProductionResultSnapshot] = Field(default_factory=list, max_length=25)

    # Future tasks hold reserved resources; the current task has already consumed its inputs.
    queued_tasks: list[ProductionTaskSnapshot] = Field(default_factory=list, max_length=98)
    completed_tasks: list[CompletedCraftingTask] = Field(default_factory=list, max_length=99)
    queue_total: int = Field(default=0, ge=0, le=99)
    collected_count: int = Field(default=0, ge=0, le=99)

    @property
    def empty(self) -> bool:
        return self.task_snapshot is None


class MiningSiteState(BaseModel):
    site_id: str = Field(min_length=1)
    assigned_partner_ids: list[str] = Field(default_factory=list, max_length=1)
    task_snapshot: ProductionTaskSnapshot | None = None
    task_results: list[ProductionResultSnapshot] = Field(default_factory=list, max_length=25)

    @property
    def empty(self) -> bool:
        return self.task_snapshot is None


class SlotState(BaseModel):
    """饲料槽/肥料槽的状态。quality_score 是存量加权平均，消耗只扣 units、不改分数。"""

    units: float = Field(default=0, ge=0)
    quality_score: float = Field(default=0, ge=0)
    updated_at: int = Field(default=0, ge=0)


class FryBatchState(BaseModel):
    """一批还没长成的鱼苗。

    剩余时间记的是【还差几个繁殖周期】而不是到期时间戳：周期本身随水产能力变化，
    换了更强的伙伴之后，塘里在长的这几批也应该跟着加速；饲料槽空了塘停摆，鱼苗
    同样停止计时。批次各自独立，先投的先成。
    """

    count: int = Field(gt=0)
    cycles_left: float = Field(gt=0)


class PondState(BaseModel):
    """资产轴生产格：没有完成时间，靠 last_settled_at 与参数快照分段推进。"""

    pond_id: str = Field(min_length=1)
    species_id: str = ""
    stock: int = Field(default=0, ge=0)
    fry: list[FryBatchState] = Field(default_factory=list, max_length=64)
    growth_remainder: float = Field(default=0, ge=0)
    generation_score: float = Field(default=0, ge=0)
    settle_remainder: int = Field(default=0, ge=0)
    last_settled_at: int = Field(default=0, ge=0)
    ability: int = Field(default=0, ge=0)
    cycle_seconds: int = Field(default=0, ge=0)
    cycle_multiplier: float = Field(default=1, gt=0)
    feed_multiplier: float = Field(default=1, ge=0)
    quality_bonus: float = 0
    generation_gain_bonus: float = 0
    trait_effects: list[dict[str, object]] = Field(default_factory=list)
    stalled: bool = False
    assigned_partner_ids: list[str] = Field(default_factory=list, max_length=1)
    # 排队中的换人：None 是没有排队，[] 是排队撤下，[id] 是排队换成这个人。
    # 过了自由窗口才会用到它，到下个周期开始时由结算搬进 assigned_partner_ids。
    pending_partner_ids: list[str] | None = Field(default=None, max_length=1)

    @property
    def fry_total(self) -> int:
        return sum(batch.count for batch in self.fry)

    @property
    def population(self) -> int:
        """占塘的总数：鱼苗也要占位置，否则鱼苗池会变成无限仓库。"""

        return self.stock + self.fry_total

    @property
    def empty(self) -> bool:
        return not self.species_id or self.population <= 0


class PendingBigCatchState(BaseModel):
    """抽中大物之后悬而未决的一次搏鱼。玩家要么追加体力搏一把，要么放弃拿回普通鱼。"""

    spot_id: str = Field(min_length=1)
    created_at: int = Field(ge=0)


class AnimalState(BaseModel):
    """一只动物。畜牧是个体建模，与鱼塘的聚合建模相反。

    进度全部记成【累计有效周期】而不是时间戳：断粮会让整栏停摆，用墙上时钟算会把
    停摆的那段白送给成长和冷却。
    """

    animal_id: str = Field(min_length=1)
    species_id: str = Field(min_length=1)
    facility_id: str = Field(min_length=1)
    # 玩家起的名字。留空就按物种名显示，不占存档也不影响任何数值。
    nickname: str = Field(default="", max_length=24)
    stage: Literal["incubating", "juvenile", "adult"] = "juvenile"
    stage_cycles: float = Field(default=0, ge=0)
    quality_gene: int = Field(default=0, ge=0, le=100)
    yield_gene: int = Field(default=0, ge=0, le=100)
    affection: int = Field(default=0, ge=0, le=100)
    # 已产出未收取的量，按品质分桶。品质在结算当周期就掷定，收取时不再重掷。
    pending_output: dict[int, int] = Field(default_factory=dict)
    pending_special: int = Field(default=0, ge=0)
    yield_progress: float = Field(default=0, ge=0)
    breeding_cooldown: float = Field(default=0, ge=0)
    cared_on: str = ""
    cared_count: int = Field(default=0, ge=0)
    born_at: int = Field(default=0, ge=0)

    @property
    def pending_total(self) -> int:
        return sum(self.pending_output.values())

    @model_validator(mode="after")
    def validate_pending(self):
        for quality, amount in self.pending_output.items():
            if quality < 1 or quality > 5:
                raise ValueError(f"animal {self.animal_id} holds an invalid quality")
            if amount < 0:
                raise ValueError(f"animal {self.animal_id} holds a negative amount")
        self.pending_output = {
            quality: amount for quality, amount in self.pending_output.items() if amount > 0
        }
        return self


class LivestockFacilityState(BaseModel):
    """资产轴生产格。畜牧周期是全局常量，所以这里只需要记余数、结算点和伙伴参数快照。

    特性参数是【快照】而不是现算：干跑要先报饲料需求量，鱼塘和畜栏才能按同一个比例
    分预算。饲料乘算如果等到推进时才算，需求量和实际消耗就对不上了。
    """

    facility_id: str = Field(min_length=1)
    tier: int = Field(default=1, ge=1)
    settle_remainder: int = Field(default=0, ge=0)
    last_settled_at: int = Field(default=0, ge=0)
    stalled: bool = False
    quality_bonus: float = 0
    feed_multiplier: float = Field(default=1, ge=0)
    overflow_bonus: int = Field(default=0, ge=0)
    special_chance_bonus: float = Field(default=0, ge=0)
    affection_quality_bonus: float = Field(default=0, ge=0)
    trait_effects: list[dict[str, object]] = Field(default_factory=list)
    assigned_partner_ids: list[str] = Field(default_factory=list, max_length=1)
    # 见 PondState.pending_partner_ids。
    pending_partner_ids: list[str] | None = Field(default=None, max_length=1)


class FishingState(BaseModel):
    spot_id: str = ""
    combo: int = Field(default=0, ge=0, le=60)
    combo_updated_at: int = Field(default=0, ge=0)
    last_cast_at: int = Field(default=0, ge=0)
    companion_partner_id: str = ""
    pending_big_catch: PendingBigCatchState | None = None
    recent_request_ids: list[str] = Field(default_factory=list, max_length=8)


class FishCodexEntry(BaseModel):
    item_id: str = Field(min_length=1)
    caught: int = Field(default=1, ge=1)
    first_caught_at: int = Field(ge=0)
    max_size: float = Field(default=0, ge=0)


class FishCodexState(BaseModel):
    entries: list[FishCodexEntry] = Field(default_factory=list, max_length=200)
    claimed_milestones: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_codex(self):
        item_ids = [entry.item_id for entry in self.entries]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("fish codex cannot record the same species twice")
        if len(self.claimed_milestones) != len(set(self.claimed_milestones)):
            raise ValueError("fish codex cannot claim the same milestone twice")
        return self

    def entry(self, item_id: str) -> FishCodexEntry | None:
        return next((entry for entry in self.entries if entry.item_id == item_id), None)


class OwnedPartnerState(BaseModel):
    partner_id: str = Field(min_length=1)
    level: int = Field(default=1, ge=1, le=60)
    experience: int = Field(default=0, ge=0)
    breakthrough: int = Field(default=0, ge=0, le=2)
    artwork_stage: int | None = Field(default=None, ge=0, le=2)
    stars: int = Field(default=0, ge=0, le=5)
    acquired_at: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_id(cls, value):
        if not isinstance(value, dict) or "partner_id" in value:
            return value
        migrated = dict(value)
        if "spirit_id" in migrated:
            migrated["partner_id"] = migrated.pop("spirit_id")
        return migrated


class GachaDropRecord(BaseModel):
    kind: Literal["partner", "task_item"]
    content_id: str = Field(min_length=1)
    rarity: int | None = Field(default=None, ge=3, le=5)
    duplicate: bool = False
    quantity: int = Field(default=1, ge=1)
    companion_marks: int = Field(default=0, ge=0)


class GachaRequestRecord(BaseModel):
    request_id: str = Field(min_length=8, max_length=128)
    pool_id: str = Field(min_length=1)
    count: Literal[1, 10]
    created_at: int = Field(ge=0)
    results: list[GachaDropRecord] = Field(min_length=1, max_length=10)


class GachaPoolProgressState(BaseModel):
    """玩家在某一个招募池里的进度：累计抽数、四星/五星保底计数器。按 pool_id 分别记录。"""

    total_pulls: int = Field(default=0, ge=0)
    four_pity: int = Field(default=0, ge=0)
    five_pity: int = Field(default=0, ge=0)


def _rename_partner_ids(migrated: dict) -> dict:
    """伙伴改 ID 之后，存档里所有引用它的地方都要跟着改，包括进行中的任务快照。"""
    migrated = deepcopy(migrated)
    for owned in migrated.get("owned_partners") or []:
        if isinstance(owned, dict) and owned.get("partner_id") in RENAMED_PARTNER_IDS:
            owned["partner_id"] = RENAMED_PARTNER_IDS[owned["partner_id"]]
    slots = [
        production_slot
        for key in ("plots", "gathering_sites", "crafting_stations", "mining_sites")
        for production_slot in (migrated.get(key) or [])
        if isinstance(production_slot, dict)
    ]
    for production_slot in slots:
        production_slot["assigned_partner_ids"] = _renamed_list(production_slot.get("assigned_partner_ids"))
        task = production_slot.get("task_snapshot")
        if not isinstance(task, dict):
            continue
        task["assigned_partner_ids"] = _renamed_list(task.get("assigned_partner_ids"))
        task["support_partner_ids"] = _renamed_list(task.get("support_partner_ids"))
        for snapshot in task.get("partner_snapshots") or []:
            if isinstance(snapshot, dict) and snapshot.get("partner_id") in RENAMED_PARTNER_IDS:
                snapshot["partner_id"] = RENAMED_PARTNER_IDS[snapshot["partner_id"]]
    return migrated


def _renamed_list(partner_ids) -> list[str]:
    return [RENAMED_PARTNER_IDS.get(partner_id, partner_id) for partner_id in (partner_ids or [])]


class PortalTributeProgress(BaseModel):
    """一项贡品的交付进度。completed_at 非零表示这一项的奖励已经结算过。"""

    tribute_id: str = Field(min_length=1)
    delivered: int = Field(default=0, ge=0)
    completed_at: int = Field(default=0, ge=0)


class PortalProgressState(BaseModel):
    portal_id: str = Field(min_length=1)
    tributes: list[PortalTributeProgress] = Field(default_factory=list, max_length=12)
    completed_at: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_progress(self):
        tribute_ids = [entry.tribute_id for entry in self.tributes]
        if len(tribute_ids) != len(set(tribute_ids)):
            raise ValueError(f"portal {self.portal_id} records the same tribute twice")
        return self

    def tribute(self, tribute_id: str) -> PortalTributeProgress | None:
        return next((entry for entry in self.tributes if entry.tribute_id == tribute_id), None)


CommissionStatus = Literal["open", "forwarded", "completed", "forward_completed"]


class CommissionState(BaseModel):
    """本人当天的委托。day 是按刷新时区算出来的自然日，换日时整条重掷。"""

    day: str = Field(min_length=1)
    commission_id: str = Field(min_length=8, max_length=64)
    npc_id: str = Field(min_length=1)
    npc_name: str = Field(min_length=1)
    npc_title: str = ""
    line: str = ""
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    tier: int = Field(ge=1, le=9)
    lucky: bool = False
    reward_maple_flame: int = Field(ge=0)
    status: CommissionStatus = "open"
    forwarded_at: int = Field(default=0, ge=0)
    completed_at: int = Field(default=0, ge=0)
    completed_by_name: str = ""

    @model_validator(mode="after")
    def validate_commission(self):
        if self.status == "forwarded" and not self.forwarded_at:
            raise ValueError("forwarded commission must record when it was forwarded")
        if self.status in ("completed", "forward_completed") and not self.completed_at:
            raise ValueError("completed commission must record when it was completed")
        return self

    @property
    def settled(self) -> bool:
        return self.status in ("completed", "forward_completed")


class TakenCommissionRecord(BaseModel):
    """接走并完成的别人的委托。按天限次靠这份流水判断。"""

    day: str = Field(min_length=1)
    commission_id: str = Field(min_length=8, max_length=64)
    owner_name: str = ""
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    reward_maple_flame: int = Field(ge=0)
    completed_at: int = Field(ge=0)


class CommissionBoardEntry(BaseModel):
    """公共转发池里的一条。发布之后内容不再改动，是否被接走由另一张表记录。"""

    commission_id: str = Field(min_length=8, max_length=64)
    day: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    owner_name: str = ""
    npc_name: str = ""
    npc_title: str = ""
    line: str = ""
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    tier: int = Field(ge=1, le=9)
    lucky: bool = False
    reward_maple_flame: int = Field(ge=0)
    owner_reward: int = Field(ge=0)
    taker_reward: int = Field(ge=0)
    forwarded_at: int = Field(ge=0)


class CommissionPayout(BaseModel):
    """别人替我完成之后回给我的那一份。收件人下次读档时入账。"""

    commission_id: str = Field(min_length=8, max_length=64)
    day: str = Field(min_length=1)
    maple_flame: int = Field(ge=0)
    taker_name: str = ""
    item_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    completed_at: int = Field(ge=0)


class MailReceiptState(BaseModel):
    """一封信在我这边的状态。信本身存在公共信箱里，这里只记我读没读过、附件领没领过。"""

    mail_id: str = Field(min_length=8, max_length=64)
    read_at: int = Field(default=0, ge=0)
    claimed_at: int = Field(default=0, ge=0)


class AchievementCompletionState(BaseModel):
    achievement_id: str = Field(min_length=1)
    completed_at: int = Field(ge=0)
    claimed_at: int = Field(default=0, ge=0)


class AchievementStats(BaseModel):
    mined_task_ids: list[str] = Field(default_factory=list)
    refining_batches: dict[int, int] = Field(default_factory=dict)
    gathering_items: dict[str, list[str]] = Field(default_factory=dict)
    livestock_item_ids: list[str] = Field(default_factory=list)
    bred_species_ids: list[str] = Field(default_factory=list)
    sailing_route_ids: list[str] = Field(default_factory=list)
    completed_expedition_ids: list[str] = Field(default_factory=list)
    completed_delve_ids: list[str] = Field(default_factory=list)
    delve_wins: dict[str, int] = Field(default_factory=dict)
    production_collections: dict[str, int] = Field(default_factory=dict)
    harvested_crop_ids: list[str] = Field(default_factory=list)
    crafted_recipe_ids: list[str] = Field(default_factory=list)
    own_commissions_completed: int = Field(default=0, ge=0)
    commissions_completed: int = Field(default=0, ge=0)
    pond_harvested: dict[str, int] = Field(default_factory=dict)
    max_production_quality: int = Field(default=0, ge=0, le=5)
    animals_bred: int = Field(default=0, ge=0)
    animals_cared: int = Field(default=0, ge=0)
    livestock_specials: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_sets_and_counts(self):
        if len(self.mined_task_ids) != len(set(self.mined_task_ids)):
            raise ValueError("achievement mining tasks must be unique")
        if any(q not in range(1, 6) or count < 0 for q, count in self.refining_batches.items()):
            raise ValueError("invalid refining achievement counts")
        if len(self.harvested_crop_ids) != len(set(self.harvested_crop_ids)):
            raise ValueError("achievement stats cannot record the same crop twice")
        if len(self.crafted_recipe_ids) != len(set(self.crafted_recipe_ids)):
            raise ValueError("achievement stats cannot record the same recipe twice")
        if any(count < 0 for count in self.production_collections.values()):
            raise ValueError("achievement production counts cannot be negative")
        if any(count < 0 for count in self.pond_harvested.values()):
            raise ValueError("achievement pond harvest counts cannot be negative")
        return self


class ExplorationEventLog(BaseModel):
    depth: int = Field(ge=1)
    event_id: str = Field(min_length=1)
    choice_id: str = Field(min_length=1)
    success: bool
    degree: Literal["automatic_success", "critical_failure", "failure", "success", "critical_success"] = "success"
    check_attribute: Literal["strength", "agility", "intelligence", "luck"] | None = None
    check_mode: Literal["best", "sum"] | None = None
    dice_mode: Literal["normal", "advantage", "disadvantage"] | None = None
    rolls: list[int] = Field(default_factory=list, max_length=2)
    kept_roll: int | None = Field(default=None, ge=1, le=20)
    modifier: int | None = None
    total: int | None = None
    actor_partner_ids: list[str] = Field(default_factory=list, max_length=3)
    text: str = Field(min_length=1)
    stamina_cost: int = Field(ge=0)
    rewards: list[ProductionResultSnapshot] = Field(default_factory=list, max_length=20)
    applied_effects: list[dict[str, object]] = Field(default_factory=list, max_length=20)


class DelveLoadoutSnapshot(BaseModel):
    """出发时冻结的一名伙伴的装备数值。存的是数值不是 item_id，所以本次探索期间
    把原物品卖了也不影响已经出发的队伍，不需要额外的库存占用逻辑。"""

    weapon_item_id: str = ""
    weapon_name: str = ""
    accessory_item_id: str = ""
    accessory_name: str = ""
    attack_attribute: Literal["strength", "agility", "intelligence"] = "strength"
    damage_dice: str = "1d4"
    attack_bonus: int = 0
    proficiency_bonus: int = 0
    armor_bonus: int = 0
    initiative_bonus: int = 0
    max_hp_bonus: int = 0
    advantage_uses: int = Field(default=0, ge=0, le=3)


class DelveMemberState(BaseModel):
    first_miss_reroll_ready: bool = False
    max_hp: int = Field(gt=0)
    hp: int = Field(ge=0)
    armor_class: int = Field(ge=1)
    initiative_bonus: int = 0

    @property
    def down(self) -> bool:
        return self.hp <= 0


class CarriedItemSnapshot(BaseModel):
    item_id: str = Field(min_length=1)
    quality: int = Field(ge=0, le=5)
    quantity: int = Field(ge=0)


class DelveEnemyState(BaseModel):
    key: str = Field(min_length=1)
    enemy_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    icon: str = ""
    max_hp: int = Field(gt=0)
    hp: int = Field(ge=0)
    armor_class: int = Field(ge=1)
    # 一回合出手几次。写进战斗快照是为了让前端在开打前就能标出来。
    attacks_per_turn: int = Field(default=1, ge=1, le=4)
    boss: bool = False


class DelveBattleLog(BaseModel):
    round: int = Field(ge=1)
    actor: str = Field(min_length=1)
    actor_name: str = Field(min_length=1)
    action: Literal["attack", "item", "flee", "advantage"]
    target: str = ""
    target_name: str = ""
    roll: int | None = None
    rolls: list[int] = Field(default_factory=list, max_length=3)
    modifier: int | None = None
    total: int | None = None
    hit: bool | None = None
    critical: bool = False
    damage: int = 0
    healing: int = 0
    text: str = Field(min_length=1)


class DelveBattleState(BaseModel):
    battle_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    choice_id: str = Field(min_length=1)
    depth: int = Field(ge=1)
    round: int = Field(default=1, ge=1)
    can_flee: bool = True
    flee_dc: int = Field(default=12, ge=2, le=30)
    enemies: list[DelveEnemyState] = Field(min_length=1, max_length=4)
    # 先攻定序的结果，元素是伙伴 id 或敌人 key，整场固定。
    order: list[str] = Field(min_length=1, max_length=7)
    turn_index: int = Field(default=0, ge=0)
    advantage_ready: dict[str, bool] = Field(default_factory=dict)
    advantage_uses_left: dict[str, int] = Field(default_factory=dict)
    logs: list[DelveBattleLog] = Field(default_factory=list, max_length=60)


class ExplorationRunState(BaseModel):
    season_pending_units: int = Field(default=0, ge=0)
    season_bonus: SeasonBonusSnapshot | None = None
    boss_defeated: bool = False
    talent_check_bonus: int = Field(default=0, ge=0)
    run_id: str = Field(min_length=1)
    expedition_id: str = Field(min_length=1)
    expedition_kind: Literal["transport", "survey", "delve"]
    status: Literal["active", "completed"] = "active"
    partner_ids: list[str] = Field(min_length=1, max_length=3)
    leader_partner_id: str = Field(min_length=1)
    exploration_ability: int = Field(ge=0)
    entry_fee: int = Field(gt=0)
    started_at: int = Field(ge=0)
    depth: int = Field(default=0, ge=0)
    current_event_id: str = Field(min_length=1)
    current_rolls: list[int] = Field(min_length=2, max_length=2)
    route_stamina_raw: int = Field(default=0, ge=0)
    action_stamina_spent: int = Field(default=0, ge=0)
    stamina_spent: int = Field(default=0, ge=0)
    next_route_discount: int = Field(default=0, ge=0, le=5)
    pending_rewards: list[ProductionResultSnapshot] = Field(default_factory=list, max_length=100)
    event_counts: dict[str, int] = Field(default_factory=dict)
    trait_usage: dict[str, int] = Field(default_factory=dict)
    logs: list[ExplorationEventLog] = Field(default_factory=list, max_length=20)
    # 以下四项只有探秘（delve）路线会用到，采运 run 一直保持空值。
    loadout: dict[str, DelveLoadoutSnapshot] = Field(default_factory=dict)
    combat_party: dict[str, DelveMemberState] = Field(default_factory=dict)
    carried_items: list[CarriedItemSnapshot] = Field(default_factory=list, max_length=6)
    # 冻结的装备类战利品。它们不进五档品质，所以和 pending_rewards 分开存。
    pending_fixed_rewards: list[CarriedItemSnapshot] = Field(default_factory=list, max_length=20)
    battle: DelveBattleState | None = None

    @model_validator(mode="after")
    def validate_run(self):
        if len(self.partner_ids) != len(set(self.partner_ids)):
            raise ValueError("exploration party cannot contain the same partner twice")
        if self.leader_partner_id not in self.partner_ids:
            raise ValueError("exploration leader must be in the party")
        if any(count < 0 for count in self.event_counts.values()):
            raise ValueError("exploration event counts cannot be negative")
        if any(count < 0 for count in self.trait_usage.values()):
            raise ValueError("exploration trait usage cannot be negative")
        if any(roll < 1 or roll > 20 for roll in self.current_rolls):
            raise ValueError("exploration rolls must be between 1 and 20")
        if set(self.loadout) - set(self.partner_ids):
            raise ValueError("exploration loadout cannot cover partners outside the party")
        if set(self.combat_party) - set(self.partner_ids):
            raise ValueError("exploration hit points cannot cover partners outside the party")
        return self


class SeasonProgressState(BaseModel):
    # 1800 units = one point; one gathering second = one unit. No rounding loss.
    units: int = Field(default=0, ge=0)
    claimed_thresholds: list[int] = Field(default_factory=list)


class PlayerState(BaseModel):
    schema_version: int = 35
    farm_upgrade_stage: int = Field(default=0, ge=0, le=3)
    feed_upgrade_stage: int = Field(default=0, ge=0, le=2)
    refining: RefiningRoomState = Field(default_factory=RefiningRoomState)
    season_progress: dict[str, SeasonProgressState] = Field(default_factory=dict)
    version: int = 1
    player_id: str
    oauth_sub: str
    display_name: str
    level: int = 1
    experience: int = 0
    coins: int = Field(default=0, ge=0)
    maple_flame: int = Field(default=0, ge=0)
    guide_leaves: int = Field(default=0, ge=0)
    companion_marks: int = Field(default=0, ge=0)
    stamina: int = Field(default=0, ge=0)
    stamina_updated_at: int
    inventory: dict[str, dict[int, int]] = Field(default_factory=dict)
    task_items: dict[str, int] = Field(default_factory=dict)
    plots: list[PlotState] = Field(default_factory=list)
    gathering_sites: list[GatheringSiteState] = Field(default_factory=list)
    crafting_stations: list[CraftingStationState] = Field(default_factory=list)
    mining_sites: list[MiningSiteState] = Field(default_factory=list)
    ponds: list[PondState] = Field(default_factory=list)
    livestock_facilities: list[LivestockFacilityState] = Field(default_factory=list)
    animals: list[AnimalState] = Field(default_factory=list, max_length=64)
    feed_slot: SlotState = Field(default_factory=SlotState)
    fishing: FishingState = Field(default_factory=FishingState)
    fish_codex: FishCodexState = Field(default_factory=FishCodexState)
    talent_nodes: list[str] = Field(default_factory=list)
    owned_partners: list[OwnedPartnerState] = Field(default_factory=list)
    seen_story_ids: list[str] = Field(default_factory=list)
    portals: list[PortalProgressState] = Field(default_factory=list)
    commission: CommissionState | None = None
    commission_takes: list[TakenCommissionRecord] = Field(default_factory=list, max_length=30)
    mail_receipts: list[MailReceiptState] = Field(default_factory=list, max_length=1000)
    bonus_talent_points: int = Field(default=0, ge=0)
    gacha_progress: dict[str, GachaPoolProgressState] = Field(default_factory=dict)
    gacha_history: list[GachaRequestRecord] = Field(default_factory=list)
    # 联动活动的一次性领取记录：campaign_id -> 领取时刻。存在这里而不是别处，是因为
    # 红叶镇存档和 OAuth 账号一一对应，账号级的「只能领一次」才不会被小号或换角色绕过。
    crossover_claims: dict[str, int] = Field(default_factory=dict)
    # 月卡。到期日是「最后一个能领奖励的自然日」，按委托的 4 点刷新口径算，空串代表没卡。
    monthly_card_expires_on: str = ""
    monthly_card_claimed_on: str = ""
    monthly_card_redeemed: int = Field(default=0, ge=0)
    # 当天用枫火买过几次体力。日期一换就重新计数。
    stamina_purchase_day: str = ""
    stamina_purchase_count: int = Field(default=0, ge=0)
    exploration_run: ExplorationRunState | None = None
    sailing: SailingState = Field(default_factory=SailingState)
    achievement_stats: AchievementStats = Field(default_factory=AchievementStats)
    achievements: list[AchievementCompletionState] = Field(default_factory=list)
    achievement_auto_rewards_reconciled: bool = True
    created_at: int
    updated_at: int

    @model_validator(mode="before")
    @classmethod
    def migrate_schema(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = deepcopy(value)
        schema_version = int(migrated.get("schema_version", 1))
        if schema_version < 3:
            legacy_partners = migrated.pop("owned_spirits", None)
            if "owned_partners" not in migrated and isinstance(legacy_partners, list):
                migrated["owned_partners"] = legacy_partners
            migrated.setdefault("owned_partners", [])
        if schema_version < 4:
            schema_version = 4
        inventory = migrated.get("inventory")
        if schema_version < 5 or (
            isinstance(inventory, dict)
            and any(not isinstance(quantity, dict) for quantity in inventory.values())
        ):
            migrated["inventory"] = {
                str(item_id): ({0: int(quantity)} if not isinstance(quantity, dict) else quantity)
                for item_id, quantity in (inventory or {}).items()
            }
        if schema_version < 6:
            migrated.setdefault("gathering_sites", [])
            migrated.setdefault("talent_nodes", [])
        if schema_version < 7:
            migrated.setdefault("crafting_stations", [])
        if schema_version < 8:
            migrated.setdefault("mining_sites", [])
        if schema_version < 9:
            migrated.setdefault("seen_story_ids", [])
        if schema_version < 10:
            migrated = _rename_partner_ids(migrated)
        if schema_version < 11:
            migrated.setdefault("portals", [])
            migrated.setdefault("bonus_talent_points", 0)
        if schema_version < 12:
            for site in migrated.get("gathering_sites") or []:
                if not isinstance(site, dict):
                    continue
                legacy_result = site.pop("task_result", None)
                site.setdefault("task_results", [legacy_result] if legacy_result else [])
        if schema_version < 13:
            for key in ("plots", "crafting_stations", "mining_sites"):
                for production_slot in migrated.get(key) or []:
                    if not isinstance(production_slot, dict):
                        continue
                    legacy_result = production_slot.pop("task_result", None)
                    production_slot.setdefault("task_results", [legacy_result] if legacy_result else [])
        if schema_version < 14:
            migrated.setdefault("maple_flame", 0)
            migrated.setdefault("guide_leaves", 0)
            migrated.setdefault("companion_marks", 0)
            migrated.setdefault("task_items", {})
            migrated.setdefault("gacha_total_pulls", 0)
            migrated.setdefault("gacha_four_pity", 0)
            migrated.setdefault("gacha_five_pity", 0)
            migrated.setdefault("gacha_history", [])
        if schema_version < 15:
            # 招募池从单一常驻池拆成多池，保底计数器改成按 pool_id 分开存。
            # 老存档的进度只可能来自当时唯一的常驻池，原样迁移过去。
            legacy_total = migrated.pop("gacha_total_pulls", 0)
            legacy_four = migrated.pop("gacha_four_pity", 0)
            legacy_five = migrated.pop("gacha_five_pity", 0)
            if legacy_total or legacy_four or legacy_five:
                migrated["gacha_progress"] = {
                    "standard-1": {
                        "total_pulls": legacy_total,
                        "four_pity": legacy_four,
                        "five_pity": legacy_five,
                    }
                }
            else:
                migrated.setdefault("gacha_progress", {})
        if schema_version < 16:
            migrated.setdefault("commission", None)
            migrated.setdefault("commission_takes", [])
        if schema_version < 17:
            for partner in migrated.get("owned_partners") or []:
                if isinstance(partner, dict):
                    _migrate_partner_progress(partner)
            for key in ("plots", "gathering_sites", "crafting_stations", "mining_sites"):
                for production_slot in migrated.get(key) or []:
                    if not isinstance(production_slot, dict):
                        continue
                    snapshot = production_slot.get("task_snapshot")
                    if isinstance(snapshot, dict):
                        _migrate_task_snapshot(snapshot)
        if schema_version < 18:
            migrated.setdefault("mail_receipts", [])
        if schema_version < 19:
            # 水产上线。两个资产轴字段和饲料槽都从空状态开始，旧存档不补发任何东西。
            migrated.setdefault("ponds", [])
            migrated.setdefault("feed_slot", {"units": 0, "quality_score": 0})
            migrated.setdefault("fishing", {})
            migrated.setdefault("fish_codex", {})
        if schema_version < 20:
            migrated.setdefault("crossover_claims", {})
        if schema_version < 21:
            for pond in migrated.get("ponds") or []:
                if not isinstance(pond, dict):
                    continue
                pond.setdefault("cycle_multiplier", 1)
                pond.setdefault("feed_multiplier", 1)
                pond.setdefault("quality_bonus", 0)
                pond.setdefault("generation_gain_bonus", 0)
                pond.setdefault("trait_effects", [])
        if schema_version < 22:
            own_commissions_completed = 0
            commission = migrated.get("commission")
            if isinstance(commission, dict) and commission.get("status") == "completed":
                own_commissions_completed = 1
            migrated.setdefault("achievement_stats", {
                "own_commissions_completed": own_commissions_completed,
                "commissions_completed": own_commissions_completed + len(migrated.get("commission_takes") or []),
            })
            migrated.setdefault("achievements", [])
        if schema_version < 23:
            for achievement in migrated.get("achievements") or []:
                if isinstance(achievement, dict):
                    achievement.setdefault("claimed_at", 0)
            # v22 曾在成就达成时自动发枫火；服务层拿到内容配置后会回收，并把它们恢复成待领取。
            migrated["achievement_auto_rewards_reconciled"] = False
        if schema_version < 24:
            # 畜牧上线。设施和动物都从空开始，散养地在 normalize 时按等级补发。
            migrated.setdefault("livestock_facilities", [])
            migrated.setdefault("animals", [])
        if schema_version < 25:
            # 畜牧特性上线，给已有畜栏补上中性参数；下一次结算会写回真实快照。
            for facility in migrated.get("livestock_facilities") or []:
                if not isinstance(facility, dict):
                    continue
                facility.setdefault("quality_bonus", 0)
                facility.setdefault("feed_multiplier", 1)
                facility.setdefault("overflow_bonus", 0)
                facility.setdefault("special_chance_bonus", 0)
                facility.setdefault("affection_quality_bonus", 0)
                facility.setdefault("trait_effects", [])
        if schema_version < 26:
            # 月卡上线。老存档一律当作没有过卡，也没有买过体力。
            migrated.setdefault("monthly_card_expires_on", "")
            migrated.setdefault("monthly_card_claimed_on", "")
            migrated.setdefault("monthly_card_redeemed", 0)
            migrated.setdefault("stamina_purchase_day", "")
            migrated.setdefault("stamina_purchase_count", 0)
        if schema_version < 27:
            migrated.setdefault("exploration_run", None)
        if schema_version < 28:
            run = migrated.get("exploration_run")
            if isinstance(run, dict) and "current_rolls" not in run:
                legacy_roll = float(run.pop("current_roll", 0))
                legacy_die = max(1, min(20, int(legacy_roll * 20) + 1))
                run["current_rolls"] = [legacy_die, legacy_die]
        if schema_version < 29:
            run = migrated.get("exploration_run")
            if isinstance(run, dict):
                run.setdefault("trait_usage", {})
        if schema_version < 30:
            # 探秘副本上线。采运的进行中路线没有装备、HP 和战斗状态，一律补空。
            run = migrated.get("exploration_run")
            if isinstance(run, dict):
                run.setdefault("loadout", {})
                run.setdefault("combat_party", {})
                run.setdefault("carried_items", [])
                run.setdefault("pending_fixed_rewards", [])
                run.setdefault("battle", None)
        if schema_version < 31:
            migrated.setdefault("sailing", {})
        if schema_version < 32:
            # Existing tasks/runs were started without an event bonus; never grant one retroactively.
            run = migrated.get("exploration_run")
            if run:
                run.setdefault("season_bonus", None)
            for site in migrated.get("gathering_sites", []):
                task = site.get("task_snapshot")
                if task:
                    task.setdefault("season_bonus", None)
        if schema_version < 33:
            migrated.setdefault("season_progress", {})
        migrated["schema_version"] = 35
        return migrated

    @model_validator(mode="after")
    def validate_owned_partners(self):
        for item_id, qualities in self.inventory.items():
            if any(quality < 0 or quality > 5 for quality in qualities):
                raise ValueError(f"inventory item {item_id} has an invalid quality")
            if any(quantity < 0 for quantity in qualities.values()):
                raise ValueError(f"inventory item {item_id} has a negative quantity")
        if any(quantity < 0 for quantity in self.task_items.values()):
            raise ValueError("task item quantity cannot be negative")
        partner_ids = [entry.partner_id for entry in self.owned_partners]
        if len(partner_ids) != len(set(partner_ids)):
            raise ValueError("player cannot own the same partner more than once")
        if len(self.talent_nodes) != len(set(self.talent_nodes)):
            raise ValueError("player cannot unlock the same talent node more than once")
        if len(self.seen_story_ids) != len(set(self.seen_story_ids)):
            raise ValueError("player cannot record the same story more than once")
        request_ids = [entry.request_id for entry in self.gacha_history]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("gacha request ids must be unique")
        portal_ids = [entry.portal_id for entry in self.portals]
        if len(portal_ids) != len(set(portal_ids)):
            raise ValueError("player cannot record the same portal more than once")
        mail_ids = [entry.mail_id for entry in self.mail_receipts]
        if len(mail_ids) != len(set(mail_ids)):
            raise ValueError("player cannot record the same mail more than once")
        achievement_ids = [entry.achievement_id for entry in self.achievements]
        if len(achievement_ids) != len(set(achievement_ids)):
            raise ValueError("player cannot complete the same achievement more than once")
        taken_ids = [entry.commission_id for entry in self.commission_takes]
        if len(taken_ids) != len(set(taken_ids)):
            raise ValueError("player cannot take the same commission more than once")
        if self.commission and self.commission.commission_id in set(taken_ids):
            raise ValueError("player cannot take their own commission")
        if self.sailing.active_run:
            if set(self.sailing.active_run.partner_ids) - set(partner_ids):
                raise ValueError("sailing party contains unowned partners")
        if self.exploration_run:
            unknown_explorers = set(self.exploration_run.partner_ids) - set(partner_ids)
            if unknown_explorers:
                raise ValueError("exploration party cannot contain partners the player does not own")
        pond_ids = [entry.pond_id for entry in self.ponds]
        if len(pond_ids) != len(set(pond_ids)):
            raise ValueError("player cannot record the same pond more than once")
        assigned_ids = [
            partner_id
            for production_slot in [
                *self.plots,
                *self.gathering_sites,
                *self.crafting_stations,
                *self.mining_sites,
                *self.ponds,
                self.refining,
            ]
            for partner_id in production_slot.assigned_partner_ids
        ]
        if len(assigned_ids) != len(set(assigned_ids)):
            raise ValueError("partner cannot be assigned to more than one production slot")
        unknown_ids = set(assigned_ids) - set(partner_ids)
        if unknown_ids:
            raise ValueError("production slots cannot assign partners the player does not own")
        companion_id = self.fishing.companion_partner_id
        if companion_id:
            if companion_id not in set(partner_ids):
                raise ValueError("fishing companion must be a partner the player owns")
            # 陪钓不写锁定引用，但跨产业唯一派驻规则照样适用。
            if companion_id in set(assigned_ids):
                raise ValueError("a partner stationed at a production slot cannot also come fishing")
        return self


class QQIdentity(BaseModel):
    platform: str
    bot_id: str
    subject: str

    @property
    def public_label(self) -> str:
        return f"{self.platform} Bot"
