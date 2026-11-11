"""Darwinbox candidate API adapter (tier 1, public JSON).

`GET https://{tenant}.darwinbox.com/ms/candidateapi/job?page=N` returns 10 jobs per page
(`{"status": "success", "message": {"jobscount": N, "jobs": [...]}}`); pages are walked until one
comes back empty. The list has no description and no verified public detail endpoint, so
descriptions are left blank (needs_detail stays False). darwinbox.com serves no robots.txt.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SourceRef
from daily_job_tool.adapters.registry import register
from daily_job_tool.http.errors import CrawlError

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

DOMAIN = "darwinbox.com"
MAX_PAGES = 100


def _epoch_or_iso(rec: dict[str, Any]) -> datetime | None:
    """Prefer `job_posting_on` (epoch seconds, the publish time), else `created_on` (ISO)."""
    ts = rec.get("job_posting_on")
    if not isinstance(ts, bool) and isinstance(ts, int | float) and ts > 0:
        try:
            return datetime.fromtimestamp(ts, UTC)
        except (OverflowError, OSError, ValueError):
            pass
    value = rec.get("created_on")
    if isinstance(value, str) and value:
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)
    return None


def _host(src: SourceRef) -> str:
    host = src.config.get("host")
    return host if isinstance(host, str) and host else f"{src.token}.{DOMAIN}"


@register
class DarwinboxAdapter(Adapter):
    name: ClassVar[str] = "darwinbox"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = [DOMAIN]

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        host = _host(src)
        base = f"https://{host}/ms/candidateapi/job"
        jobs: list[RawJob] = []
        warnings: list[str] = []
        seen: set[str] = set()
        complete = True
        for page in range(1, MAX_PAGES + 1):
            try:
                data = await http.get_json(base, params={"page": str(page)})
                recs = self._page_jobs(data)
            except (CrawlError, ValueError) as exc:
                if page == 1:
                    raise
                complete = False
                warnings.append(f"page {page} failed: {exc}")
                break
            if not recs:
                break
            fresh = 0
            for rec in recs:
                job = self._map(rec, host)
                if job is None:
                    warnings.append(f"skipped malformed record: {str(rec)[:120]}")
                elif job.external_id not in seen:
                    seen.add(job.external_id)
                    jobs.append(job)
                    fresh += 1
            if fresh == 0:
                break  # the API repeated a page; stop instead of looping
        else:
            complete = False
            warnings.append(f"stopped after {MAX_PAGES} pages")
        return FetchResult(jobs=jobs, complete=complete, warnings=warnings)

    @staticmethod
    def _page_jobs(data: Any) -> list[Any]:
        msg = data.get("message") if isinstance(data, dict) else None
        jobs = msg.get("jobs") if isinstance(msg, dict) else None
        if not isinstance(jobs, list):
            raise ValueError("unexpected darwinbox payload")
        return jobs

    @staticmethod
    def _map(rec: Any, host: str | None = None) -> RawJob | None:
        if not isinstance(rec, dict):
            return None
        jid = rec.get("id")
        title = rec.get("title") or rec.get("designation_display_name")
        if not jid or not title:
            return None
        if host is None:  # renormalize path: host not known, fall back to stored hint
            host = str(rec.get("_host") or "")
        locs: list[str] = []
        tips = rec.get("tool_tip_locations")
        candidates = tips if isinstance(tips, list) else [rec.get("officelocation_show_arr")]
        for loc in candidates:
            if isinstance(loc, str) and loc.strip() and loc.strip() not in locs:
                locs.append(loc.strip())
        url = f"https://{host}/ms/candidatev2/main/careers/jobDetails/{jid}" if host else ""
        return RawJob(
            external_id=str(jid),
            title=str(title),
            url=url or str(jid),
            apply_url=url or None,
            locations_raw=locs,
            department_raw=rec.get("department") or None,
            employment_type_raw=rec.get("emp_type") or None,
            posted_at=_epoch_or_iso(rec),
            payload={**rec, "_host": host} if host else rec,
        )

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        host = (p.hostname or "").lower()
        if not host.endswith(f".{DOMAIN}"):
            return None
        tenant = host[: -len(DOMAIN) - 1]
        if not tenant or "." in tenant or tenant in ("www", "app", "login"):
            return None
        return tenant, {}
