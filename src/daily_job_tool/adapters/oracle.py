"""Oracle Recruiting Cloud (Candidate Experience) adapter (tier 2; public JSON REST behind every
``/hcmUI/CandidateExperience`` career site).

Source config: ``{"host": "ekgn.fa.us6.oraclecloud.com", "site": "CX_2001"}``; the registry token
is ``<host first label>/<site>``. ``site`` is either the ``CX_####`` number or the site code from
the careers URL (``UberCareers``). Optional keys: ``us_facet`` (bool, default true),
``detail_title_regex`` (str), ``page_size`` (default 100), ``max_jobs`` (default 3000),
``max_pages`` (default 200).

List: ``GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true
&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},limit={n},offset={k},
sortBy=POSTING_DATES_DESC[,selectedLocationsFacet={id}]``. The ``;`` and ``,`` of the finder must
stay in the URL string. The response holds ONE search wrapper in ``items[0]`` (top-level
``hasMore``/``count`` describe that wrapper, not the jobs) with ``TotalJobsCount`` (match count),
``Offset``/``Limit`` and the jobs in ``requisitionList`` (Id, Title, PostedDate (ISO date, real
posting date), PrimaryLocation, PrimaryLocationCountry, WorkplaceType, JobSchedule, ...). Without
``expand=requisitionList`` the list is empty. Every page repeats a large facet tree (locations,
categories, titles ...), which is not stored. ``limit`` accepts at least 250.

QUIRK: an unknown ``siteNumber`` does NOT error; the host silently serves its default site. The
wrapper echoes ``SiteNumber`` as requested, so it cannot be used to detect this; a wrong site
shows up as the wrong company's jobs (verify sites at seed time).

Paging continues until an EMPTY page. A page adding no new Id stops the crawl with
complete=False (server ignoring offset / unstable sort) unless every reported job was already
seen (tail repeat). ``complete=True`` requires ``distinct ids + malformed skipped >= total``
(max ``TotalJobsCount`` seen), no slack. Hitting max_jobs/max_pages gives complete=False plus
"tenant too large for full enumeration; jobs will not auto-close".

US narrowing: ``locationsFacet`` lists ``{"Id", "Name", "TotalCount"}``; the entry named exactly
"United States" (country level, id differs per tenant) is DISCOVERED with a cheap limit=1 request
and the listing is then filtered with ``selectedLocationsFacet={id}``. No such facet (or
``us_facet`` false) -> unfiltered list and ingest's US filter applies. A failing filtered first
page retries unfiltered with a warning. 400/404/422 on the first page raises NotFound.
Sandbox/test pods are rejected with ConfigError.

Stable payload: the list record minus volatile keys (``Distance`` (a timestamp-like number),
``Relevancy``, ``TrendingFlag``, ``BeFirstToApplyFlag``, ``MediaThumbURL``). external_id is the
list ``Id`` only.

Detail: ``GET .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{id}",
siteNumber={site}`` -> ``items[0]`` with ExternalDescriptionStr (HTML), ExternalResponsibilitiesStr,
ExternalQualificationsStr, ExternalPostedStartDate (ISO datetime), JobSchedule, Category,
workLocation (list), requisitionFlexFields (pay appears here when filled). A missing id returns
HTTP 200 with ``items: []`` which we raise as NotFound. Detail is only fetched for titles matching
``detail_title_regex`` (default: the Workday gate). ``_map`` rebuilds a RawJob without HTTP.
"""

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef
from daily_job_tool.adapters.html_list import ConfigError
from daily_job_tool.adapters.registry import register
from daily_job_tool.adapters.workday import DEFAULT_DETAIL_TITLE_REGEX
from daily_job_tool.http.errors import FetchError, NotFound

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

PAGE_SIZE = 100
MAX_JOBS = 3000
MAX_PAGES = 200
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)*\.fa\.[a-z0-9.-]*oraclecloud\.com$")
SANDBOX_RE = re.compile(r"(^|-)(test|dev\d*|stage|stg|uat|sandbox|preprod|demo)\d*(-|$)")
SITE_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
US_NAMES = {"united states", "united states of america", "usa", "us"}
VOLATILE = {"Distance", "Relevancy", "TrendingFlag", "BeFirstToApplyFlag", "MediaThumbURL"}
PAY_PROMPT_RE = re.compile(r"salary|pay|wage|compensation|rate|range|\bmin|\bmax", re.I)
DISCLAIMER_RE = re.compile(r"disclaimer|statement|note", re.I)
BASE = "https://{host}/hcmRestApi/resources/latest/"


def _iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def find_us_location_id(wrapper: Any) -> int | str | None:
    """Id of the country-level United States entry of ``locationsFacet``, if present."""
    facet = wrapper.get("locationsFacet") if isinstance(wrapper, dict) else None
    for f in facet if isinstance(facet, list) else []:
        if isinstance(f, dict) and f.get("Id") and str(f.get("Name", "")).lower() in US_NAMES:
            return f["Id"]
    return None


