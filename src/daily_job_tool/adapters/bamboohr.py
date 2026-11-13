"""BambooHR careers adapter (tier 1; public JSON behind every ``{co}.bamboohr.com/careers`` page).

Token = company subdomain (``pharmaforceiq`` for Aktana, ``berkshiregrey``); no config.

List: ``GET https://{co}.bamboohr.com/careers/list`` ->
``{"meta": {"totalCount": N}, "result": [...]}`` with every job in one response (no pagination).
Items carry ``id``, ``jobOpeningName``,
``departmentLabel``, ``employmentStatusLabel``, ``isRemote`` and two location objects: ``location``
(city/state) for ``locationType`` 0 and ``atsLocation`` (city/state/country) for type 1.
The list has no description, so every job is ``needs_detail``.

Detail: ``GET /careers/{id}/detail`` -> ``result.jobOpening`` {description (HTML), datePosted
(ISO date), compensation (free text such as "$165,000 - $175,000 a year" or "Base salary range
$150k-$230k"), employmentStatusLabel, jobOpeningShareUrl, location objects}. An unknown id is a 404.
The detail also holds a bulky ``formFields`` application form which is never stored.

``complete`` is True when the list parses; ``meta.totalCount`` is not trusted for anything else.
The stored payload is the list item plus (after detail) the ``jobOpening`` object under ``detail``,
with the ``_co`` company key; all of it is stable day to day. ``_map`` rebuilds a RawJob from that
payload with no HTTP.
"""

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef
from daily_job_tool.adapters.registry import register

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

HOST_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)\.bamboohr\.com$")
NOT_COMPANIES = {"www", "app", "api", "resources", "documentation", "help"}
AMOUNT = r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?"
RANGE_RE = re.compile(AMOUNT + r"\s*(?:-|–|—|to)\s*" + AMOUNT, re.IGNORECASE)
SINGLE_RE = re.compile(AMOUNT, re.IGNORECASE)
PERIOD_RE = re.compile(r"(hour|hr\b|year|annual|yr\b|month|week|day)", re.IGNORECASE)


def _amount(num: str, k: str | None) -> float:
    return float(num.replace(",", "")) * (1000 if k else 1)


def parse_compensation(text: Any) -> SalaryRaw | None:
    """Free-text compensation -> SalaryRaw (always keeps the text; numbers when clearly parsed)."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    lo = hi = None
    if m := RANGE_RE.search(text):
        lo, hi = _amount(m.group(1), m.group(2)), _amount(m.group(3), m.group(4))
    elif m := SINGLE_RE.search(text):
        lo = hi = _amount(m.group(1), m.group(2))
    period = None
    if lo is not None and (p := PERIOD_RE.search(text)):
        word = p.group(1).lower()
        period = (
            "hour"
            if word.startswith(("hour", "hr"))
            else "year"
            if word.startswith(("year", "annual", "yr"))
            else word
        )
    return SalaryRaw(
        min=lo, max=hi, currency="USD" if lo is not None else None, period=period, text=text
    )


def _place(loc: Any) -> str | None:
    if not isinstance(loc, dict):
        return None
    parts = [
        loc.get(k) or loc.get(alt)
        for k, alt in (("city", None), ("state", "province"), ("country", "addressCountry"))
    ]
    clean = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    return ", ".join(clean) or None


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _str(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


@register
class BambooHRAdapter(Adapter):
    name: ClassVar[str] = "bamboohr"
    tier: ClassVar[int] = 1
    hosts: ClassVar[list[str]] = ["*.bamboohr.com"]

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        data = await http.get_json(f"https://{src.token}.bamboohr.com/careers/list")
        recs = data.get("result") if isinstance(data, dict) else None
        if not isinstance(recs, list):
            raise ValueError(f"bamboohr {src.token}: unexpected list payload")
        jobs: list[RawJob] = []
        warnings: list[str] = []
        for rec in recs:
            job = self._map({**rec, "_co": src.token}) if isinstance(rec, dict) else None
            if job is None:
                warnings.append(f"skipped malformed record: {str(rec)[:120]}")
            else:
                jobs.append(job)
        return FetchResult(jobs=jobs, complete=True, warnings=warnings)

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        data = await http.get_json(
            f"https://{src.token}.bamboohr.com/careers/{job.external_id}/detail"
        )
        result = data.get("result") if isinstance(data, dict) else None
        opening = result.get("jobOpening") if isinstance(result, dict) else None
        if not isinstance(opening, dict):
            raise ValueError(f"bamboohr {src.token}: unexpected detail payload")
        mapped = self._map({**job.payload, "detail": opening})
        return mapped if mapped is not None else job

    @staticmethod
    def _map(payload: Any) -> RawJob | None:
        if not isinstance(payload, dict):
            return None
        jid, title, co = payload.get("id"), payload.get("jobOpeningName"), payload.get("_co")
        if jid in (None, "") or not _str(title) or not co:
            return None
        d = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
        locs: list[str] = []
        for src_obj in (d, payload):
            for key in ("atsLocation", "location"):
                p = _place(src_obj.get(key))
                if p and p not in locs:
                    locs.append(p)
            if locs:
                break
        url = f"https://{co}.bamboohr.com/careers/{jid}"
        desc = d.get("description")
        return RawJob(
            external_id=str(jid),
            title=str(title).strip(),
            url=url,
            apply_url=url,
            locations_raw=locs,
            workplace_raw="remote" if payload.get("isRemote") in (True, "1", 1) else None,
            department_raw=_str(d.get("departmentLabel")) or _str(payload.get("departmentLabel")),
            employment_type_raw=_str(d.get("employmentStatusLabel"))
            or _str(payload.get("employmentStatusLabel")),
            description_html=desc if isinstance(desc, str) and desc.strip() else None,
            posted_at=_date(d.get("datePosted")),
            salary=parse_compensation(d.get("compensation")),
            needs_detail=not d,
            payload=payload,
        )

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        m = HOST_RE.match((p.hostname or "").lower())
        if not m or m.group(1) in NOT_COMPANIES:
            return None
        return m.group(1), {}
