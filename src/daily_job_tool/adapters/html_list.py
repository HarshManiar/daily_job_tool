"""Config-driven "HTML list + detail pages" adapter (tier 3).

One adapter serves many bespoke careers sites; everything site-specific lives in the source's
`config` JSON (seeds.csv `config` column). Schema:

    {
      "list_url": "https://www.example.com/careers/",          # required
      "mode": "html" | "sitemap",                              # default "html"
      "job_link_selector": "a.job-link",                       # css; required for mode "html"
      "link_filter_regex": "/careers/[^/]+/?$",                # optional re.search on the abs URL
      "base_url": "https://www.example.com",                   # optional; resolves relative links
      "pagination": {"param": "page", "max_pages": 20},        # optional (mode "html" only)
      "max_jobs": 1000,                                        # optional safety cap
      "id_from": "url_path" | "query:<name>" | "<regex>",      # default "url_path"
      "detail": {
        "title": "h1",                                         # css (default "h1")
        "location": "css",  "department": "css",  "employment_type": "css",
        "description": "css",
        "posted_at": {"selector": "<see below>"}
      }
    }

Selectors are CSS (selectolax/lexbor), or `label:Text` (the element whose text is "Text", value =
its next sibling element; for `<h3>Location</h3><p>NJ</p>` and `<dt>/<dd>` layouts), or a dict
`{"selector": "...", "attr": "content"}` to read an attribute instead of the text.
`posted_at.selector` also accepts `jsonld` (JobPosting.datePosted), `yoast` (the Yoast/WordPress
schema graph's `datePublished`) and `meta:<property>` (e.g. `meta:article:published_time`).

Flow: fetch the list (html: collect `job_link_selector` hrefs; sitemap: every `<loc>`; `lastmod`
is never used as a posted date) -> absolute, de-duplicated URLs -> every job is `needs_detail`.
`fetch_detail` fetches the posting page and extracts fields; a schema.org `JobPosting` JSON-LD
block wins over selectors when present (title, datePosted, jobLocation, employmentType,
description, baseSalary). `posted_at` is only set from a trustworthy source, else None.
`external_id` is derived from the URL (`id_from`). The list payload is just the URL (stable, so
unchanged postings are not re-fetched); the detail payload carries the extracted fields.

`complete` is True only when the whole list was enumerated: an empty list (probably broken
selectors), a hit page/job cap, or a failed later page make it False with a warning.
"""

import html as htmllib
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import parse_qs, urldefrag, urlencode, urljoin, urlsplit, urlunsplit

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import SelectolaxError

from daily_job_tool.adapters.base import Adapter, FetchResult, RawJob, SalaryRaw, SourceRef
from daily_job_tool.adapters.registry import register
from daily_job_tool.http.errors import NotFound

if TYPE_CHECKING:
    from selectolax.lexbor import LexborNode

    from daily_job_tool.http.client import CrawlClient

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_JOBS = 1000
_TOP_KEYS = {
    "list_url", "mode", "job_link_selector", "link_filter_regex", "pagination", "detail",
    "id_from", "base_url", "max_jobs",
}  # fmt: skip
_DETAIL_KEYS = {"title", "location", "department", "description", "posted_at", "employment_type"}
_LABEL_TAGS = "h2,h3,h4,h5,h6,dt,th,strong,b,label,span,p"


class ConfigError(ValueError):
    """Invalid html_list source config."""


