"""Lightweight ATS detector (ARCHITECTURE.md section 9, candidate pages + signature scan).

All network I/O goes through CrawlClient (robots, rate limits, UA, blocklist). A bot wall
(`Blocked`) ends work on that company; we never retry or work around it.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from daily_job_tool.detect.patterns import LINKEDIN_ONLY, match_text, match_url
from daily_job_tool.http.client import CrawlClient
from daily_job_tool.http.errors import (
    Blocked,
    CrawlError,
    NotFound,
    RobotsDisallowed,
)

MAX_FETCHES = 6
COMPANY_TIMEOUT = 60.0
CAREERS_RX = re.compile(
    r"careers?|jobs?|join\s+us|work\s+with\s+us|open\s+roles?|hiring|opportunities", re.I
)
_URL_RX = re.compile(r"(?:https?:)?//[^\s\"'<>\\)]+", re.I)
# a deeper "list of jobs" page reached from a careers landing page
_JOBS_SUBPATH_RX = re.compile(
    r"/(?:jobs|open-roles?|open-positions|openings|current-openings|positions|all-jobs|"
    r"job-search|search-jobs|join-us)/?$",
    re.I,
)
_JOBS_TEXT_RX = re.compile(
    r"(?:view|see|explore|browse|search|all)\s+(?:all\s+)?(?:open\s+)?(?:jobs|roles|openings|positions)"
    r"|open\s+(?:roles|positions|jobs)",
    re.I,
)
_CAREERPUCK_API = "https://api.careerpuck.com/v1/public/job-boards/"
_WORKABLE_CANON_RX = re.compile(r"https?://apply\.workable\.com/([a-z0-9][a-z0-9_-]*)/j/", re.I)
_JOBLINK_RX = re.compile(r"/(jobs?|positions?|openings?|roles?|vacanc\w*|careers?)/[^/?#]+", re.I)

# confidence by evidence kind
CONF = {
    "final_url": 0.95,
    "iframe": 0.95,
    "script": 0.9,
    "link": 0.9,
    "anchor": 0.85,
    "inline": 0.7,
}


@dataclass
class Detection:
    ats: str = "unknown"
    token: str | None = None
    careers_url: str | None = None
    confidence: float = 0.0
    evidence: str = ""
    jsonld_jobposting: bool = False
    status: str = "unreachable"
    config: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    fetches: int = 0


def _has_jobposting(node: Any) -> bool:
    if isinstance(node, list):
        return any(_has_jobposting(n) for n in node)
    if isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "JobPosting" in types:
            return True
        return any(_has_jobposting(v) for v in node.values() if isinstance(v, dict | list))
    return False


def has_jsonld_jobposting(tree: HTMLParser) -> bool:
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text() or "")
        except ValueError:
            continue
        if _has_jobposting(data):
            return True
    return False


def _registrable(host: str) -> str:
    return ".".join(host.split(".")[-2:])


@dataclass
class _Hit:
    ats: str
    token: str | None
    config: dict[str, Any]
    conf: float
    evidence: str
    url: str


def scan_page(final_url: str, html: str) -> tuple[list[_Hit], HTMLParser]:
    """All ATS signatures on one page, best first."""
    hits: list[_Hit] = []
    tree = HTMLParser(html)

    def add(url: str, kind: str, label: str) -> None:
        m = match_url(url)
        if m:
            hits.append(_Hit(m[0], m[1], m[2], CONF[kind], f"{label}:{url}"[:200], url))

    add(final_url, "final_url", "final_url")
    for sel, attr, kind in (
        ("iframe[src]", "src", "iframe"),
        ("script[src]", "src", "script"),
        ("link[href]", "href", "link"),
        ("a[href]", "href", "anchor"),
        ("form[action]", "action", "anchor"),
    ):
        for n in tree.css(sel):
            v = n.attributes.get(attr)
            if v:
                add(urljoin(final_url, v), kind, f"{kind}")
    for n in tree.css("#rr-job-board[data-job-board-id]"):  # Rippling embed
        tok = (n.attributes.get("data-job-board-id") or "").strip()
        if tok:
            hits.append(_Hit("rippling", tok, {}, CONF["script"], f"rr-job-board:{tok}", final_url))
    for u in set(_URL_RX.findall(html)):
        add("https:" + u if u.startswith("//") else u, "inline", "inline_url")
    if not hits:
        t = match_text(html)
        if t:
            hits.append(_Hit(t[0], None, {}, 0.6, f"inline_keyword:{t[1]}", final_url))
    hits.sort(key=lambda h: (-h.conf, h.token is None))
    return hits, tree


def _careers_anchor(tree: HTMLParser, base: str) -> str | None:
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        text = " ".join((a.text() or "").split())
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if CAREERS_RX.search(text) or CAREERS_RX.search(urlsplit(href).path):
            full = urljoin(base, href)
            if urlsplit(full).scheme in ("http", "https"):
                return full
    return None


def _jobs_followup(tree: HTMLParser, base: str, seen: set[str]) -> str | None:
    """A link from a careers landing page to the real job list (same registrable domain,
    any subdomain such as careers.<domain>), e.g. /careers/jobs, /open-roles."""
    base_sp = urlsplit(base)
    reg = _registrable(base_sp.hostname or "")
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base, href).split("#", 1)[0]
        sp = urlsplit(full)
        if sp.scheme not in ("http", "https") or _registrable(sp.hostname or "") != reg:
            continue
        same_page = sp.hostname == base_sp.hostname and sp.path.rstrip("/") == base_sp.path.rstrip(
            "/"
        )
        if same_page or full.rstrip("/") in seen:
            continue
        text = " ".join((a.text() or "").split())
        if _JOBS_SUBPATH_RX.search(sp.path) or _JOBS_TEXT_RX.search(text):
            return full
    return None


async def _resolve_careerpuck(client: CrawlClient, slug: str) -> tuple[str, str] | None:
    """CareerPuck board slug -> (underlying ATS, its board token), via CareerPuck's public API."""
    data = await client.get_json(_CAREERPUCK_API + slug)
    if not isinstance(data, dict):
        return None
    jobs = data.get("jobs")
    first = jobs[0] if isinstance(jobs, list) and jobs and isinstance(jobs[0], dict) else {}
    board = data.get("jobBoard")
    if not isinstance(board, dict):
        board = first.get("jobBoard") if isinstance(first.get("jobBoard"), dict) else {}
    platform = board.get("atsSourcePlatform") or data.get("atsSourcePlatform")
    platform = platform or first.get("atsSourcePlatform")
    token = board.get("atsSourceId") or slug
    if not isinstance(platform, str) or not platform.strip():
        return None
    return platform.strip().lower(), str(token)


