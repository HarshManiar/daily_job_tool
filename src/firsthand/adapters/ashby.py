"""Ashby posting API adapter (tier 1)."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from firsthand.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef
from firsthand.adapters.registry import register

if TYPE_CHECKING:
    from firsthand.http.client import CrawlClient

API = "https://api.ashbyhq.com/posting-api/job-board"
SITE_HOST = "jobs.ashbyhq.com"
API_HOST = "api.ashbyhq.com"


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


def _salary(comp: Any) -> SalaryRaw | None:
    if not isinstance(comp, dict):
        return None
    text = comp.get("compensationTierSummary") or None
    comps = [
        c
        for c in comp.get("summaryComponents") or []
        if isinstance(c, dict) and c.get("compensationType") == "Salary"
    ]
    c = comps[0] if comps else None
    if c is None and text is None:
        return None
    c = c or {}
    return SalaryRaw(
        min=c.get("minValue"),
        max=c.get("maxValue"),
        currency=c.get("currencyCode"),
        period=c.get("interval"),
        text=text,
    )


@register
class AshbyAdapter(Adapter):
    name: ClassVar[str] = "ashby"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = [API_HOST, SITE_HOST]

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        data = await http.get_json(f"{API}/{src.token}", params={"includeCompensation": "true"})
        raw_jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw_jobs, list):
            raise ValueError(f"ashby {src.token}: unexpected jobs payload")
        jobs: list[RawJob] = []
        warnings: list[str] = []
        for rec in raw_jobs:
            if isinstance(rec, dict) and rec.get("isListed") is False:
                continue
            job = self._map(rec)
            if job is None:
                warnings.append(f"skipped malformed record: {str(rec)[:120]}")
            else:
                jobs.append(job)
        return FetchResult(jobs=jobs, complete=True, warnings=warnings)

    @staticmethod
    def _map(rec: Any) -> RawJob | None:
        if not isinstance(rec, dict):
            return None
        jid, title, url = rec.get("id"), rec.get("title"), rec.get("jobUrl")
        if not jid or not title or not url:
            return None
        locs: list[str] = []
        secondary = [
            s.get("location") for s in rec.get("secondaryLocations") or [] if isinstance(s, dict)
        ]
        for loc in [rec.get("location"), *secondary]:
            if isinstance(loc, str) and loc.strip() and loc.strip() not in locs:
                locs.append(loc.strip())
        workplace = rec.get("workplaceType") or ("Remote" if rec.get("isRemote") is True else None)
        return RawJob(
            external_id=str(jid),
            title=str(title),
            url=url,
            apply_url=rec.get("applyUrl"),
            locations_raw=locs,
            workplace_raw=workplace,
            department_raw=rec.get("department") or rec.get("team"),
            employment_type_raw=rec.get("employmentType"),
            description_html=rec.get("descriptionHtml"),
            posted_at=_dt(rec.get("publishedAt")),
            salary=_salary(rec.get("compensation")),
            payload=rec,
        )

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        host = (p.hostname or "").lower()
        parts = [s for s in p.path.split("/") if s]
        if host == SITE_HOST and parts:
            return parts[0], {}
        if host == API_HOST and len(parts) >= 3 and parts[:2] == ["posting-api", "job-board"]:
            return parts[2], {}
        return None