# --------------------------------------------------------------------------- config
class _Cfg:
    def __init__(self, raw: dict[str, Any], token: str) -> None:
        def bad(msg: str) -> ConfigError:
            return ConfigError(f"html_list {token}: {msg}")

        if not isinstance(raw, dict) or not raw:
            raise bad("empty config; list_url and job_link_selector are required")
        unknown = set(raw) - _TOP_KEYS
        if unknown:
            raise bad(f"unknown config key(s) {sorted(unknown)}; allowed: {sorted(_TOP_KEYS)}")
        self.list_url = raw.get("list_url")
        if not isinstance(self.list_url, str) or urlsplit(self.list_url).scheme not in (
            "http",
            "https",
        ):
            raise bad("list_url must be an absolute http(s) URL")
        self.mode = raw.get("mode", "html")
        if self.mode not in ("html", "sitemap"):
            raise bad(f"mode must be 'html' or 'sitemap', got {self.mode!r}")
        self.link_sel = raw.get("job_link_selector")
        if self.mode == "html":
            if not isinstance(self.link_sel, str) or not self.link_sel.strip():
                raise bad("job_link_selector is required for mode 'html'")
            self._check_css(self.link_sel, "job_link_selector", bad)
        self.link_rx: re.Pattern[str] | None = None
        if raw.get("link_filter_regex"):
            try:
                self.link_rx = re.compile(raw["link_filter_regex"])
            except (re.error, TypeError) as exc:
                raise bad(f"link_filter_regex is not a valid regex: {exc}") from exc
        self.base_url = raw.get("base_url") or self.list_url
        if urlsplit(self.base_url).scheme not in ("http", "https"):
            raise bad("base_url must be an absolute http(s) URL")
        pag = raw.get("pagination")
        self.page_param: str | None = None
        self.max_pages = DEFAULT_MAX_PAGES
        if pag is not None:
            if self.mode == "sitemap":
                raise bad("pagination is not supported with mode 'sitemap'")
            if not isinstance(pag, dict) or not isinstance(pag.get("param"), str):
                raise bad("pagination must be an object with a string 'param'")
            self.page_param = pag["param"]
            mp = pag.get("max_pages", DEFAULT_MAX_PAGES)
            if not isinstance(mp, int) or isinstance(mp, bool) or mp < 1:
                raise bad("pagination.max_pages must be an integer >= 1")
            self.max_pages = mp
        mj = raw.get("max_jobs", DEFAULT_MAX_JOBS)
        if not isinstance(mj, int) or isinstance(mj, bool) or mj < 1:
            raise bad("max_jobs must be an integer >= 1")
        self.max_jobs = mj
        self.id_from = raw.get("id_from", "url_path")
        self.id_rx: re.Pattern[str] | None = None
        if not isinstance(self.id_from, str) or not self.id_from:
            raise bad("id_from must be 'url_path', 'query:<param>' or a regex with one group")
        if self.id_from != "url_path" and not self.id_from.startswith("query:"):
            try:
                self.id_rx = re.compile(self.id_from)
            except re.error as exc:
                raise bad(
                    f"id_from is neither url_path/query:<name> nor a valid regex: {exc}"
                ) from exc
            if self.id_rx.groups < 1:
                raise bad("id_from regex needs one capture group, e.g. '/jobs/(\\d+)'")
        elif self.id_from == "query:":
            raise bad("id_from 'query:' needs a parameter name, e.g. 'query:id'")
        det = raw.get("detail", {})
        if not isinstance(det, dict):
            raise bad("detail must be an object of selectors")
        extra = set(det) - _DETAIL_KEYS
        if extra:
            raise bad(f"unknown detail key(s) {sorted(extra)}; allowed: {sorted(_DETAIL_KEYS)}")
        self.detail: dict[str, tuple[str, str | None]] = {}
        for key, val in det.items():
            sel, attr = (
                (val.get("selector"), val.get("attr")) if isinstance(val, dict) else (val, None)
            )
            if not isinstance(sel, str) or not sel.strip():
                raise bad(f"detail.{key} must be a selector string or {{'selector': ...}}")
            if not (
                sel.startswith(("label:", "meta:")) or (key == "posted_at" and sel in _DATE_MAGIC)
            ):
                self._check_css(sel, f"detail.{key}", bad)
            self.detail[key] = (sel, attr)
        self.detail.setdefault("title", ("h1", None))

    @staticmethod
    def _check_css(sel: str, where: str, bad: Any) -> None:
        try:
            HTMLParser("<html></html>").css(sel)
        except SelectolaxError as exc:
            raise bad(f"{where}: invalid CSS selector {sel!r}") from exc


_DATE_MAGIC = {"jsonld", "yoast"}


# --------------------------------------------------------------------------- helpers
def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _node_text(n: "LexborNode") -> str:
    return _clean(n.text(deep=True, separator=" ", strip=True))


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    d: datetime | None = None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                d = datetime.strptime(v, fmt)  # noqa: DTZ007 - made aware just below
                break
            except ValueError:
                continue
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def _walk(node: Any) -> Any:
    """Yield every dict in a JSON-LD structure."""
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for v in node.values():
            if isinstance(v, dict | list):
                yield from _walk(v)