async def _resolve_workable(client: CrawlClient, shortcode: str) -> str | None:
    """apply.workable.com/j/<shortcode> -> account slug, read from the canonical URL."""
    resp = await client.request("GET", f"https://apply.workable.com/j/{shortcode}")
    m = _WORKABLE_CANON_RX.search(str(resp.url))
    if m:
        return m.group(1)
    tree = HTMLParser(resp.text)
    for sel, attr in (('link[rel="canonical"]', "href"), ('meta[property="og:url"]', "content")):
        n = tree.css_first(sel)
        m = _WORKABLE_CANON_RX.search(n.attributes.get(attr) or "") if n is not None else None
        if m:
            return m.group(1)
    return None


def _linkedin_jobs_link(tree: HTMLParser, base: str) -> str | None:
    for a in tree.css("a[href]"):
        full = urljoin(base, a.attributes.get("href") or "")
        m = match_url(full)
        if m and m[0] == LINKEDIN_ONLY:
            return full
    return None


async def _resolve_indirect(client: CrawlClient, d: Detection) -> None:
    """One extra fetch for skins/shortlinks that hide the real board (CareerPuck, Workable /j/)."""
    try:
        if d.ats == "careerpuck" and d.token:
            d.fetches += 1
            got = await _resolve_careerpuck(client, d.token)
            if got:
                d.evidence = f"careerpuck:{d.token}->{got[0]}:{got[1]}"
                d.ats, d.token, d.config = got[0], got[1], {}
            else:
                d.notes = (d.notes + "; careerpuck: underlying ATS unknown").strip("; ")
        elif d.ats == "workable" and not d.token and d.config.get("shortcode"):
            d.fetches += 1
            slug = await _resolve_workable(client, d.config["shortcode"])
            if slug:
                d.evidence = f"workable_shortcode:{d.config['shortcode']}->{slug}"
                d.token, d.config = slug, {}
            else:
                d.notes = (d.notes + "; workable: no canonical account slug").strip("; ")
    except Exception as exc:  # noqa: BLE001 - resolution is best-effort
        d.notes = (d.notes + f"; {d.ats} resolve failed {type(exc).__name__}").strip("; ")


