"""Autumn harvest festival data: three tables, four chapters and two rites.

The event runs on the season window (`season_id`), so dates live in
`data/seasons.json` and nothing here has to be re-edited when the schedule
moves. Progress is kept in `PlayerState.crossover_claims` only — no schema
change:

    harvest:sent:<table>:<dish>   how many portions of that dish are on the table
    harvest:day:<table>           the last day (YYYYMMDD) the table was served
    harvest:dayn:<table>          portions served on that day
    harvest:table:<table>         1 = table filled, reward granted

The two rites (field rite / harvest thanksgiving) are plain story beats: they
carry no claim keys, `done` is just "the player has read that story id".
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from red_leaf_town.content import RewardDefinition

AUTUMN_ID = "autumn_harvest_festival"

_PATH = Path(__file__).resolve().parents[2] / "data/harvest_festival.json"


class HarvestDish(BaseModel):
    item_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class HarvestTable(BaseModel):
    id: str = Field(min_length=1)
    name: str = ""
    subtitle: str = ""
    requires: str | None = None
    dishes: list[HarvestDish] = Field(min_length=1)
    reward: RewardDefinition = RewardDefinition()
    story_id: str = ""


class HarvestChapter(BaseModel):
    story_id: str = Field(min_length=1)
    title: str = ""
    cue: str = ""
    note: str = ""
    unlock: str = "start"
    # 剧本还没写完时置 true：卡片留着位置，明说「未完待续」，不给点。
    pending: bool = False


class HarvestRite(BaseModel):
    id: str = Field(min_length=1)
    name: str = ""
    host: str = ""
    unlock: str = "start"
    story_id: str = ""
    cue: str = ""


class HarvestFestival(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    season_id: str = Field(min_length=1)
    name: str = "丰收祭"
    min_level: int = Field(default=1, ge=1)
    daily_limit_per_table: int = Field(default=4, ge=1, le=20)
    tables: list[HarvestTable] = Field(min_length=1)
    chapters: list[HarvestChapter] = Field(default_factory=list)
    rites: list[HarvestRite] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_refs(self):
        table_ids = [table.id for table in self.tables]
        if len(set(table_ids)) != len(table_ids):
            raise ValueError("harvest table ids must be unique")
        known = set(table_ids)
        # 每张桌子最多依赖一张更早的桌子，桌与桌串成一条链。
        index = {table_id: position for position, table_id in enumerate(table_ids)}
        for table in self.tables:
            if table.requires is None:
                continue
            if table.requires not in known:
                raise ValueError(f"harvest table {table.id} requires unknown table {table.requires}")
            if index[table.requires] >= index[table.id]:
                raise ValueError(f"harvest table {table.id} must come after {table.requires}")
        story_ids = [chapter.story_id for chapter in self.chapters]
        if len(set(story_ids)) != len(story_ids):
            raise ValueError("harvest chapter story ids must be unique")
        rite_ids = [rite.id for rite in self.rites]
        if len(set(rite_ids)) != len(rite_ids):
            raise ValueError("harvest rite ids must be unique")
        return self

    def table_map(self) -> dict[str, HarvestTable]:
        return {table.id: table for table in self.tables}


@lru_cache(maxsize=1)
def load_harvest_festival() -> HarvestFestival:
    """配置按进程缓存；改完重启服务（与 summer_event 一致）。"""
    return HarvestFestival.model_validate(json.loads(_PATH.read_text(encoding="utf-8")))
