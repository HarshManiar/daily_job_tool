"""Owner profile (data/profile.yaml, falling back to data/profile.example.yaml)."""

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from daily_job_tool.normalize.visa import VisaPhrases

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Built-in defaults for the optional profile knobs. They equal the values that used to be
# hardcoded, so a profile that omits a knob behaves exactly as before.
DEFAULT_BLOCKED_LEVELS = (
    "manager", "director", "vp", "vice", "president", "head", "principal", "staff", "intern",
    "internship", "chief", "executive", "svp", "evp",
)  # fmt: skip
DEFAULT_DETAIL_TITLE_REGEX = (
    r"data|analy|insight|report|intelligence|\bBI\b|scien|engineer|machine learning|\bML\b|\bAI\b"
    r"|statistic|forecast|commercial|real.world|\bRWE\b|\bHEOR\b|claims|market access|pricing"
    r"|operations research|decision|consult|solutions|product analyst|sales operations|field"
    r"|econom|epidem|access|planner|planning|\bops\b|operations|revops|research|informatics"
    r"|actuar|programmer|strategy|engagement|evidence|tableau|incentive|gross.to.net|outcomes"
    r"|value|population health|performance|measurement|quantitative|risk adjustment|biostat"
    r"|clinical data|medical affairs"
)
# Labels for the location names the original profile used, so the UI reads the same.
_LEGACY_LABELS = {"nj": "New Jersey", "nyc": "NYC area", "us_remote": "US remote"}


def _check_regex(v: str) -> str:
    try:
        re.compile(v)
    except re.error as e:
        raise ValueError(f"invalid regex {v!r}: {e}") from e
    return v


class GenericRoles(BaseModel):
    title_regex: list[str] = Field(default_factory=list)
    exclude_title_regex: list[str] = Field(default_factory=list)


class TargetTitles(BaseModel):
    tier1: list[str] = Field(default_factory=list)
    tier2: list[str] = Field(default_factory=list)
    tier3: list[str] = Field(default_factory=list)
    generic_data_roles: GenericRoles = Field(default_factory=GenericRoles)
    # Title words that mark a level outside the target band (a "Senior Manager" is not a
    # "Senior Analyst" with extra words). Replaces the built-in list entirely when set.
    blocked_levels: list[str] = Field(default_factory=lambda: list(DEFAULT_BLOCKED_LEVELS))

    @field_validator("blocked_levels")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return [x.strip().lower() for x in v if x and x.strip()]


class YearsRequired(BaseModel):
    min: int = 2
    max: int = 6


class VisaFilter(BaseModel):
    # "none" (the default) turns every visa filter off; any other value enables the phrases.
    status: str = "none"
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
    label: str | None = None  # shown in the UI; defaults to a readable form of `name`
    states: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    remote_regions: list[str] = Field(default_factory=list)

    @property
    def display_label(self) -> str:
        return self.label or _LEGACY_LABELS.get(self.name) or self.name.replace("_", " ").title()

    @property
    def key(self) -> str:
        """Value used in the feed's ?place= query string (us_remote is 'remote' there)."""
        return "remote" if self.name == "us_remote" else self.name


class ProfileInfo(BaseModel):
    summary: str = ""
    data: list[str] = Field(default_factory=list)
    strong_skills: list[str] = Field(default_factory=list)
    gap_skills: list[str] = Field(default_factory=list)
    treat_as_match: list[str] = Field(default_factory=list)
    min_base_usd: int | None = None


class Scoring(BaseModel):
    """Points behind the /10 job score (web/queries.py builds the SQL from these numbers)."""

    tier_points: list[float] = Field(default_factory=lambda: [4.0, 3.0, 2.0])  # tier 1, 2, 3
    skills_max: float = Field(default=2.0, ge=0, le=10)
    years_in_range: float = Field(default=2.0, ge=0, le=10)
    years_unknown_or_low: float = Field(default=1.0, ge=0, le=10)
    years_over_max: float = Field(default=0.0, ge=0, le=10)
    # points by location rank (index = position in location_priority, last = anything else)
    location_points: list[float] = Field(default_factory=lambda: [1.0, 0.75, 0.5, 0.0])
    industry: float = Field(default=1.0, ge=0, le=10)

    @field_validator("tier_points", "location_points")
    @classmethod
    def _bounded(cls, v: list[float]) -> list[float]:
        if len(v) > 20 or any(not (0 <= x <= 10) for x in v):
            raise ValueError("points must be between 0 and 10 (at most 20 entries)")
        return v

    @field_validator("tier_points")
    @classmethod
    def _three(cls, v: list[float]) -> list[float]:
        return (list(v) + [0.0, 0.0, 0.0])[:3]


class CrawlConfig(BaseModel):
    detail_title_regex: str = DEFAULT_DETAIL_TITLE_REGEX

    @field_validator("detail_title_regex")
    @classmethod
    def _valid(cls, v: str) -> str:
        return _check_regex(v)


class Profile(BaseModel):
    target_titles: TargetTitles = Field(default_factory=TargetTitles)
    hard_filters: HardFilters = Field(default_factory=HardFilters)
    location_priority: list[LocationPriority] = Field(default_factory=list)
    industry_priority: list[str] = Field(default_factory=list)
    profile: ProfileInfo = Field(default_factory=ProfileInfo)
    scoring: Scoring = Field(default_factory=Scoring)
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    # skill name -> regex; merged over the built-in aliases in match/fit.py
    skill_aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("skill_aliases")
    @classmethod
    def _aliases(cls, v: dict[str, str]) -> dict[str, str]:
        return {k.strip().lower(): _check_regex(rx) for k, rx in v.items()}

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
        if v.status == "none":
            return VisaPhrases((), ())
        return VisaPhrases(tuple(v.exclude_if_phrases), tuple(v.flag_if_phrases))

    @property
    def visa_filters_on(self) -> bool:
        return self.hard_filters.visa.status != "none"

    def location(self, name: str) -> LocationPriority | None:
        return next((p for p in self.location_priority if p.name == name), None)


def profile_is_example(path: Path | None = None) -> bool:
    p = path or profile_path()
    return p is not None and p.name == "profile.example.yaml"


def profile_path() -> Path | None:
    env = os.environ.get("DAILY_JOB_TOOL_PROFILE")
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
