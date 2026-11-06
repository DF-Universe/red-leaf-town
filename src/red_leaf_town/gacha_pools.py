from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class GachaItemDropDefinition(BaseModel):
    task_item_id: str = Field(min_length=1)
    weight: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)


class GachaDefinition(BaseModel):
    """一个招募池的抽取规则。经济参数（汇率、印记、升星消耗）不在这里，见 GachaEconomyDefinition。"""

    season_id: str = ""
    rotating: bool = False
    pool_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    display_order: int = 0
    min_level: int = Field(default=1, ge=1)
    max_pulls_per_player: int | None = Field(default=None, ge=1)
    rarity_probabilities: dict[int, float]
    item_probability: float = Field(ge=0, lt=1)
    first_pulls_without_items: int = Field(default=0, ge=0)
    four_star_guarantee: int = Field(gt=0)
    five_star_pity: int = Field(gt=0)
    item_drops: list[GachaItemDropDefinition] = Field(default_factory=list)
    background_asset_id: str = ""
    featured_partner_id: str | None = None
    featured_rate: float = Field(default=0, ge=0, le=1)
    # 四星 UP：抽中四星时按这个比例给 UP 角色，没有大保底。
    featured_four_star_partner_id: str | None = None
    featured_four_star_rate: float = Field(default=0, ge=0, le=1)
    # 限定池的伙伴名单。留空表示不限定，用全部伙伴。
    partner_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_probabilities(self):
        if set(self.rarity_probabilities) != {3, 4, 5}:
            raise ValueError(f"gacha pool {self.pool_id}: rarity probabilities must define 3, 4 and 5 stars")
        if any(probability < 0 for probability in self.rarity_probabilities.values()):
            raise ValueError(f"gacha pool {self.pool_id}: rarity probabilities cannot be negative")
        if abs(sum(self.rarity_probabilities.values()) + self.item_probability - 1) > 1e-8:
            raise ValueError(f"gacha pool {self.pool_id}: probabilities must sum to one")
        if self.item_probability > 0 and not self.item_drops:
            raise ValueError(f"gacha pool {self.pool_id}: item_probability is positive but item_drops is empty")
        if bool(self.featured_partner_id) != (self.featured_rate > 0):
            raise ValueError(f"gacha pool {self.pool_id}: featured_partner_id and featured_rate must be set together")
        if bool(self.featured_four_star_partner_id) != (self.featured_four_star_rate > 0):
            raise ValueError(
                f"gacha pool {self.pool_id}: featured_four_star_partner_id and featured_four_star_rate must be set together"
            )
        if len(self.partner_ids) != len(set(self.partner_ids)):
            raise ValueError(f"gacha pool {self.pool_id}: partner_ids must be unique")
        if self.partner_ids and self.featured_partner_id and self.featured_partner_id not in self.partner_ids:
            raise ValueError(f"gacha pool {self.pool_id}: featured_partner_id must be part of partner_ids")
        if (
            self.partner_ids
            and self.featured_four_star_partner_id
            and self.featured_four_star_partner_id not in self.partner_ids
        ):
            raise ValueError(f"gacha pool {self.pool_id}: featured_four_star_partner_id must be part of partner_ids")
        return self


DEFAULT_GACHA_POOLS_DIR = Path(__file__).resolve().parents[2] / "data" / "gacha"


@lru_cache(maxsize=4)
def load_gacha_pools(directory: str | Path = DEFAULT_GACHA_POOLS_DIR) -> dict[str, GachaDefinition]:
    pools_dir = Path(directory)
    if not pools_dir.exists():
        return {}
    pools: dict[str, GachaDefinition] = {}
    for file_path in sorted(pools_dir.glob("*.json")):
        definition = GachaDefinition.model_validate(json.loads(file_path.read_text(encoding="utf-8")))
        if definition.pool_id in pools:
            raise ValueError(f"duplicate gacha pool id: {definition.pool_id}")
        pools[definition.pool_id] = definition
    return pools
