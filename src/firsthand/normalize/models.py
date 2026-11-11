"""Normalizer I/O models (ARCHITECTURE.md sections 3 and 6)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

NORMALIZER_VERSION = 1


class CompanyInfo(BaseModel):
    name: str
    domain: str | None = None
    hq_country: str | None = None  # ISO alpha-2
    size_bucket: str | None = None  # '1-10','11-50','51-200','201-1000','1001-5000','5000+'


class SourceInfo(BaseModel):
    adapter: str
    token: str


class NormalizedJob(BaseModel):
    title: str
    title_norm: str
    seniority: str = "unknown"
    function: str = "other"
    department_raw: str | None = None
    employment_type: str = "unknown"
    remote_type: str = "unknown"
    remote_regions: list[str] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_min_usd: int | None = None
    salary_max_usd: int | None = None
    salary_source: str | None = None
    tech_tags: list[str] = Field(default_factory=list)
    description_html: str | None = None
    description_text: str | None = None
    posted_at: datetime | None = None
    apply_url: str | None = None
    normalizer_version: int = NORMALIZER_VERSION
