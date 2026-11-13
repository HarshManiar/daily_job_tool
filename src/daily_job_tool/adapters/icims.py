"""iCIMS career-portal adapter (tier 2; public HTML search + schema.org JSON-LD job pages).

Source config: ``{"host": "careers-navitus.icims.com"}``; the registry token is the host label
(``careers-navitus``; the host defaults to ``{token}.icims.com``). Optional config keys:
``detail_title_regex`` (str; default = Workday's widened DEFAULT_DETAIL_TITLE_REGEX),
``max_pages`` (default 100), ``max_jobs`` (default 3000).

List: ``GET https://{host}/jobs/search?ss=1&in_iframe=1&pr={n}`` (n = 0, 1, 2, ...; 50 rows per
page). We use the paginated search HTML rather than ``/sitemap.xml``: the rows carry title,
location, requisition id and category/pay fields that the sitemap lacks, and the sitemap would
still need the same detail fetches. Rows are ``.iCIMS_JobsTable .row`` with ``.title a`` (job URL
``/jobs/<id>/<slug>/job``), ``.header.left`` (location, e.g. ``US-NJ-Cranbury``), ``.header.right``
(requisition id) and ``dl.iCIMS_JobHeaderGroup`` (Category, Position Type, Remote, pay rates ...).
There is no total-jobs count; the pager says "Page X of N" (``.iCIMS_Paging``).

Completeness (strict): ``complete=True`` only when the pager's N is known and pages 0..N-1 were all
fetched with rows, or (no pager) an empty/past-the-end page was reached. A page that adds no new
job links, an empty page before page N, an empty first page (we do not trust "no jobs" on an
unrecognised layout, so an empty board never auto-closes) or hitting max_pages/max_jobs gives
``complete=False`` plus a warning. A past-the-end page renders an error message and no rows.

Stable payload: ingest hashes the payload, so only stable list fields are stored (path, title,
raw location, requisition id, header fields, ``_nd`` gate flag); the volatile description snippet
and relative "posted N days ago" text are not. ``external_id`` is the numeric id from the job URL
path only. ``posted_at`` is only ever the JSON-LD ``datePosted`` (absolute); otherwise None.

Detail: ``GET https://{host}{path}?in_iframe=1`` -> JSON-LD ``JobPosting`` (title, datePosted,
jobLocation, employmentType, description, baseSalary min/max, jobLocationType, hiringOrganization,
occupationalCategory); if the page has none we fall back to HTML selectors (h1, header fields,
``.iCIMS_InfoField_Job``/``.iCIMS_InfoMsg_Job`` sections). Detail is requested only for titles
matching ``detail_title_regex`` (``needs_detail``); other jobs keep list-level fields.

``_map`` rebuilds a RawJob from the stored payload (list fields + optional ``detail``) with no
HTTP.
"""

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import SelectolaxError

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef
from daily_job_tool.adapters.html_list import ConfigError
from daily_job_tool.adapters.registry import register
from daily_job_tool.adapters.workday import DEFAULT_DETAIL_TITLE_REGEX

if TYPE_CHECKING:
    from daily_job_tool.http.client import CrawlClient

MAX_PAGES = 100
MAX_JOBS = 3000
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.icims\.com$")
NOT_PORTALS = {
    "login",
    "www",
    "api",
    "app",
    "cdn",
    "images",
    "provider",
    "developer",
    "support",
    "community",
    "status",
    "static",
}
JOB_PATH_RE = re.compile(r"^/jobs/(\d+)/[^/]*/job/?$")
PAY_RE = re.compile(r"([A-Z]{3})?\s*\$?\s*([\d,]+(?:\.\d+)?)\s*/\s*([A-Za-z]+)")
PERIODS = {"yr": "year", "hr": "hour", "mo": "month", "wk": "week", "day": "day"}
UNAVAILABLE = "UNAVAILABLE"
DEFAULT_RE_FLAGS = re.IGNORECASE


def _text(node: Any) -> str:
    return re.sub(r"\s+", " ", node.text()).strip() if node is not None else ""


def _parse(html: str) -> HTMLParser | None:
    try:
        return HTMLParser(html)
    except (SelectolaxError, ValueError):
        return None


