"""Rippling ATS adapter (tier 1, public JSON plus one page per new posting).

List: `GET https://api.rippling.com/platform/api/ats/v1/board/{board}/jobs` returns a flat array
with one row per (job, location); rows are merged on `uuid`. The list carries no dates or
descriptions, so every job has needs_detail=True and `fetch_detail` reads the public posting
page `https://ats.rippling.com/{board}/jobs/{uuid}` (its `__NEXT_DATA__` holds `createdOn`,
`description`, `employmentType`, `workLocations`). The ingest layer only calls fetch_detail for
postings that are new. Detail fields are stored under payload["detail"] so `_map` can rebuild
the full job from the stored payload. `payRangeDetails` was empty on every sampled posting, so
salary is not mapped. ats.rippling.com robots: `Disallow: /internal/` only; api.rippling.com has
no robots.txt (404).
"""

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SourceRef
from daily_job_tool.adapters.registry import register

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

API = "https://api.rippling.com/platform/api/ats/v1/board"
SITE_HOST = "ats.rippling.com"
API_HOST = "api.rippling.com"
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_NOT_BOARDS = {"embed", "internal", "api", "_next", "static"}


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


def _label(obj: Any) -> str | None:
    if isinstance(obj, dict):
        v = obj.get("label") or obj.get("name")
        return v.strip() if isinstance(v, str) and v.strip() else None
    return None


def _merge(rows: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Collapse per-location rows into one record per uuid with a `locations` list."""
    merged: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("uuid") or not row.get("name"):
            warnings.append(f"skipped malformed record: {str(row)[:120]}")
            continue
        rec = merged.setdefault(str(row["uuid"]), {**row, "locations": []})
        loc = _label(row.get("workLocation"))
        if loc and loc not in rec["locations"]:
            rec["locations"].append(loc)
    return list(merged.values()), warnings


def parse_detail(html: str) -> dict[str, Any] | None:
    """Pull the useful fields out of a posting page's __NEXT_DATA__; None if absent."""
    m = _NEXT_DATA.search(html)
    if not m:
        return None
    try:
        api = json.loads(m.group(1))["props"]["pageProps"]["apiData"]
        post = api["jobPost"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(post, dict):
        return None
    desc = post.get("description")
    if isinstance(desc, dict):
        desc = "\n".join(str(desc[k]) for k in ("role", "company") if desc.get(k)) or None
    emp = post.get("employmentType")
    return {
        "createdOn": post.get("createdOn"),
        "description": desc if isinstance(desc, str) else None,
        "employmentType": (emp.get("id") or emp.get("label")) if isinstance(emp, dict) else emp,
        "workLocations": [x for x in post.get("workLocations") or [] if isinstance(x, str)],
    }


@register
class RipplingAdapter(Adapter):
    name: ClassVar[str] = "rippling"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = [API_HOST, SITE_HOST]

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        data = await http.get_json(f"{API}/{src.token}/jobs")
        if not isinstance(data, list):
            raise ValueError(f"rippling {src.token}: unexpected jobs payload")
        recs, warnings = _merge(data)
        jobs: list[RawJob] = []
        for rec in recs:
            job = self._map(rec)
            if job is None:
                warnings.append(f"skipped malformed record: {str(rec)[:120]}")
            else:
                jobs.append(job)
        return FetchResult(jobs=jobs, complete=True, warnings=warnings)

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        html = await http.get_text(f"https://{SITE_HOST}/{src.token}/jobs/{job.external_id}")
        detail = parse_detail(html)
        if detail is None:
            return job.model_copy(update={"needs_detail": False})
        return self._map({**job.payload, "detail": detail}) or job

    @staticmethod
    def _map(rec: Any) -> RawJob | None:
        if not isinstance(rec, dict):
            return None
        uid, title = rec.get("uuid"), rec.get("name")
        url = rec.get("url")
        if not uid or not title or not url:
            return None
        locs = [x for x in rec.get("locations") or [] if isinstance(x, str)]
        if not locs and (one := _label(rec.get("workLocation"))):
            locs = [one]
        detail = rec.get("detail") if isinstance(rec.get("detail"), dict) else None
        if detail:
            for x in detail.get("workLocations") or []:
                if isinstance(x, str) and x not in locs:
                    locs.append(x)
        dept = _label(rec.get("department"))
        return RawJob(
            external_id=str(uid),
            title=str(title),
            url=url,
            apply_url=url,
            locations_raw=locs,
            department_raw=dept,
            employment_type_raw=(detail or {}).get("employmentType") or None,
            description_html=(detail or {}).get("description") or None,
            posted_at=_dt((detail or {}).get("createdOn")),
            needs_detail=detail is None,
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
            if parts[0] == "embed" and len(parts) >= 2:
                return parts[1], {}
            if parts[0] not in _NOT_BOARDS:
                return parts[0], {}
        if host == API_HOST and len(parts) >= 6 and parts[:4] == ["platform", "api", "ats", "v1"]:
            if parts[4] == "board":
                return parts[5], {}
        return None
