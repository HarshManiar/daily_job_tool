"""Workable widget API adapter (tier 1, public JSON).

`GET https://apply.workable.com/api/v1/widget/accounts/{account}?details=true` returns every
published job with its HTML description, so no per-job detail call is needed.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SourceRef
from daily_job_tool.adapters.registry import register

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

API = "https://apply.workable.com/api/v1/widget/accounts"
SITE_HOST = "apply.workable.com"
_NOT_ACCOUNTS = {"j", "api", "static", "assets"}


def _dt(value: Any) -> datetime | None:
    """`published_on` is a bare date ("2026-09-25"); ISO datetimes are accepted too."""
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def _join(*parts: Any) -> str:
    out: list[str] = []
    for p in parts:
        if isinstance(p, str) and p.strip() and p.strip() not in out:
            out.append(p.strip())
    return ", ".join(out)


def _locations(rec: dict[str, Any]) -> list[str]:
    locs: list[str] = []
    for item in rec.get("locations") or []:
        if not isinstance(item, dict) or item.get("hidden") is True:
            continue
        text = _join(item.get("city"), item.get("region"), item.get("country"))
        if text and text not in locs:
            locs.append(text)
    if not locs:
        text = _join(rec.get("city"), rec.get("state"), rec.get("country"))
        if text:
            locs.append(text)
    return locs


def _merge(rows: list[Any]) -> list[Any]:
    """The widget repeats a job once per location; fold those rows into one by shortcode."""
    merged: dict[str, dict[str, Any]] = {}
    out: list[Any] = []
    for rec in rows:
        code = rec.get("shortcode") if isinstance(rec, dict) else None
        if not code:
            out.append(rec)  # malformed: let _map reject it
            continue
        locs = [x for x in rec.get("locations") or [] if isinstance(x, dict)]
        if code not in merged:
            merged[code] = {**rec, "locations": list(locs)}
            out.append(merged[code])
        else:
            for x in locs:
                if x not in merged[code]["locations"]:
                    merged[code]["locations"].append(x)
    return out


@register
class WorkableAdapter(Adapter):
    name: ClassVar[str] = "workable"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = [SITE_HOST]

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        data = await http.get_json(f"{API}/{src.token}", params={"details": "true"})
        raw_jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw_jobs, list):
            raise ValueError(f"workable {src.token}: unexpected jobs payload")
        jobs: list[RawJob] = []
        warnings: list[str] = []
        for rec in _merge(raw_jobs):
            job = self._map(rec)
            if job is None:
                warnings.append(f"skipped malformed record: {str(rec)[:120]}")
            else:
                jobs.append(job)
        name = data.get("name")
        return FetchResult(
            jobs=jobs,
            complete=True,
            board_name=name if isinstance(name, str) else None,
            warnings=warnings,
        )

    @staticmethod
    def _map(rec: Any) -> RawJob | None:
        if not isinstance(rec, dict):
            return None
        code, title, url = rec.get("shortcode"), rec.get("title"), rec.get("url")
        if not code or not title or not url:
            return None
        return RawJob(
            external_id=str(code),
            title=str(title),
            url=url,
            apply_url=rec.get("application_url"),
            locations_raw=_locations(rec),
            workplace_raw="Remote" if rec.get("telecommuting") is True else None,
            department_raw=rec.get("department") or None,
            employment_type_raw=rec.get("employment_type") or None,
            description_html=rec.get("description") or None,
            posted_at=_dt(rec.get("published_on") or rec.get("created_at")),
            payload=rec,
        )

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        if (p.hostname or "").lower() != SITE_HOST:
            return None
        parts = [s for s in p.path.split("/") if s]
        if len(parts) >= 5 and parts[:4] == ["api", "v1", "widget", "accounts"]:
            return parts[4], {}
        if parts and parts[0].lower() not in _NOT_ACCOUNTS:
            return parts[0], {}
        return None