async def _detect(client: CrawlClient, domain: str, hints: dict[str, Any], d: Detection) -> None:
    domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    home = f"https://{domain}"
    queue: list[str] = []
    if hints.get("careers_url"):
        queue.append(hints["careers_url"])
    queue.append(home)
    fallbacks = [
        f"{home}/careers",
        f"{home}/jobs",
        f"{home}/careers/jobs",
        f"{home}/open-roles",
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
    ]
    seen: set[str] = set()
    pages_ok = 0
    robots_hits = 0
    careers_page: str | None = None
    careers_tree: HTMLParser | None = None
    linkedin: str | None = None
    discovered = False
    best: _Hit | None = None
    notes: list[str] = []

    while d.fetches < MAX_FETCHES:
        if not queue:
            if not discovered:
                discovered = True
            queue.extend(fallbacks)
            fallbacks = []
            if not queue:
                break
        url = queue.pop(0)
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        d.fetches += 1
        try:
            resp = await client.request("GET", url)
        except Blocked as exc:
            d.status = "bot_wall"
            notes.append(f"blocked {exc.status} at {url}")
            if best is None:
                d.notes = "; ".join(notes)
                return
            break
        except RobotsDisallowed:
            robots_hits += 1
            notes.append(f"robots disallow {url}")
            continue
        except NotFound:
            continue
        except CrawlError as exc:
            notes.append(f"{type(exc).__name__} {url}"[:120])
            continue
        except Exception as exc:  # noqa: BLE001 - never abort the batch
            notes.append(f"{type(exc).__name__} {url}"[:120])
            continue
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code >= 400 or ("html" not in ctype and ctype):
            continue
        pages_ok += 1
        final = str(resp.url)
        hits, tree = scan_page(final, resp.text)
        if has_jsonld_jobposting(tree):
            d.jsonld_jobposting = True
        non_li = [h for h in hits if h.ats != LINKEDIN_ONLY]
        if non_li and (best is None or non_li[0].conf > best.conf):
            best = non_li[0]
        if best is not None and best.conf >= 0.85:
            break
        li = _linkedin_jobs_link(tree, final)
        if li:
            linkedin = li
        is_home = key == home
        if is_home:
            link = _careers_anchor(tree, final)
            if link and link.rstrip("/") not in seen:
                queue.insert(0, link)
            discovered = True
        else:
            deeper = _jobs_followup(tree, final, seen)
            if deeper and deeper.rstrip("/") not in seen:
                queue.insert(0, deeper)
            if careers_page is None:
                careers_page, careers_tree = final, tree
        if is_home and CAREERS_RX.search(urlsplit(final).path):
            careers_page, careers_tree = final, tree

    d.fetches = min(d.fetches, MAX_FETCHES)
    d.notes = "; ".join(notes)[:300]
    if best is not None:
        d.ats, d.token, d.config = best.ats, best.token, best.config
        d.confidence, d.evidence, d.careers_url = best.conf, best.evidence, best.url
        d.status = "detected"
        await _resolve_indirect(client, d)
        return
    if pages_ok == 0:
        d.status = "blocked_by_robots" if robots_hits else "unreachable"
        return
    if careers_page is None:
        if linkedin:
            d.status, d.ats, d.careers_url = "linkedin_only", LINKEDIN_ONLY, linkedin
            d.evidence = f"linkedin:{linkedin}"
            d.confidence = 0.7
        elif robots_hits:
            d.status = "blocked_by_robots"
        else:
            d.status = "no_careers_page"
        return
    d.careers_url = careers_page
    if linkedin and not d.jsonld_jobposting:
        d.status, d.ats = "linkedin_only", LINKEDIN_ONLY
        d.evidence, d.confidence = f"linkedin:{linkedin}", 0.7
        return
    d.status, d.confidence, d.evidence = "custom_page", 0.6, "careers page, no known ATS"
    if not d.jsonld_jobposting and careers_tree is not None and d.fetches < MAX_FETCHES:
        base_host = _registrable(urlsplit(careers_page).hostname or "")
        for a in careers_tree.css("a[href]"):
            full = urljoin(careers_page, a.attributes.get("href") or "")
            sp = urlsplit(full)
            if (
                sp.scheme in ("http", "https")
                and _registrable(sp.hostname or "") == base_host
                and full.rstrip("/") not in seen
                and sp.path.rstrip("/") != urlsplit(careers_page).path.rstrip("/")
                and _JOBLINK_RX.search(sp.path)
            ):
                d.fetches += 1
                try:
                    r = await client.request("GET", full)
                    if has_jsonld_jobposting(HTMLParser(r.text)):
                        d.jsonld_jobposting = True
                except Exception:  # noqa: BLE001
                    pass
                break
    if d.jsonld_jobposting:
        d.evidence = "careers page with schema.org JobPosting JSON-LD"


async def detect_company(
    client: CrawlClient,
    domain: str,
    hints: dict[str, Any] | None = None,
    *,
    timeout: float = COMPANY_TIMEOUT,
) -> Detection:
    """Classify one company's careers system. Never raises."""
    d = Detection()
    try:
        await asyncio.wait_for(_detect(client, domain, hints or {}, d), timeout)
    except TimeoutError:
        d.notes = (d.notes + "; timeout").strip("; ")
        if d.status not in ("detected", "bot_wall"):
            d.status = "unreachable"
    except Exception as exc:  # noqa: BLE001
        d.notes = f"{type(exc).__name__}: {exc}"[:300]
        d.status = "unreachable"
    return d
