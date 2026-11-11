"""Gem job board API adapter (tier 1, public JSON).

`GET https://api.gem.com/job_board/v0/{board}/job_posts/` is the vendor API that Gem-hosted
career pages (jobs.gem.com/{board}) call themselves; it is intended for public job boards.
Robots note: api.gem.com/robots.txt answers 403. CrawlClient treats a 4xx robots response as
"no robots file published" and allows the fetch (RFC 9309); this is intentional and documented.
"""

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SourceRef
from daily_job_tool.adapters.registry import register

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

API = "https://api.gem.com/job_board/v0"
SITE_HOST = "jobs.gem.com"
API_HOST = "api.gem.com"
_CATCH_ALL = re.compile(r"don.?t see your role", re.I)


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


def _loc_name(obj: Any) -> str | None:
    if isinstance(obj, dict):
        name = obj.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


@register
class GemAdapter(Adapter):
    name: ClassVar[str] = "gem"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = [API_HOST, SITE_HOST]

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        data = await http.get_json(f"{API}/{src.token}/job_posts/")
        if not isinstance(data, list):
            raise ValueError(f"gem {src.token}: unexpected job_posts payload")
        jobs: list[RawJob] = []
        warnings: list[str] = []
        for rec in data:
            if isinstance(rec, dict) and _CATCH_ALL.search(str(rec.get("title") or "")):
                continue  # evergreen "Don't see your role?" talent-pool posting
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
        jid, title, url = rec.get("id"), rec.get("title"), rec.get("absolute_url")
        if not jid or not title or not url:
            return None
        locs: list[str] = []
        offices = [o.get("location") for o in rec.get("offices") or [] if isinstance(o, dict)]
        for loc in [rec.get("location"), *offices]:
            name = _loc_name(loc)
            if name and name not in locs:
                locs.append(name)
        depts = [
            d["name"]
            for d in rec.get("departments") or []
            if isinstance(d, dict) and isinstance(d.get("name"), str) and d["name"]
        ]
        ltype = rec.get("location_type")
        return RawJob(
            external_id=str(jid),
            title=str(title),
            url=url,
            apply_url=url,
            locations_raw=locs,
            workplace_raw=ltype if isinstance(ltype, str) and ltype else None,
            department_raw=", ".join(depts) or None,
            employment_type_raw=rec.get("employment_type") or None,
            description_html=rec.get("content") or None,
            posted_at=_dt(rec.get("first_published_at") or rec.get("created_at")),
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
        parts = [s for s in p.path.split("/") if s]
        if host == SITE_HOST and parts:
            return parts[0], {}
        if host == API_HOST and len(parts) >= 3 and parts[:2] == ["job_board", "v0"]:
            return parts[2], {}
        return None
