"""Adapter interface (ARCHITECTURE.md section 4). Adapters never import httpx."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from firsthand.http.client import CrawlClient


class SalaryRaw(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str | None = None
    period: str | None = None  # as given
    text: str | None = None  # free-text comp string if any


class RawJob(BaseModel):
    external_id: str
    title: str
    url: str  # hosted posting page (employer site or employer's ATS board)
    apply_url: str | None = None
    locations_raw: list[str] = Field(default_factory=list)
    workplace_raw: str | None = None  # structured remote/hybrid hint if the ATS provides it
    department_raw: str | None = None
    employment_type_raw: str | None = None
    description_html: str | None = None
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    salary: SalaryRaw | None = None
    needs_detail: bool = False  # list endpoint lacked description; fetch_detail required
    payload: dict[str, Any]  # complete original record, JSON-serializable


class FetchResult(BaseModel):
    jobs: list[RawJob]
    complete: bool  # True only if the whole board was enumerated
    board_name: str | None = None  # used by detection to verify identity
    warnings: list[str] = Field(default_factory=list)


class SourceRef(BaseModel):
    id: int | None = None
    adapter: str
    token: str
    config: dict[str, Any] = Field(default_factory=dict)


class Adapter(ABC):
    name: ClassVar[str]
    tier: ClassVar[int]
    hosts: ClassVar[list[str]]

    @abstractmethod
    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult: ...

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        return job

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        """If url belongs to this ATS, return (token, config). Pure; used by the detector."""
        return None
