"""PharmaACE careers API adapter (tier 1, bespoke public JSON).

`GET https://pharmaace.com/api/V1/careers/jobs` is the API the company's own careers page calls
(robots.txt: `Allow: /`). It returns every row, open and closed, in one array; only
`status == "Open"` rows are kept. Public posting page: https://pharmaace.com/career/jobs/{job_id}
(verified to resolve; the page is a client-side app).
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SourceRef
from daily_job_tool.adapters.registry import register

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

API_URL = "https://pharmaace.com/api/V1/careers/jobs"
SITE_HOST = "pharmaace.com"
JOB_URL = "https://pharmaace.com/career/jobs"


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


def _description(rec: dict[str, Any]) -> str | None:
    parts = [
        rec.get(k).strip()
        for k in ("description", "responsibilities", "qualifications")
        if isinstance(rec.get(k), str) and rec[k].strip()
    ]
    return "\n".join(parts) or None


@register
class PharmaAceAdapter(Adapter):
    name: ClassVar[str] = "pharmaace"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = [SITE_HOST]

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        data = await http.get_json(API_URL)
        if not isinstance(data, list):
            raise ValueError("pharmaace: unexpected jobs payload")
        jobs: list[RawJob] = []
        warnings: list[str] = []
        for rec in data:
            if isinstance(rec, dict) and str(rec.get("status") or "").lower() != "open":
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
        jid, title = rec.get("job_id"), rec.get("position")
        if jid in (None, "") or not title:
            return None
        url = f"{JOB_URL}/{jid}"
        loc = rec.get("location")
        return RawJob(
            external_id=str(jid),
            title=str(title),
            url=url,
            apply_url=url,
            locations_raw=[loc.strip()] if isinstance(loc, str) and loc.strip() else [],
            department_raw=rec.get("practice") or None,
            employment_type_raw=rec.get("position_type") or None,
            description_html=_description(rec),
            posted_at=_dt(rec.get("created_at")),
            updated_at=_dt(rec.get("updated_at")),
            payload=rec,
        )

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        host = (p.hostname or "").lower()
        if host not in (SITE_HOST, f"www.{SITE_HOST}"):
            return None
        path = p.path.lower()
        if path.startswith(("/career", "/api/v1/careers")):
            return "pharmaace", {}
        return None