def _num(text: Any) -> float | None:
    if not isinstance(text, str):
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    return float(m.group(0).replace(",", "")) if m else None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        i = i.strip()
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _salary(fields: Any) -> SalaryRaw | None:
    lo = hi = None
    parts: list[str] = []
    for f in fields if isinstance(fields, list) else []:
        if not isinstance(f, dict):
            continue
        prompt, value = str(f.get("Prompt") or ""), f.get("Value")
        if not isinstance(value, str) or not value.strip() or len(value) > 60:
            continue
        low = prompt.lower()
        if re.search(r"min", low) and _num(value) is not None:
            lo = _num(value)
        elif re.search(r"max", low) and _num(value) is not None:
            hi = _num(value)
        else:
            continue
        parts.append(f"{prompt}: {value}")
    if lo is None and hi is None:
        return None
    return SalaryRaw(min=lo, max=hi, currency="USD", text="; ".join(parts))


@register
class OracleAdapter(Adapter):
    name: ClassVar[str] = "oracle"
    tier: ClassVar[int] = 2
    hosts: ClassVar[list[str]] = ["*.oraclecloud.com"]

    @staticmethod
    def _cfg(src: SourceRef) -> tuple[str, str]:
        host, site = src.config.get("host"), src.config.get("site")
        if not host or not site:
            raise ConfigError(f"oracle {src.token}: config needs host and site")
        host, site = str(host).lower(), str(site)
        first = host.split(".", 1)[0]
        if SANDBOX_RE.search(first) or ("saasfa" in first and "saasfaprod" not in first):
            raise ConfigError(f"oracle {src.token}: sandbox/test host {host} is not supported")
        if not HOST_RE.match(host):
            raise ConfigError(f"oracle {src.token}: {host} is not a *.fa.*.oraclecloud.com host")
        if not SITE_RE.match(site):
            raise ConfigError(f"oracle {src.token}: invalid site {site!r}")
        return host, site

    @staticmethod
    def _list_url(host: str, site: str, limit: int, offset: int, us_id: Any = None) -> str:
        finder = (
            f"findReqs;siteNumber={site},limit={limit},offset={offset},sortBy=POSTING_DATES_DESC"
        )
        if us_id is not None:
            finder += f",selectedLocationsFacet={us_id}"
        return (
            BASE.format(host=host) + "recruitingCEJobRequisitions?onlyData=true"
            "&expand=requisitionList.secondaryLocations&finder=" + finder
        )

    @staticmethod
    def _wrapper(data: Any, token: str) -> dict[str, Any]:
        items = data.get("items") if isinstance(data, dict) else None
        w = items[0] if isinstance(items, list) and items else None
        if not isinstance(w, dict) or not isinstance(w.get("requisitionList"), list):
            raise ValueError(f"oracle {token}: unexpected requisitions payload")
        return w

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        host, site = self._cfg(src)
        c = src.config
        size = max(1, int(c.get("page_size", PAGE_SIZE)))
        max_jobs = int(c.get("max_jobs", MAX_JOBS))
        max_pages = int(c.get("max_pages", MAX_PAGES))
        detail_re = re.compile(c.get("detail_title_regex") or DEFAULT_DETAIL_TITLE_REGEX, re.I)
        warnings: list[str] = []
        us_id: Any = None

        async def page(offset: int, limit: int = size) -> dict[str, Any]:
            data = await http.get_json(self._list_url(host, site, limit, offset, us_id))
            return self._wrapper(data, src.token)

        pages = 0
        url0 = self._list_url(host, site, size, 0)
        try:
            if c.get("us_facet", True):
                pages += 1
                disc = await page(0, 1)  # cheap discovery of the country facet id
                us_id = find_us_location_id(disc)
            pages += 1
            wrap = await page(0)
        except FetchError as exc:
            if us_id is None and exc.status in (400, 404, 422):
                raise NotFound(url0, exc.status) from exc
            if us_id is None:
                raise
            warnings.append(
                f"oracle {src.token}: US facet request failed ({exc.status}); retrying unfiltered"
            )
            us_id = None
            pages += 1
            try:
                wrap = await page(0)
            except FetchError as exc2:
                if exc2.status in (400, 404, 422):
                    raise NotFound(url0, exc2.status) from exc2
                raise

        total = 0
        jobs: list[RawJob] = []
        seen: set[str] = set()
        skipped = 0
        complete = True
        offset = 0
        while True:
            if isinstance(wrap.get("TotalJobsCount"), int):
                total = max(total, wrap["TotalJobsCount"])
            items = wrap["requisitionList"]
            if not items:
                break
            valid = new = 0
            for rec in items:
                job = self._build(rec, host, site, detail_re)
                if job is None:
                    skipped += 1
                    warnings.append(f"skipped malformed record: {str(rec)[:120]}")
                    continue
                valid += 1
                if job.external_id not in seen:
                    seen.add(job.external_id)
                    jobs.append(job)
                    new += 1
            offset += len(items)
            covered = total > 0 and len(seen) + skipped >= total
            if valid and not new and covered:
                break  # tail page repeating postings already seen
            if valid and not new:
                complete = False
                warnings.append(
                    f"oracle {src.token}: page at offset {offset - len(items)} added no new "
                    "postings (offset ignored or unstable sort); stopping"
                )
                break
            if covered and len(items) < size:
                break  # short final page and every reported job seen: no trailing request
            if len(jobs) >= max_jobs or pages >= max_pages:
                complete = False
                warnings.append(
                    f"oracle {src.token}: tenant too large for full enumeration; "
                    f"jobs will not auto-close (cap hit at {len(jobs)} jobs / {pages} pages)"
                )
                break
            pages += 1
            wrap = await page(offset)
        if complete and len(seen) + skipped < total:
            complete = False
            warnings.append(
                f"oracle {src.token}: listed {len(seen)} (+{skipped} malformed) of "
                f"reported total {total}"
            )
        return FetchResult(jobs=jobs, complete=complete, warnings=warnings)

    def _build(self, rec: Any, host: str, site: str, detail_re: re.Pattern[str]) -> RawJob | None:
        if not isinstance(rec, dict) or not rec.get("Title") or rec.get("Id") in (None, ""):
            return None
        payload = {k: v for k, v in rec.items() if k not in VOLATILE}
        payload.update(
            {"_host": host, "_site": site, "_nd": bool(detail_re.search(str(rec["Title"])))}
        )
        return self._map(payload)

    @staticmethod
    def _map(payload: Any) -> RawJob | None:
        if not isinstance(payload, dict):
            return None
        title, jid = payload.get("Title"), payload.get("Id")
        host, site = payload.get("_host"), payload.get("_site")
        if not title or jid in (None, "") or not host or not site:
            return None
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else None
        d = detail or {}
        url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}"
        locs = (
            [payload["PrimaryLocation"]] if isinstance(payload.get("PrimaryLocation"), str) else []
        )
        for s in payload.get("secondaryLocations") or []:
            if isinstance(s, dict) and isinstance(s.get("Name"), str):
                locs.append(s["Name"])
        parts = [
            d.get(k)
            for k in (
                "ExternalDescriptionStr",
                "ExternalResponsibilitiesStr",
                "ExternalQualificationsStr",
            )
            if isinstance(d.get(k), str) and d[k].strip()
        ]
        desc = "\n".join(parts) if parts else None
        emp = d.get("JobSchedule") or payload.get("JobSchedule") or payload.get("WorkerType")
        dept = (
            d.get("Category")
            or payload.get("Department")
            or payload.get("JobFamily")
            or payload.get("JobFunction")
        )
        return RawJob(
            external_id=str(jid),
            title=str(title),
            url=url,
            apply_url=url,
            locations_raw=_dedupe(locs),
            workplace_raw=payload.get("WorkplaceType") or None,
            department_raw=dept or None,
            employment_type_raw=emp or None,
            description_html=desc,
            posted_at=_iso(d.get("ExternalPostedStartDate")) or _iso(payload.get("PostedDate")),
            salary=_salary(d.get("requisitionFlexFields")),
            needs_detail=bool(payload.get("_nd")) and detail is None,
            payload=payload,
        )

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        host, site = self._cfg(src)
        url = (
            BASE.format(host=host) + "recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
            f'&finder=ById;Id="{job.external_id}",siteNumber={site}'
        )
        data = await http.get_json(url)
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ValueError(f"oracle {src.token}: unexpected detail payload")
        if not items or not isinstance(items[0], dict):
            raise NotFound(url, 404)  # Oracle answers 200 with an empty list for a closed job
        info = items[0]
        keep = {
            k: info.get(k)
            for k in (
                "ExternalDescriptionStr",
                "ExternalResponsibilitiesStr",
                "ExternalQualificationsStr",
                "ExternalPostedStartDate",
                "JobSchedule",
                "Category",
            )
        }
        keep["workLocation"] = [
            {k: w.get(k) for k in ("TownOrCity", "Region2", "Country")}
            for w in info.get("workLocation") or []
            if isinstance(w, dict)
        ]
        keep["requisitionFlexFields"] = [
            {k: f.get(k) for k in ("Prompt", "Value")}
            for f in info.get("requisitionFlexFields") or []
            if isinstance(f, dict)
            and PAY_PROMPT_RE.search(str(f.get("Prompt") or ""))
            and not DISCLAIMER_RE.search(str(f.get("Prompt") or ""))
        ]
        mapped = self._map({**job.payload, "detail": keep})
        return mapped if mapped is not None else job

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            p = urlsplit(url.strip())
        except ValueError:
            return None
        host = (p.hostname or "").lower()
        if not HOST_RE.match(host):
            return None
        parts = [s for s in p.path.split("/") if s]
        low = [s.lower() for s in parts]
        if "sites" not in low or low[:2] != ["hcmui", "candidateexperience"]:
            return None
        i = low.index("sites")
        if i + 1 >= len(parts) or not SITE_RE.match(parts[i + 1]):
            return None
        site = parts[i + 1]
        return f"{host.split('.', 1)[0]}/{site}", {"host": host, "site": site}