def _jsonld_blocks(tree: HTMLParser) -> list[Any]:
    out: list[Any] = []
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            out.append(json.loads(s.text() or ""))
        except ValueError:
            continue
    return out


def _types(d: dict[str, Any]) -> list[Any]:
    t = d.get("@type")
    return t if isinstance(t, list) else [t]


def _find_jobposting(blocks: list[Any]) -> dict[str, Any] | None:
    for b in blocks:
        for d in _walk(b):
            if "JobPosting" in _types(d):
                return d
    return None


def _yoast_date(blocks: list[Any]) -> str | None:
    best: str | None = None
    for b in blocks:
        for d in _walk(b):
            v = d.get("datePublished")
            if isinstance(v, str) and v:
                if any(t in _types(d) for t in ("WebPage", "Article", "ProfilePage")):
                    return v
                best = best or v
    return best


def _ld_locations(jp: dict[str, Any]) -> list[str]:
    raw = jp.get("jobLocation")
    items = raw if isinstance(raw, list) else [raw] if raw else []
    out: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        addr = it.get("address", it)
        if isinstance(addr, str):
            parts = [addr]
        elif isinstance(addr, dict):
            country = addr.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), country]
        else:
            continue
        s = ", ".join(_clean(str(p)) for p in parts if isinstance(p, str) and p.strip())
        if s and s not in out:
            out.append(s)
    return out


def _ld_salary(jp: dict[str, Any]) -> SalaryRaw | None:
    bs = jp.get("baseSalary")
    if not isinstance(bs, dict):
        return None
    cur = bs.get("currency")
    val = bs.get("value", bs)
    lo = hi = None
    period = None
    if isinstance(val, dict):
        lo, hi = val.get("minValue"), val.get("maxValue")
        if lo is None and hi is None and isinstance(val.get("value"), int | float):
            lo = hi = val["value"]
        unit = val.get("unitText")
        period = unit.lower() if isinstance(unit, str) else None
    elif isinstance(val, int | float):
        lo = hi = val
    nums = [x for x in (lo, hi) if isinstance(x, int | float) and not isinstance(x, bool)]
    if not nums:
        return None
    return SalaryRaw(
        min=float(lo) if isinstance(lo, int | float) else None,
        max=float(hi) if isinstance(hi, int | float) else None,
        currency=cur if isinstance(cur, str) else None,
        period=period,
    )


def _ld_description(v: Any) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    return htmllib.unescape(v) if "<" not in v and "&lt;" in v else v


def _next_element(n: "LexborNode") -> "LexborNode | None":
    cur = n.next
    while cur is not None and cur.tag.startswith("-"):  # text/comment nodes
        cur = cur.next
    return cur


def _select_nodes(tree: HTMLParser, sel: str) -> "list[LexborNode]":
    if sel.startswith("label:"):
        want = sel[6:].strip().rstrip(":").lower()
        for n in tree.css(_LABEL_TAGS):
            if _node_text(n).rstrip(":").strip().lower() == want:
                val = _next_element(n)
                if val is None and n.parent is not None:
                    val = _next_element(n.parent)
                if val is not None:
                    return [val]
        return []
    return tree.css(sel)


def _id_for(url: str, cfg: _Cfg) -> str | None:
    p = urlsplit(url)
    if cfg.id_from == "url_path":
        return p.path.strip("/") or None
    if cfg.id_from.startswith("query:"):
        vals = parse_qs(p.query).get(cfg.id_from[6:])
        return vals[0] if vals and vals[0] else None
    assert cfg.id_rx is not None
    m = cfg.id_rx.search(url)
    return (m.group(1) if m else None) or None


