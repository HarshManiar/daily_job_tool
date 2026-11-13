"""Workday CXS adapter (tier 2; undocumented but stable public JSON behind every career site).

Source config: ``{"host": "tenant.wd5.myworkdayjobs.com", "tenant": "tenant", "site": "Site"}``;
the registry token is ``tenant/site``. Optional config keys: ``us_facet`` (bool, default true),
``detail_title_regex`` (str), ``max_jobs`` (default 5000), ``max_pages`` (default 300).

List: ``POST https://{host}/wday/cxs/{tenant}/{site}/jobs`` with
``{"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}``. Items carry title,
externalPath, locationsText ("2 Locations" when several), postedOn (relative text such as
"Posted Today", "Posted 6 Days Ago", "Posted 30+ Days Ago"), bulletFields (job id) and often
timeType. ``total`` is the match count; some tenants only report it on the first page (later
pages say 0), so ``total`` is the MAX non-zero value seen on any page. Paging continues until
an EMPTY page (it does not stop at offset >= total, so jobs posted mid-crawl are still
reached). A page that adds no new ``externalPath`` (server ignoring ``offset``, or overlapping
pages from an unstable sort) stops the crawl with ``complete=False`` and a warning.
``complete=True`` requires ``distinct paths seen + malformed records skipped >= total``;
there is no percentage slack, so a silent miss can never let ingest auto-close live jobs.
Hitting max_jobs / max_pages returns ``complete=False`` plus the warning "tenant too large for
full enumeration; jobs will not auto-close" (no sitemap diffing; big tenants simply never
auto-close).

US narrowing: the response ``facets`` tree contains a country facet (``locationCountry`` or
``Country``) whose value for the United States has the same global id on every tenant seen
(bc33aa3152ec42d4995f4791a106ed09). When ``us_facet`` is true and such a facet exists, the
first (unfiltered) page is only used for discovery and listing restarts at offset 0 with
``appliedFacets={<facet>: [<US id>]}``. The US id is DISCOVERED per tenant from the facet value
whose descriptor says United States (the known global id is just what it usually is). Tenants
without a country facet are listed in full and ingest's US filter handles them. If the filtered
first page errors (any FetchError, e.g. 400/422), a warning is added and the whole listing is
retried UNFILTERED once; nothing is remembered across crawls. Switching between the filtered
and unfiltered view between crawls only changes which NON-US postings are listed (harmless:
ingest drops them anyway), so ``complete=True`` still means "every posting in the view
enumerated". A 400/422 on the first UNFILTERED page raises NotFound (counts toward 'broken').
Sandbox hosts (``impl-wdN`` / ``wdN-impl``) are rejected with ConfigError.

Stable payload: ingest hashes the payload, and ``postedOn`` is relative text that changes daily.
So the stored payload drops ``postedOn`` and instead carries ``_posted``, the absolute ISO date
``anchor - N days`` where anchor = today in America/Los_Angeles and N comes from "Posted
Today/Yesterday/N Days Ago". N and the anchor both advance one per day, so the value is the
same on every crawl and an unchanged job hashes identically day to day. "Posted 30+ Days Ago"
(unbounded) stores NO date key, so posted_at is None (the feed treats undated as first_seen).
A job crossing from 29 days to "30+" changes its payload once. ``external_id`` comes only from
the list item's externalPath (last ``_`` segment, e.g. ``R-101600`` or the repost form ``R9-1``),
never from bulletFields or the detail response (bulletFields stays in the payload for display).

Detail: ``GET https://{host}/wday/cxs/{tenant}/{site}{externalPath}`` ->
``jobPostingInfo`` {jobDescription (HTML), location, additionalLocations, timeType,
remoteType (only on some), startDate (ISO date, equals the posting date), jobReqId,
externalUrl, ...}. Detail calls are slow (0.5 req/s per host), so ``needs_detail`` is set only
for titles matching ``detail_title_regex`` (default DEFAULT_DETAIL_TITLE_REGEX); the rest keep
list-level fields. posted_at: detail ``startDate`` when present, else the stored ``_posted`` date.

``_map`` rebuilds a RawJob from the stored payload (list item + optional ``detail`` +
``_host``/``_site``/``_nd``/``_posted`` context keys) with no HTTP.
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SourceRef
from daily_job_tool.adapters.html_list import ConfigError
from daily_job_tool.adapters.registry import register
from daily_job_tool.http.errors import FetchError, NotFound
from daily_job_tool.match.profile import DEFAULT_DETAIL_TITLE_REGEX

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

PAGE_SIZE = 20
MAX_JOBS = 5000
MAX_PAGES = 300
US_COUNTRY_ID = "bc33aa3152ec42d4995f4791a106ed09"
US_NAMES = {"united states of america", "united states", "usa", "us"}


def default_detail_title_regex() -> str:
    """Profile's crawl.detail_title_regex; the built-in default when no profile is readable."""
    try:
        from daily_job_tool.match.profile import get_profile

        return get_profile().crawl.detail_title_regex
    except Exception:  # noqa: BLE001 - a broken/missing profile must never break a crawl
        return DEFAULT_DETAIL_TITLE_REGEX