def _iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _format_location(raw: str) -> list[str]:
    """'US-NJ-Cranbury' -> 'Cranbury, NJ'; 'CA-QC-Montreal' -> 'Montreal, QC, CA'; 'US-' -> []."""
    out: list[str] = []
    for part in raw.split("|"):
        part = part.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split("-")]
        if len(bits) >= 2 and re.fullmatch(r"[A-Z]{2}", bits[0]):
            country, rest = bits[0], [b for b in bits[1:] if b]
            if not rest:
                continue
            if len(rest) >= 2 and re.fullmatch(r"[A-Z]{2,3}", rest[0]):
                city = "-".join(rest[1:])
                loc = f"{city}, {rest[0]}" if country == "US" else f"{city}, {rest[0]}, {country}"
            else:
                loc = (
                    ", ".join(reversed(rest))
                    if country == "US"
                    else f"{', '.join(rest)}, {country}"
                )
            out.append(loc)
        else:
            out.append(part)
    return out


def _salary_from_fields(fields: dict[str, str]) -> SalaryRaw | None:
    lo = hi = None
    cur = period = None
    for key, value in fields.items():
        k = key.lower()
        if "pay rate" not in k and "salary" not in k:
            continue
        m = PAY_RE.search(value)
        if not m:
            continue
        amount = float(m.group(2).replace(",", ""))
        cur = cur or m.group(1)
        period = period or PERIODS.get(m.group(3).lower()[:3].rstrip("."), None)
        if "min" in k:
            lo = amount
        elif "max" in k:
            hi = amount
        else:
            lo = lo if lo is not None else amount
    if lo is None and hi is None:
        return None
    return SalaryRaw(min=lo, max=hi, currency=cur, period=period)


def _jsonld_job(doc: HTMLParser) -> dict[str, Any] | None:
    for s in doc.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text(), strict=False)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for it in items if isinstance(items, list) else [items]:
            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                return it
    return None


def _ld_locations(ld: dict[str, Any]) -> list[str]:
    locs = ld.get("jobLocation")
    out: list[str] = []
    for loc in locs if isinstance(locs, list) else [locs]:
        addr = loc.get("address") if isinstance(loc, dict) else None
        if not isinstance(addr, dict):
            continue

        def val(k: str, addr: dict[str, Any] = addr) -> str:
            v = addr.get(k)
            return v.strip() if isinstance(v, str) and v.strip().upper() != UNAVAILABLE else ""

        parts = [val("addressLocality"), val("addressRegion")]
        parts = [p for p in parts if p]
        if parts:
            text = ", ".join(parts)
            if val("addressCountry") not in ("", "US"):
                text += f", {val('addressCountry')}"
        elif val("streetAddress").lower() == "remote":
            text = f"Remote, {val('addressCountry')}" if val("addressCountry") else "Remote"
        else:
            text = val("addressCountry")
        if text and text not in out:
            out.append(text)
    return out


