"""Owner profile (data/profile.yaml, falling back to data/profile.example.yaml)."""

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from firsthand.normalize.visa import VisaPhrases

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[3] / "data"


class GenericRoles(BaseModel):
    title_regex: list[str] = Field(default_factory=list)
    exclude_title_regex: list[str] = Field(default_factory=list)


class TargetTitles(BaseModel):
    tier1: list[str] = Field(default_factory=list)
    tier2: list[str] = Field(default_factory=list)
    tier3: list[str] = Field(default_factory=list)
    generic_data_roles: GenericRoles = Field(default_factory=GenericRoles)


class YearsRequired(BaseModel):
    min: int = 2
    max: int = 6


class VisaFilter(BaseModel):
    status: str = "h1b_transfer_needed"
    exclude_if_phrases: list[str] = Field(default_factory=list)
    flag_if_phrases: list[str] = Field(default_factory=list)


class HardFilters(BaseModel):
    employment: str = "full_time_only"
    max_age_days: int = 14
    years_required: YearsRequired = Field(default_factory=YearsRequired)
    exclude_companies: list[str] = Field(default_factory=list)
    exclude_staffing_reposts: bool = True
    visa: VisaFilter = Field(default_factory=VisaFilter)


class LocationPriority(BaseModel):
    name: str
    states: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    remote_regions: list[str] = Field(default_factory=list)


class ProfileInfo(BaseModel):
    summary: str = ""
    data: list[str] = Field(default_factory=list)
    strong_skills: list[str] = Field(default_factory=list)
    gap_skills: list[str] = Field(default_factory=list)
    treat_as_match: list[str] = Field(default_factory=list)
    min_base_usd: int | None = None


class Profile(BaseModel):
    target_titles: TargetTitles = Field(default_factory=TargetTitles)
    hard_filters: HardFilters = Field(default_factory=HardFilters)
    location_priority: list[LocationPriority] = Field(default_factory=list)
    industry_priority: list[str] = Field(default_factory=list)
    profile: ProfileInfo = Field(default_factory=ProfileInfo)

    @model_validator(mode="before")
    @classmethod
    def _hoist_generic(cls, data):
        # the schema nests generic_data_roles under target_titles; accept top-level too
        if isinstance(data, dict) and "generic_data_roles" in data:
            data = dict(data)
            tt = dict(data.get("target_titles") or {})
            tt.setdefault("generic_data_roles", data.pop("generic_data_roles"))
            data["target_titles"] = tt
        return data

    @property
    def generic_data_roles(self) -> GenericRoles:
        return self.target_titles.generic_data_roles

    def visa_phrases(self) -> VisaPhrases:
        v = self.hard_filters.visa
        return VisaPhrases(tuple(v.exclude_if_phrases), tuple(v.flag_if_phrases))

    def location(self, name: str) -> LocationPriority | None:
        return next((p for p in self.location_priority if p.name == name), None)


def profile_path() -> Path | None:
    env = os.environ.get("FIRSTHAND_PROFILE")
    if env:
        return Path(env)
    for name in ("profile.yaml", "profile.example.yaml"):
        if (DATA_DIR / name).exists():
            return DATA_DIR / name
    return None


def load_profile(path: Path | str | None = None) -> Profile:
    p = Path(path) if path else profile_path()
    if p is None or not p.exists():
        log.warning("no profile file found; using a permissive empty profile")
        return Profile()
    return Profile.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})


@lru_cache(maxsize=1)
def get_profile() -> Profile:
    return load_profile()


@lru_cache(maxsize=512)
def compile_rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)