HOST_RE = re.compile(r"^([a-z0-9][a-z0-9-]*)\.(wd\d+)\.myworkdayjobs\.com$")
IMPL_RE = re.compile(r"\.(impl-wd\d+|wd\d+-impl)\.myworkdayjobs\.com$")
LA = ZoneInfo("America/Los_Angeles")
STATES = {
    s.lower()
    for s in (
        "Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida Georgia "
        "Hawaii Idaho Illinois Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts "
        "Michigan Minnesota Mississippi Missouri Montana Nebraska Nevada Ohio Oklahoma Oregon "
        "Pennsylvania Tennessee Texas Utah Vermont Virginia Washington Wisconsin Wyoming"
    ).split()
} | {
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "rhode island",
    "south carolina",
    "south dakota",
    "west virginia",
    "district of columbia",
}


def la_today() -> date:
    """Date anchor for relative 'Posted N Days Ago' text (America/Los_Angeles calendar date)."""
    return datetime.now(LA).date()


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
            if str(v.get("descriptor", "")).lower() in US_NAMES:
                return param, str(v["id"])
    return None


def _place(seg: str) -> str:
    words = [w for w in seg.replace("_", "-").split("-") if w]
    if len(words) < 2:
        return " ".join(words)
    for n in (2, 1):
        if len(words) > n:
            tail = " ".join(words[-n:])
            if tail.lower() in STATES or (n == 1 and re.fullmatch(r"[A-Z]{2}", tail)):
                return f"{' '.join(words[:-n])}, {tail}"
    return f"{words[0]}, {' '.join(words[1:])}" if len(words) == 2 else " ".join(words)


def _slug_location(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "job":
        return " - ".join(_place(x) for x in parts[1].split("---") if x.strip()) or None
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
                raise ConfigError(f"workday {src.token}: config needs host, tenant and site")
        host = str(host).lower()
        if IMPL_RE.search(host):
            raise ConfigError(f"workday {src.token}: sandbox (impl) host {host} is not supported")
        if not HOST_RE.match(host):
            raise ConfigError(f"workday {src.token}: {host} is not a *.wdN.myworkdayjobs.com host")
        return host, str(tenant), str(site)

    @staticmethod
    def _today() -> date:
        return la_today()

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        host, tenant, site = self._cfg(src)
        max_jobs = int(src.config.get("max_jobs", MAX_JOBS))
        max_pages = int(src.config.get("max_pages", MAX_PAGES))
        detail_re = re.compile(
            src.config.get("detail_title_regex") or default_detail_title_regex(), re.IGNORECASE
        )
        today = self._today()
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
        try:
            data = await page(0)
        except FetchError as exc:
            if exc.status in (400, 422):
                raise NotFound(url, exc.status) from exc
            raise
        if src.config.get("us_facet", True):
            us = find_us_facet(data.get("facets"))
            if us:
                applied = {us[0]: [us[1]]}
                pages += 1
                try:
                    data = await page(0)  # restart so page 1 is the US-filtered view
                except FetchError as exc:
                    warnings.append(
                        f"workday {src.token}: US facet request failed ({exc.status}); "
                        "retrying unfiltered"
                    )
                    applied = {}
                    pages += 1
                    data = await page(0)

        total = 0
        jobs: list[RawJob] = []
        seen: set[str] = set()
        skipped = 0
        complete = True
        offset = 0
        while True:
            if isinstance(data.get("total"), int):
                total = max(total, data["total"])
            items = data["jobPostings"]
            if not items:
                break
            valid = new = 0
            for rec in items:
                job = self._build(rec, host, site, detail_re, today)
                if job is None:
                    skipped += 1
                    warnings.append(f"skipped malformed record: {str(rec)[:120]}")
                    continue
                valid += 1
                if rec["externalPath"] not in seen:
                    seen.add(rec["externalPath"])
                    jobs.append(job)
                    new += 1
            offset += len(items)
            covered = total > 0 and len(seen) + skipped >= total
            if valid and not new and covered:
                break  # tail page repeating postings already seen; nothing left to list
            if valid and not new:
                complete = False
                warnings.append(
                    f"workday {src.token}: page at offset {offset - len(items)} added no new "
                    "postings (offset ignored or unstable sort); stopping"
                )
                break
            if covered and len(items) < PAGE_SIZE:
                break  # short final page and every reported job seen: no trailing request
            if len(jobs) >= max_jobs or pages >= max_pages:
                complete = False
                warnings.append(
                    f"workday {src.token}: tenant too large for full enumeration; "
                    f"jobs will not auto-close (cap hit at {len(jobs)} jobs / {pages} pages)"
                )
                break
            pages += 1
            data = await page(offset)
        if complete and len(seen) + skipped < total:
            complete = False
            warnings.append(
                f"workday {src.token}: listed {len(seen)} (+{skipped} malformed) of "
                f"reported total {total}"
            )
        return FetchResult(jobs=jobs, complete=complete, warnings=warnings)

    def _build(
        self, rec: Any, host: str, site: str, detail_re: re.Pattern[str], today: date
    ) -> RawJob | None:
        if not isinstance(rec, dict) or not rec.get("title") or not rec.get("externalPath"):
            return None
        payload = {k: v for k, v in rec.items() if k != "postedOn"}
        payload.update(
            {"_host": host, "_site": site, "_nd": bool(detail_re.search(str(rec["title"])))}
        )
        posted = parse_relative(rec.get("postedOn"), today)
        if posted is not None:
            payload["_posted"] = posted.date().isoformat()
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
        last = path.rstrip("/").rsplit("/", 1)[-1]
        ext_id = last.rsplit("_", 1)[-1] if "_" in last else last
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
        posted = _iso_date(d.get("startDate")) or _iso_date(payload.get("_posted"))
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
        info = {k: v for k, v in info.items() if k != "postedOn"}  # volatile relative text
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
