"""Greenhouse boards API adapter (tier 1)."""

import html
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef
from daily_job_tool.adapters.registry import register

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

API = "https://boards-api.greenhouse.io/v1/boards"
BOARD_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "job-boards.eu.greenhouse.io",
    "boards.eu.greenhouse.io",
}
API_HOST = "boards-api.greenhouse.io"


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def _salary(ranges: Any) -> SalaryRaw | None:
    if not isinstance(ranges, list):
        return None
    for r in ranges:
        if not isinstance(r, dict):
            continue
        lo, hi = r.get("min_cents"), r.get("max_cents")
        if lo is None and hi is None:
            continue
        return SalaryRaw(
            min=lo / 100 if lo is not None else None,
            max=hi / 100 if hi is not None else None,
            currency=r.get("currency_type"),
            text=r.get("title"),
        )
    return None


@register
class GreenhouseAdapter(Adapter):
    name: ClassVar[str] = "greenhouse"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = sorted(BOARD_HOSTS | {API_HOST})

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        data = await http.get_json(f"{API}/{src.token}/jobs", params={"content": "true"})
        raw_jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw_jobs, list):
            raise ValueError(f"greenhouse {src.token}: unexpected jobs payload")
        warnings: list[str] = []
        jobs: list[RawJob] = []
        for rec in raw_jobs:
            job = self._map(rec)
            if job is None:
                warnings.append(f"skipped malformed record: {str(rec)[:120]}")
            else:
                jobs.append(job)
        board_name: str | None = None
        try:
            info = await http.get_json(f"{API}/{src.token}")
            if isinstance(info, dict) and isinstance(info.get("name"), str):
                board_name = info["name"] or None
        except Exception as exc:  # board name is optional; never fail the crawl for it
            warnings.append(f"board name lookup failed: {type(exc).__name__}")
        return FetchResult(jobs=jobs, complete=True, board_name=board_name, warnings=warnings)

    @staticmethod
    def _map(rec: Any) -> RawJob | None:
        if not isinstance(rec, dict):
            return None
        jid, title, url = rec.get("id"), rec.get("title"), rec.get("absolute_url")
        if jid is None or not title or not url:
            return None
        loc = rec.get("location")
        loc_name = loc.get("name") if isinstance(loc, dict) else None
        depts = rec.get("departments") or []
        dept = next((d["name"] for d in depts if isinstance(d, dict) and d.get("name")), None)
        content = rec.get("content")
        return RawJob(
            external_id=str(jid),
            title=str(title),
            url=url,
            apply_url=url,
            locations_raw=[loc_name.strip()]
            if isinstance(loc_name, str) and loc_name.strip()
            else [],
            department_raw=dept,
            description_html=html.unescape(content) if isinstance(content, str) else None,
            posted_at=_dt(rec.get("first_published")),
            updated_at=_dt(rec.get("updated_at")),
            salary=_salary(rec.get("pay_input_ranges")),
            # pay ranges are only exposed by the per-job ?pay_transparency=true call
            needs_detail=True,
            payload=rec,
        )

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        data = await http.get_json(
            f"{API}/{src.token}/jobs/{job.external_id}", params={"pay_transparency": "true"}
        )
        ranges = data.get("pay_input_ranges") if isinstance(data, dict) else None
        payload = {**job.payload, "pay_input_ranges": ranges if ranges is not None else []}
        return job.model_copy(
            update={
                "salary": _salary(ranges) or job.salary,
                "needs_detail": False,
                "payload": payload,
            }
        )

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        host = (p.hostname or "").lower()
        parts = [s for s in p.path.split("/") if s]
        if host == API_HOST:
            if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "boards":
                return parts[2], {}
            return None
        if host in BOARD_HOSTS:
            if parts and parts[0] == "embed":
                from urllib.parse import parse_qs

                token = (parse_qs(p.query).get("for") or [""])[0]
                return (token, {}) if token else None
            if parts:
                return parts[0], {}
        return None