def _detail_from_jsonld(ld: dict[str, Any]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for key in ("datePosted", "employmentType", "description", "jobLocationType"):
        if isinstance(ld.get(key), str) and ld[key].strip():
            d[key] = ld[key]
    if isinstance(ld.get("occupationalCategory"), str):
        d["category"] = ld["occupationalCategory"]
    org = ld.get("hiringOrganization")
    if isinstance(org, dict) and isinstance(org.get("name"), str) and org["name"] != UNAVAILABLE:
        d["org"] = org["name"]
    locs = _ld_locations(ld)
    if locs:
        d["locations"] = locs
    sal = ld.get("baseSalary")
    if isinstance(sal, dict):
        v = sal.get("value") if isinstance(sal.get("value"), dict) else sal
        lo, hi = v.get("minValue"), v.get("maxValue")
        if isinstance(lo, int | float) or isinstance(hi, int | float):
            d["salary"] = {
                "min": lo if isinstance(lo, int | float) else None,
                "max": hi if isinstance(hi, int | float) else None,
                "currency": sal.get("currency") or ld.get("salaryCurrency"),
                "unit": v.get("unitText") if isinstance(v.get("unitText"), str) else None,
            }
    return d


def _detail_from_html(doc: HTMLParser) -> dict[str, Any] | None:
    content = doc.css_first(".iCIMS_JobContent")
    if content is None:
        return None
    d: dict[str, Any] = {}
    parts: list[str] = []
    label = None
    for n in content.css(".iCIMS_InfoMsg"):
        cls = n.attributes.get("class") or ""
        if "iCIMS_InfoField_Job" in cls:
            label = _text(n)
        elif "iCIMS_InfoMsg_Job" in cls:
            body = n.css_first(".iCIMS_Expandable_Text")
            html = (body or n).html or ""
            parts.append(f"<h2>{label}</h2>{html}" if label else html)
            label = None
    if parts:
        d["description"] = "\n".join(parts)
    left = content.css_first(".header.left")
    if left is not None:
        spans = [
            _text(s) for s in left.css("span") if "sr-only" not in (s.attributes.get("class") or "")
        ]
        locs = _format_location(" | ".join(x for x in spans if x))
        if locs:
            d["locations"] = locs
    return d or None


def _fields(row: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in row.css(".iCIMS_JobHeaderTag"):
        k, v = _text(tag.css_first("dt")), _text(tag.css_first("dd"))
        if k and v:
            out[k] = v
    return out


@register
class IcimsAdapter(Adapter):
    name: ClassVar[str] = "icims"
    tier: ClassVar[int] = 2
    hosts: ClassVar[list[str]] = ["*.icims.com"]

    @staticmethod
    def _host(src: SourceRef) -> str:
        host = str(src.config.get("host") or f"{src.token}.icims.com").lower()
        if not HOST_RE.match(host) or host.split(".")[0] in NOT_PORTALS:
            raise ConfigError(f"icims {src.token}: {host} is not a *.icims.com career portal host")
        return host

    @staticmethod
    def _detail_re(src: SourceRef) -> re.Pattern[str]:
        pattern = src.config.get("detail_title_regex") or DEFAULT_DETAIL_TITLE_REGEX
        return re.compile(pattern, DEFAULT_RE_FLAGS)

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        host = self._host(src)
        detail_re = self._detail_re(src)
        max_pages = int(src.config.get("max_pages", MAX_PAGES))
        max_jobs = int(src.config.get("max_jobs", MAX_JOBS))
        jobs: list[RawJob] = []
        seen: set[str] = set()
        warnings: list[str] = []
        board_name: str | None = None
        total_pages = 0
        complete = True
        n = 0
        while True:
            html = await http.get_text(f"https://{host}/jobs/search?ss=1&in_iframe=1&pr={n}")
            doc = _parse(html)
            rows = doc.css(".iCIMS_JobsTable .row") if doc is not None else []
            if n == 0 and doc is not None:
                m = re.match(r"\s*Job Listings at\s+(.+?)\s*$", _text(doc.css_first("title")))
                board_name = m.group(1) if m else None
            if doc is not None:
                for pg in doc.css(".iCIMS_Paging"):
                    for m2 in re.finditer(r"(?<![A-Za-z])of\s+(\d+)\b", _text(pg)):
                        total_pages = max(total_pages, int(m2.group(1)))
            if not rows:
                if n == 0:
                    complete = False
                    warnings.append(
                        f"icims {src.token}: first page has no job rows; not trusting an empty "
                        "listing (jobs will not auto-close)"
                    )
                elif total_pages and n < total_pages:
                    complete = False
                    warnings.append(
                        f"icims {src.token}: page {n} is empty but the pager reports {total_pages}"
                    )
                break
            new = 0
            for row in rows:
                job = self._build(row, host, detail_re)
                if job is None:
                    warnings.append("skipped malformed row")
                    continue
                if job.external_id not in seen:
                    seen.add(job.external_id)
                    jobs.append(job)
                    new += 1
            if not new:
                complete = False
                warnings.append(
                    f"icims {src.token}: page {n} added no new jobs (pagination ignored); stopping"
                )
                break
            n += 1
            if total_pages and n >= total_pages:
                break  # pager says that was the last page
            if n >= max_pages or len(jobs) >= max_jobs:
                complete = False
                warnings.append(
                    f"icims {src.token}: portal too large for full enumeration; jobs will not "
                    f"auto-close (cap hit at {len(jobs)} jobs / {n} pages)"
                )
                break
        return FetchResult(jobs=jobs, complete=complete, board_name=board_name, warnings=warnings)

    def _build(self, row: Any, host: str, detail_re: re.Pattern[str]) -> RawJob | None:
        a = row.css_first(".title a")
        href = a.attributes.get("href") if a is not None else None
        if not href:
            return None
        path = urlsplit(urljoin(f"https://{host}/", href)).path
        title = _text(a.css_first("h3")) or (a.attributes.get("title") or "").split(" - ", 1)[-1]
        if not title or not JOB_PATH_RE.match(path):
            return None
        left, right = row.css_first(".header.left"), row.css_first(".header.right")

        def value(box: Any) -> str:
            spans = box.css("span") if box is not None else []
            return " | ".join(
                _text(s) for s in spans if "sr-only" not in (s.attributes.get("class") or "")
            ).strip(" |")

        payload: dict[str, Any] = {
            "_host": host,
            "path": path,
            "title": title,
            "location": value(left),
            "req_id": value(right),
            "fields": _fields(row),
            "_nd": bool(detail_re.search(title)),
        }
        return self._map(payload)

    @staticmethod
    def _map(payload: Any) -> RawJob | None:
        if not isinstance(payload, dict):
            return None
        title, path, host = payload.get("title"), payload.get("path"), payload.get("_host")
        m = JOB_PATH_RE.match(path) if isinstance(path, str) else None
        if not title or not m or not host:
            return None
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else None
        d = detail or {}
        url = f"https://{host}{path}"
        locs = d.get("locations") or _format_location(str(payload.get("location") or ""))
        workplace = None
        if str(d.get("jobLocationType", "")).upper() == "TELECOMMUTE" or (
            str(fields.get("Remote", "")).lower() == "yes"
        ):
            workplace = "remote"
        salary: SalaryRaw | None = None
        if isinstance(d.get("salary"), dict):
            s = d["salary"]
            salary = SalaryRaw(
                min=s.get("min"),
                max=s.get("max"),
                currency=s.get("currency"),
                period=PERIODS.get(str(s.get("unit") or "").lower()[:3]) or s.get("unit"),
            )
        elif fields:
            salary = _salary_from_fields(fields)
        emp = d.get("employmentType") or fields.get("Position Type") or None
        desc = d.get("description")
        return RawJob(
            external_id=m.group(1),
            title=str(title),
            url=url,
            apply_url=url,
            locations_raw=list(locs),
            workplace_raw=workplace,
            department_raw=d.get("category") or fields.get("Category") or None,
            employment_type_raw=emp,
            description_html=desc if isinstance(desc, str) else None,
            posted_at=_iso(d.get("datePosted")),
            salary=salary,
            needs_detail=bool(payload.get("_nd")) and detail is None,
            payload=payload,
        )

    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        host = self._host(src)
        html = await http.get_text(f"https://{host}{job.payload['path']}?in_iframe=1")
        doc = _parse(html)
        if doc is None:
            raise ValueError(f"icims {src.token}: unparseable job page")
        ld = _jsonld_job(doc)
        detail = _detail_from_jsonld(ld) if ld else None
        if not detail or "description" not in detail:
            fallback = _detail_from_html(doc)
            if fallback:
                detail = {**(fallback), **(detail or {})}
        if not detail:
            raise ValueError(f"icims {src.token}: job page has no JSON-LD or recognised content")
        mapped = self._map({**job.payload, "detail": detail})
        return mapped if mapped is not None else job

    @classmethod
    def match_url(cls, url: str) -> tuple[str, dict[str, Any]] | None:
        try:
            host = (urlsplit(url.strip()).hostname or "").lower()
        except ValueError:
            return None
        if not HOST_RE.match(host):
            return None
        label = host.split(".")[0]
        if label in NOT_PORTALS or re.fullmatch(r"cdn\d*", label):
            return None
        return label, {"host": host}
