"""精制采用冻结的确定性品质时间点；不执行加工特性或随机品质。"""
from fractions import Fraction
from math import ceil

from pydantic import BaseModel, Field


class RefiningTask(BaseModel):
    task_id: str
    recipe_id: str
    input_item_id: str
    input_quantity: int = Field(gt=0)
    input_quality: int = Field(ge=1, le=4)
    produce_item_id: str
    started_at: int
    quality_times: list[int] = Field(min_length=1, max_length=4)
    ability: int = Field(ge=0)
    quality_ability: int = Field(ge=0)
    input_value: int = Field(ge=0)
    output_prices: list[int]

    def quality_at(self, now: int) -> int:
        return sum(now >= at for at in self.quality_times)


class RefiningRoomState(BaseModel):
    built: bool = False
    assigned_partner_ids: list[str] = Field(default_factory=list, max_length=1)
    slots: list[RefiningTask | None] = Field(default_factory=lambda: [None] * 6, min_length=6, max_length=6)
    settled_at: int = 0
    experience_remainders: dict[str, int] = Field(default_factory=dict)

    @property
    def occupied(self) -> bool:
        return any(task is not None for task in self.slots)


def quality_cap(ability: int) -> int:
    return 1 + sum(ability >= threshold for threshold in (60, 120, 180))


def quality_times(started_at, top_hours, raw_value, output_prices, ability, quality_ability, raw_quality):
    multiplier = (Fraction(1), Fraction(4, 5), Fraction(3, 5), Fraction(2, 5))[raw_quality - 1]
    efficiency = 1 + Fraction(ability, ability + 100)
    top = output_prices[3] - raw_value
    return [started_at + ceil(Fraction(str(top_hours)) * 3600 * (price - raw_value) / top / efficiency * multiplier)
            for price in output_prices[:quality_cap(quality_ability)]]


def working_seconds(room: RefiningRoomState, now: int) -> int:
    intervals = sorted((max(room.settled_at, task.started_at), min(now, task.quality_times[-1]))
                       for task in room.slots if task is not None)
    total, end = 0, room.settled_at
    for start, stop in intervals:
        start = max(start, end)
        if stop > start:
            total += stop - start
            end = stop
    return total
