"""Validated event windows. Missing or invalid schedules never open seasonal content."""

from datetime import datetime
import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from red_leaf_town.domain.models import SeasonBonusSnapshot


class SeasonBonus(BaseModel):
    partner_id: str = Field(min_length=1)
    reward_multiplier: float = Field(default=1, ge=1, le=10, allow_inf_nan=False)
    label: str = ""
    note: str = ""


class SeasonDefinition(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    starts_at: datetime
    ends_at: datetime
    enabled: bool = True
    leader_bonus: SeasonBonus | None = None
    companion_bonus: SeasonBonus | None = None
    gathering_bonus: SeasonBonus | None = None
    leader_note: str = ""
    fishing_spot_badge: str = ""
    fishing_spot_note: str = ""
    gathering_site_badge: str = ""
    gathering_site_note: str = ""

    @model_validator(mode="after")
    def validate_window(self):
        if self.starts_at.utcoffset() is None or self.ends_at.utcoffset() is None:
            raise ValueError("season dates must include a timezone")
        if self.ends_at <= self.starts_at:
            raise ValueError("season ends_at must be later than starts_at")
        return self

    def is_open(self, now: int) -> bool:
        return self.enabled and self.starts_at.timestamp() <= now < self.ends_at.timestamp()

    def bonus_snapshot(self, kind: str, partner_ids: list[str]) -> SeasonBonusSnapshot | None:
        bonus = getattr(self, kind)
        if bonus is None or bonus.partner_id not in partner_ids:
            return None
        return SeasonBonusSnapshot(
            season_id=self.id, season_name=self.name, partner_id=bonus.partner_id,
            multiplier=bonus.reward_multiplier, label=bonus.label,
        )


class SeasonCatalog(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    seasons: list[SeasonDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ids(self):
        if len({entry.id for entry in self.seasons}) != len(self.seasons):
            raise ValueError("season ids must be unique")
        return self


DEFAULT_SEASONS_PATH = Path(__file__).resolve().parents[2] / "data" / "seasons.json"


def load_seasons(path: Path | str = DEFAULT_SEASONS_PATH) -> dict[str, SeasonDefinition]:
    # Read on demand so operators can change dates without restarting the service.
    catalog = SeasonCatalog.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    return {entry.id: entry for entry in catalog.seasons}
