"""Workday CXS adapter (tier 2; undocumented but stable public JSON behind every career site).

Source config: ``{"host": "tenant.wd5.myworkdayjobs.com", "tenant": "tenant", "site": "Site"}``;
the registry token is ``tenant/site``. Optional config keys: ``us_facet`` (bool, default true),
``detail_title_regex`` (str), ``max_jobs`` (default 5000), ``max_pages`` (default 300).

List: ``POST https://{host}/wday/cxs/{tenant}/{site}/jobs`` with
``{"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}``. Items carry title,
externalPath, locationsText ("2 Locations" when several), postedOn (relative text such as
"Posted Today", "Posted 6 Days Ago", "Posted 30+ Days Ago"), bulletFields (job id) and often
timeType. ``total`` is the match count; some tenants only report it on the first page (later
pages say 0), so the first page's value is kept. Paging stops at an empty page or
offset >= total. Hitting max_jobs / max_pages returns ``complete=False`` plus a warning.

US narrowing: the response ``facets`` tree contains a country facet (``locationCountry`` or
``Country``) whose value for the United States has the same global id on every tenant seen
(bc33aa3152ec42d4995f4791a106ed09). When ``us_facet`` is true and such a facet exists, the
first (unfiltered) page is only used for discovery and listing restarts at offset 0 with
``appliedFacets={<facet>: [<US id>]}``. Tenants without a country facet are listed in full
and ingest's US filter handles them. ``complete=True`` then means "the entire US-filtered
view was enumerated"; that view is applied consistently on every crawl (it only changes if the
tenant adds or removes its country facet), so closing rules stay trustworthy.

Detail: ``GET https://{host}/wday/cxs/{tenant}/{site}{externalPath}`` ->
``jobPostingInfo`` {jobDescription (HTML), location, additionalLocations, timeType,
remoteType (only on some), startDate (ISO date, equals the posting date), jobReqId,
externalUrl, ...}. Detail calls are slow (0.5 req/s per host), so ``needs_detail`` is set only
for titles matching ``detail_title_regex`` (default DEFAULT_DETAIL_TITLE_REGEX); the rest keep
list-level fields. posted_at: detail ``startDate`` when present, else derived from the list's
relative text (approximate to the day; "30+ Days Ago" is unbounded so None).

``_map`` rebuilds a RawJob from the stored payload (list item + optional ``detail`` +
``_host``/``_site``/``_nd`` context keys) with no HTTP.
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SourceRef
from daily_job_tool.adapters.registry import register

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

PAGE_SIZE = 20
MAX_JOBS = 5000
MAX_PAGES = 300
US_COUNTRY_ID = "bc33aa3152ec42d4995f4791a106ed09"
US_NAMES = {"united states of america", "united states", "usa", "us"}
DEFAULT_DETAIL_TITLE_REGEX = (
    r"data|analy|insight|report|intelligence|\bBI\b|scien|engineer|machine learning|\bML\b|\bAI\b"
    r"|statistic|forecast|commercial|real.world|\bRWE\b|\bHEOR\b|claims|market access|pricing"
    r"|operations research|decision|consult|solutions|product analyst|sales operations|field"
)
HOST_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)\.((?:impl-)?wd\d+(?:-impl)?)\.myworkdayjobs\.com$")
LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")
NOT_SITES = {"wday", "job", "jobs", "login", "introduceYourself", "refreshFacet"}


def parse_relative(text: Any, today: date) -> datetime | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted N Days Ago' -> midnight UTC; else None."""
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if "30+" in t:
        return None
    days: int | None = None
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    elif m := re.search(r"(\d+)\s+days?\s+ago", t):
        days = int(m.group(1))
    if days is None:
        return None
    d = today - timedelta(days=days)
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _iso_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _facets(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for f in node:
            _facets(f, out)
    elif isinstance(node, dict):
        if node.get("facetParameter") and node.get("values") is not None:
            out.append(node)
        _facets(node.get("values"), out)


def find_us_facet(facets: Any) -> tuple[str, str] | None:
    """(facet parameter, id) of the United States value of a country facet, if present."""
    found: list[dict[str, Any]] = []
    _facets(facets, found)
    for f in found:
        param = str(f["facetParameter"])
        if param.lower() not in {"locationcountry", "country"}:
            continue
        for v in f.get("values") or []:
            if not isinstance(v, dict) or not v.get("id"):
                continue
            if v["id"] == US_COUNTRY_ID or str(v.get("descriptor", "")).lower() in US_NAMES:
                return param, str(v["id"])
    return None


def _slug_location(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "job":
        return " - ".join(x.replace("-", " ").strip() for x in parts[1].split("---")) or None
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        i = i.strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


@register
class WorkdayAdapter(Adapter):
    name: ClassVar[str] = "workday"
    tier: ClassVar[int] = 2
    hosts: ClassVar[list[str]] = ["*.myworkdayjobs.com"]

    @staticmethod
    def _cfg(src: SourceRef) -> tuple[str, str, str]:
        c = src.config
        host, tenant, site = c.get("host"), c.get("tenant"), c.get("site")
        if not (host and tenant and site):
            if "/" in src.token and host:
                tenant, site = src.token.split("/", 1)
            else:
                raise ValueError(f"workday {src.token}: config needs host, tenant and site")
        return str(host), str(tenant), str(site)

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        host, tenant, site = self._cfg(src)
        max_jobs = int(src.config.get("max_jobs", MAX_JOBS))
        max_pages = int(src.config.get("max_pages", MAX_PAGES))
        detail_re = re.compile(
            src.config.get("detail_title_regex") or DEFAULT_DETAIL_TITLE_REGEX, re.IGNORECASE
        )
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        warnings: list[str] = []
        applied: dict[str, list[str]] = {}

        async def page(offset: int) -> Any:
            body = {
                "appliedFacets": applied,
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            }
            data = await http.post_json(url, body)
            if not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
                raise ValueError(f"workday {src.token}: unexpected jobs payload")
            return data

        pages = 1
        data = await page(0)
        if src.config.get("us_facet", True):
            us = find_us_facet(data.get("facets"))
            if us:
                applied = {us[0]: [us[1]]}
                pages += 1
                data = await page(0)  # restart so page 1 is the US-filtered view

        total = data.get("total") if isinstance(data.get("total"), int) else 0
        jobs: list[RawJob] = []
        seen: set[str] = set()
        complete = True
        offset = 0
        while True:
            items = data["jobPostings"]
            if not items:
                break
            for rec in items:
                job = self._build(rec, host, site, detail_re)
                if job is None:
                    warnings.append(f"skipped malformed record: {str(rec)[:120]}")
                elif rec["externalPath"] not in seen:
                    seen.add(rec["externalPath"])
                    jobs.append(job)
            offset += len(items)
            if total and offset >= total:
                break
            if len(jobs) >= max_jobs or pages >= max_pages:
                complete = False
                warnings.append(f"workday {src.token}: cap hit at {len(jobs)} jobs / {pages} pages")
                break
            pages += 1
            data = await page(offset)
        if complete and total and len(seen) < total * 0.9:
            complete = False
            warnings.append(f"workday {src.token}: listed {len(seen)} of reported total {total}")
        return FetchResult(jobs=jobs, complete=complete, warnings=warnings)

    def _build(self, rec: Any, host: str, site: str, detail_re: re.Pattern[str]) -> RawJob | None:
        if not isinstance(rec, dict) or not rec.get("title") or not rec.get("externalPath"):
            return None
        payload = {
            **rec,
            "_host": host,
            "_site": site,
            "_nd": bool(detail_re.search(str(rec["title"]))),
        }
        return self._map(payload)

    @staticmethod
    def _map(payload: Any) -> RawJob | None:
        if not isinstance(payload, dict):
            return None
        title, path = payload.get("title"), payload.get("externalPath")
        host, site = payload.get("_host"), payload.get("_site")
        if not title or not isinstance(path, str) or not path or not host or not site:
            return None
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else None
        d = detail or {}
        bullets = payload.get("bulletFields")
        bullet = (
            next((b for b in bullets if isinstance(b, str) and b), None)
            if isinstance(bullets, list)
            else None
        )
        last = path.rstrip("/").rsplit("/", 1)[-1]
        ext_id = d.get("jobReqId") or bullet or (last.rsplit("_", 1)[-1] if "_" in last else last)
        url = d.get("externalUrl") or f"https://{host}/{site}{path}"
        if detail and isinstance(d.get("location"), str):
            extra = d.get("additionalLocations")
            locs = _dedupe([d["location"], *(x for x in extra or [] if isinstance(x, str))])
        else:
            text = payload.get("locationsText")
            if (
                isinstance(text, str)
                and text.strip()
                and not re.match(r"^\d+ locations?$", text.strip(), re.I)
            ):
                locs = [text.strip()]
            else:
                slug = _slug_location(path)
                locs = [slug] if slug else []
        posted = _iso_date(d.get("startDate")) or parse_relative(
            payload.get("postedOn") or d.get("postedOn"), datetime.now(UTC).date()
        )
        return RawJob(
            external_id=str(ext_id),
            title=str(title),
            url=url,
            apply_url=url,
            locations_raw=locs,
            workplace_raw=d.get("remoteType") or None,
            employment_type_raw=d.get("timeType") or payload.get("timeType") or None,
            description_html=d.get("jobDescription")
            if isinstance(d.get("jobDescription"), str)
            else None,
            posted_at=posted,
            needs_detail=bool(payload.get("_nd")) and detail is None,
            payload=payload,
        )

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        host, tenant, site = self._cfg(src)
        data = await http.get_json(
            f"https://{host}/wday/cxs/{tenant}/{site}{job.payload['externalPath']}"
        )
        info = data.get("jobPostingInfo") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            raise ValueError(f"workday {src.token}: unexpected detail payload")
        mapped = self._map({**job.payload, "detail": info})
        return mapped if mapped is not None else job

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        host = (p.hostname or "").lower()
        m = HOST_RE.match(host)
        if not m:
            return None
        tenant = m.group(1)
        parts = [s for s in p.path.split("/") if s]
        if parts and LOCALE_RE.match(parts[0]):
            parts = parts[1:]
        if parts[:3] and parts[0] == "wday" and len(parts) >= 4 and parts[1] == "cxs":
            tenant, parts = parts[2], parts[3:]
        if not parts or parts[0] in NOT_SITES:
            return None
        site = parts[0]
        return f"{tenant}/{site}", {"host": host, "tenant": tenant, "site": site}
