"""Lever postings API adapter (tier 1)."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef
from daily_job_tool.adapters.registry import register

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

API = {"us": "https://api.lever.co", "eu": "https://api.eu.lever.co"}
SITE_HOSTS = {"jobs.lever.co": "us", "jobs.eu.lever.co": "eu"}
API_HOSTS = {"api.lever.co": "us", "api.eu.lever.co": "eu"}


def _ms(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _description(rec: dict[str, Any]) -> str | None:
    parts: list[str] = []
    desc = rec.get("description") or rec.get("descriptionBody")
    if isinstance(desc, str) and desc:
        parts.append(desc)
    for item in rec.get("lists") or []:
        if not isinstance(item, dict):
            continue
        text, content = item.get("text"), item.get("content")
        if text:
            parts.append(f"<h3>{text}</h3>")
        if content:
            parts.append(f"<ul>{content}</ul>")
    extra = rec.get("additional")
    if isinstance(extra, str) and extra:
        parts.append(extra)
    return "\n".join(parts) or None


@register
class LeverAdapter(Adapter):
    name: ClassVar[str] = "lever"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = sorted(set(SITE_HOSTS) | set(API_HOSTS))

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        base = API["eu" if src.config.get("region") == "eu" else "us"]
        data = await http.get_json(f"{base}/v0/postings/{src.token}", params={"mode": "json"})
        if not isinstance(data, list):
            raise ValueError(f"lever {src.token}: unexpected postings payload")
        jobs: list[RawJob] = []
        warnings: list[str] = []
        for rec in data:
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
        jid, title, url = rec.get("id"), rec.get("text"), rec.get("hostedUrl")
        if not jid or not title or not url:
            return None
        cats = rec.get("categories") if isinstance(rec.get("categories"), dict) else {}
        locs: list[str] = []
        for loc in [cats.get("location"), *(cats.get("allLocations") or [])]:
            if isinstance(loc, str) and loc.strip() and loc.strip() not in locs:
                locs.append(loc.strip())
        sal = rec.get("salaryRange")
        salary = None
        if isinstance(sal, dict) and (sal.get("min") is not None or sal.get("max") is not None):
            salary = SalaryRaw(
                min=sal.get("min"),
                max=sal.get("max"),
                currency=sal.get("currency"),
                period=sal.get("interval"),
            )
        return RawJob(
            external_id=str(jid),
            title=str(title),
            url=url,
            apply_url=rec.get("applyUrl"),
            locations_raw=locs,
            workplace_raw=rec.get("workplaceType"),
            department_raw=cats.get("department") or cats.get("team"),
            employment_type_raw=cats.get("commitment"),
            description_html=_description(rec),
            posted_at=_ms(rec.get("createdAt")),
            salary=salary,
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
        if host in SITE_HOSTS:
            site = parts[0] if parts else None
        elif host in API_HOSTS:
            site = parts[2] if len(parts) >= 3 and parts[:2] == ["v0", "postings"] else None
        else:
            return None
        if not site:
            return None
        region = (SITE_HOSTS | API_HOSTS)[host]
        return site, ({"region": "eu"} if region == "eu" else {})