def _humanize(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    if slug == "job":  # e.g. /careers/<slug>/job/
        slug = urlsplit(url).path.rstrip("/").rsplit("/", 2)[-2]
    return re.sub(r"[-_]+", " ", slug).strip().title() or url


# --------------------------------------------------------------------------- adapter
@register
class HtmlListAdapter(Adapter):
    name: ClassVar[str] = "html_list"
    tier: ClassVar[int] = 3
    hosts: ClassVar[list[str]] = ["*"]  # config-driven: any host named by source.config

    async def fetch_jobs(self, src: SourceRef, http: "CrawlClient") -> FetchResult:
        cfg = _Cfg(src.config, src.token)
        warnings: list[str] = []
        complete = True
        urls: list[str] = []
        seen: set[str] = set()

        def add(u: str) -> bool:
            u = urldefrag(urljoin(cfg.base_url, u.strip()))[0]
            if urlsplit(u).scheme not in ("http", "https") or u in seen:
                return False
            if cfg.link_rx is not None and not cfg.link_rx.search(u):
                return False
            seen.add(u)
            urls.append(u)
            return True

        if cfg.mode == "sitemap":
            body = await http.get_text(cfg.list_url)
            for u in self._sitemap_urls(body, src.token):
                add(u)
        else:
            page = 1
            while True:
                url = self._page_url(cfg, page)
                try:
                    body = await http.get_text(url)
                except NotFound:
                    if page == 1:
                        raise
                    break  # ran past the last page
                except Exception as exc:  # noqa: BLE001
                    if page == 1:
                        raise
                    warnings.append(f"page {page} failed ({type(exc).__name__}); list incomplete")
                    complete = False
                    break
                tree = HTMLParser(body)
                new = 0
                for a in tree.css(cfg.link_sel or ""):
                    href = a.attributes.get("href")
                    if not href:
                        inner = a.css_first("a[href]")
                        href = inner.attributes.get("href") if inner is not None else None
                    if href and add(href):
                        new += 1
                if cfg.page_param is None or new == 0:
                    break
                if page >= cfg.max_pages:
                    warnings.append(f"hit max_pages={cfg.max_pages}; list incomplete")
                    complete = False
                    break
                page += 1

        if len(urls) > cfg.max_jobs:
            warnings.append(f"{len(urls)} job links; capped at max_jobs={cfg.max_jobs}")
            urls = urls[: cfg.max_jobs]
            complete = False
        if not urls:
            warnings.append("no job links found (empty list or selectors no longer match)")
            complete = False  # never let a broken selector close every posting

        jobs: list[RawJob] = []
        ids: set[str] = set()
        for u in urls:
            ext = _id_for(u, cfg)
            if ext is None:
                warnings.append(f"no id derivable from {u}; skipped")
                complete = False
                continue
            if ext in ids:
                warnings.append(f"duplicate id {ext!r} from {u}; skipped")
                continue
            ids.add(ext)
            jobs.append(
                RawJob(
                    external_id=ext,
                    title=_humanize(u),  # placeholder; the detail page supplies the real title
                    url=u,
                    apply_url=u,
                    needs_detail=True,
                    payload={"url": u},
                )
            )
        return FetchResult(jobs=jobs, complete=complete, warnings=warnings)

    @staticmethod
    def _page_url(cfg: _Cfg, page: int) -> str:
        if cfg.page_param is None or page == 1:
            return cfg.list_url
        p = urlsplit(cfg.list_url)
        q = parse_qs(p.query, keep_blank_values=True)
        q[cfg.page_param] = [str(page)]
        return urlunsplit(p._replace(query=urlencode(q, doseq=True)))

    @staticmethod
    def _sitemap_urls(body: str, token: str) -> list[str]:
        try:
            root = SafeET.fromstring(body)
        except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
            raise ValueError(
                f"html_list {token}: list_url is not a valid XML sitemap ({exc})"
            ) from exc
        tag = root.tag.rsplit("}", 1)[-1]
        if tag == "sitemapindex":
            raise ConfigError(
                f"html_list {token}: list_url is a sitemap index; point it at a urlset sitemap"
            )
        if tag != "urlset":
            raise ValueError(f"html_list {token}: unexpected sitemap root <{tag}>")
        return [
            (el.text or "").strip()
            for el in root.iter()
            if el.tag.rsplit("}", 1)[-1] == "loc" and (el.text or "").strip()
        ]

    # ------------------------------------------------------------------ detail
    async def fetch_detail(self, src: SourceRef, job: RawJob, http: "CrawlClient") -> RawJob:
        cfg = _Cfg(src.config, src.token)
        body = await http.get_text(job.url)
        f = self._extract(body, cfg)
        if not f["title"]:
            raise ValueError(f"no title found on {job.url}")
        payload = {"url": job.url, "external_id": job.external_id, **f}
        return self._build(payload, job)

    @staticmethod
    def _extract(body: str, cfg: _Cfg) -> dict[str, Any]:
        tree = HTMLParser(body)
        blocks = _jsonld_blocks(tree)
        jp = _find_jobposting(blocks)

        def sel_text(key: str, many: bool = False) -> list[str]:
            if key not in cfg.detail:
                return []
            sel, attr = cfg.detail[key]
            if key == "posted_at":
                return []
            out: list[str] = []
            for n in _select_nodes(tree, sel):
                t = _clean(n.attributes.get(attr)) if attr else _node_text(n)
                if t and t not in out:
                    out.append(t)
                if not many and out:
                    break
            return out

        title = employment = description = None
        locations: list[str] = []
        salary: SalaryRaw | None = None
        workplace: str | None = None
        posted: str | None = None
        if jp is not None:
            t = jp.get("title") or jp.get("name")
            title = _clean(htmllib.unescape(t)) if isinstance(t, str) else None
            locations = _ld_locations(jp)
            et = jp.get("employmentType")
            if isinstance(et, list):
                et = ", ".join(str(x) for x in et)
            employment = str(et).replace("_", " ").lower() if et else None
            description = _ld_description(jp.get("description"))
            salary = _ld_salary(jp)
            if str(jp.get("jobLocationType", "")).upper() == "TELECOMMUTE":
                workplace = "remote"
            posted = jp.get("datePosted") if isinstance(jp.get("datePosted"), str) else None
        title = title or next(iter(sel_text("title")), None)
        locations = locations or sel_text("location", many=True)
        employment = employment or next(iter(sel_text("employment_type")), None)
        department = next(iter(sel_text("department")), None)
        if description is None and "description" in cfg.detail:
            sel, _ = cfg.detail["description"]
            nodes = _select_nodes(tree, sel)
            if nodes:
                for junk in nodes[0].css("script,style,noscript"):
                    junk.decompose()
                description = (nodes[0].inner_html or "").strip() or None
        if posted is None and "posted_at" in cfg.detail:
            sel, attr = cfg.detail["posted_at"]
            if sel == "jsonld":
                pass  # JobPosting already consulted above
            elif sel == "yoast":
                posted = _yoast_date(blocks)
            elif sel.startswith("meta:"):
                m = tree.css_first(f'meta[property="{sel[5:]}"], meta[name="{sel[5:]}"]')
                posted = m.attributes.get("content") if m is not None else None
            else:
                nodes = _select_nodes(tree, sel)
                if nodes:
                    n = nodes[0]
                    posted = (
                        n.attributes.get(attr or "datetime") or n.attributes.get("content")
                        or _node_text(n)
                    )  # fmt: skip
        pd = _dt(posted)
        return {
            "title": title,
            "locations": locations,
            "department": department,
            "employment_type": employment,
            "workplace": workplace,
            "description_html": description,
            "posted_at": pd.isoformat() if pd else None,
            "salary": salary.model_dump() if salary else None,
            "detail": True,
        }

    @staticmethod
    def _build(payload: dict[str, Any], job: RawJob) -> RawJob:
        sal = payload.get("salary")
        return job.model_copy(
            update={
                "title": payload["title"],
                "locations_raw": list(payload.get("locations") or []),
                "department_raw": payload.get("department"),
                "employment_type_raw": payload.get("employment_type"),
                "workplace_raw": payload.get("workplace"),
                "description_html": payload.get("description_html"),
                "posted_at": _dt(payload.get("posted_at")),
                "salary": SalaryRaw(**sal) if isinstance(sal, dict) else None,
                "needs_detail": False,
                "payload": payload,
            }
        )

    @classmethod
    def _map(cls, payload: Any) -> RawJob | None:
        """Rebuild a RawJob from a stored detail payload (no HTTP)."""
        if not isinstance(payload, dict) or not payload.get("detail") or not payload.get("title"):
            return None
        url, ext = payload.get("url"), payload.get("external_id")
        if not url or not ext:
            return None
        base = RawJob(
            external_id=str(ext), title=payload["title"], url=url, apply_url=url, payload=payload
        )
        return cls._build(payload, base)
