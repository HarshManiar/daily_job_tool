"""URL / host / text signatures that identify an applicant tracking system (ATS).

`match_url` is a pure function: URL in, (ats, token_or_tenant, config) out. `match_text`
looks for inline keyword signatures when no URL is available.
"""

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

Match = tuple[str, str | None, dict[str, Any]]

LINKEDIN_ONLY = "linkedin-only"
UNKNOWN = "unknown"

_LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")
_NOT_TENANT = {"www", "app", "api", "apply", "static", "cdn", "assets", "docs", "help", "support"}


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _first(parts: list[str]) -> str | None:
    return parts[0] if parts else None


def _sub(host: str, suffix_re: str) -> str | None:
    m = re.fullmatch(rf"([a-z0-9][a-z0-9_-]*)\.{suffix_re}", host)
    if m and m.group(1) not in _NOT_TENANT:
        return m.group(1)
    return None


def _greenhouse(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io"):
        if parts[:1] == ["embed"]:
            tok = (q.get("for") or [None])[0]
            return ("greenhouse", tok, {}) if tok else ("greenhouse", None, {})
        tok = _first(parts)
        return ("greenhouse", tok, {}) if tok else None
    if host == "boards-api.greenhouse.io":
        if parts[:2] == ["v1", "boards"] and len(parts) >= 3:
            return "greenhouse", parts[2], {}
        return None
    if host in ("grnh.se", "app.greenhouse.io"):
        return "greenhouse", None, {}
    return None


def _lever(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        tok = _first(parts)
        cfg = {"region": "eu"} if ".eu." in host else {}
        return ("lever", tok, cfg) if tok else None
    if host in ("api.lever.co", "api.eu.lever.co") and parts[:2] == ["v0", "postings"]:
        return ("lever", parts[2], {}) if len(parts) >= 3 else None
    return None


def _ashby(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host == "jobs.ashbyhq.com":
        tok = _first(parts)
        return ("ashby", tok, {}) if tok else None
    if host == "api.ashbyhq.com" and parts[:2] == ["posting-api", "job-board"] and len(parts) >= 3:
        return "ashby", parts[2], {}
    return None


def _workable(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host == "apply.workable.com":
        tok = _first(parts)
        if tok == "api" and len(parts) >= 4:
            tok = parts[3]
        return ("workable", tok, {}) if tok else None
    tok = _sub(host, r"workable\.com")
    return ("workable", tok, {}) if tok else None


def _smartrecruiters(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"):
        tok = _first(parts)
        if tok == "oneclick-ui" and len(parts) >= 3:
            tok = parts[2]
        return ("smartrecruiters", tok, {}) if tok else None
    if host == "api.smartrecruiters.com" and parts[:2] == ["v1", "companies"] and len(parts) >= 3:
        return "smartrecruiters", parts[2], {}
    return None


def _recruitee(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"recruitee\.com")
    return ("recruitee", tok, {}) if tok else None


def _teamtailor(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"teamtailor\.com")
    return ("teamtailor", tok, {}) if tok else None


def _personio(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"jobs\.personio\.(?:de|com)")
    return ("personio", tok, {}) if tok else None


def _bamboohr(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"bamboohr\.com")
    return ("bamboohr", tok, {}) if tok else None


def _workday(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    m = re.fullmatch(r"([a-z0-9_-]+)\.(wd\d{1,2})\.myworkdayjobs\.com", host)
    if m:
        segs = [s for s in parts if not _LOCALE.match(s)]
        cfg: dict[str, Any] = {"wd": m.group(2)}
        if segs:
            cfg["site"] = segs[0]
        return "workday", m.group(1), cfg
    m = re.fullmatch(r"(?:[a-z0-9_-]+\.)?(wd\d{1,2})\.myworkdaysite\.com", host)
    if m:
        # /recruiting/{tenant}/{site}
        if len(parts) >= 3 and parts[0] == "recruiting":
            return "workday", parts[1], {"wd": m.group(1), "site": parts[2]}
        return "workday", None, {"wd": m.group(1)}
    if host.endswith(".myworkdayjobs.com"):
        return "workday", host.split(".")[0], {}
    return None


def _icims(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    m = re.fullmatch(r"([a-z0-9_-]+)\.icims\.com", host)
    if not m or m.group(1) in _NOT_TENANT:
        return None
    tok = m.group(1)
    for pre in ("careers-", "jobs-", "career-"):
        if tok.startswith(pre):
            tok = tok[len(pre) :]
            break
    return "icims", tok, {"host": host}


def _successfactors(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host.startswith("rmkcdn."):  # SF career-site template CDN: SF, tenant unknown
        return "successfactors", None, {"host": host}
    if re.fullmatch(r"[a-z0-9_-]+\.successfactors\.(?:com|eu)", host) and host.split(".")[
        0
    ] not in (
        "www",
        "help",
        "community",
    ):
        tok = (q.get("company") or [None])[0]
        return "successfactors", tok or host.split(".")[0], {"host": host}
    if host == "jobs.sap.com":
        return "successfactors", "sap", {"host": host}
    return None


def _oracle(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    m = re.fullmatch(r"([a-z0-9_-]+)\.fa\.([a-z0-9_-]+)\.oraclecloud\.com", host)
    if m:
        cfg: dict[str, Any] = {"host": host}
        if "sites" in parts:
            i = parts.index("sites")
            if i + 1 < len(parts):
                cfg["site"] = parts[i + 1]
        return "oracle", m.group(1), cfg
    if host.endswith(".oraclecloud.com") and "hcmUI" in parts and "CandidateExperience" in parts:
        return "oracle", host.split(".")[0], {"host": host}
    return None


def _taleo(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"taleo\.net")
    return ("taleo", tok, {"host": host}) if tok else None


def _jobvite(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host in ("jobs.jobvite.com", "careers.jobvite.com", "hire.jobvite.com"):
        tok = _first(parts)
        return ("jobvite", tok, {}) if tok else None
    return None


def _adp(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host in ("workforcenow.adp.com", "recruiting.adp.com", "myjobs.adp.com"):
        tok = (q.get("cid") or q.get("ccId") or q.get("c") or [None])[0]
        return "adp", tok, {"host": host}
    return None


def _ultipro(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if re.fullmatch(r"recruiting\d*\.ultipro\.(?:com|ca)", host) or host.endswith(
        (".ultipro.com",)
    ):
        tok = _first(parts)
        return ("ultipro", tok, {"host": host}) if tok else ("ultipro", None, {"host": host})
    if host.endswith(".ukg.com") and host not in ("www.ukg.com", "ukg.com"):
        if host.startswith("recruiting") or "JobBoard" in parts:
            return "ultipro", _first(parts), {"host": host}
    return None


def _paylocity(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host in ("recruiting.paylocity.com", "access.paylocity.com"):
        guid = next((p for p in parts if re.fullmatch(r"[0-9a-fA-F-]{32,36}", p)), None)
        return "paylocity", guid, {}
    return None


def _paycom(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host.endswith("paycomonline.net") and "ats" in parts:
        tok = (q.get("clientkey") or [None])[0]
        if not tok:
            tok = next((p for p in parts if re.fullmatch(r"[0-9A-Fa-f]{32}", p)), None)
        return "paycom", tok, {}
    return None


def _phenom(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host.endswith(("phenompeople.com", "phenompro.com", "phenom.com")) and host not in (
        "www.phenom.com",
        "phenom.com",
    ):
        return "phenom", None, {"host": host}
    return None


def _avature(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"avature\.net")
    return ("avature", tok, {"host": host}) if tok else None


def _eightfold(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"eightfold\.ai")
    return ("eightfold", tok, {}) if tok else None


def _brassring(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host.endswith(("brassring.com", "kenexa.com")) and host not in ("www.brassring.com",):
        tok = (q.get("partnerid") or q.get("partnerId") or [None])[0]
        return "brassring", tok, {"host": host}
    return None


def _cornerstone(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"csod\.com")
    return ("cornerstone", tok, {}) if tok else None


def _dayforce(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host == "jobs.dayforcehcm.com":
        segs = [p for p in parts if not _LOCALE.match(p)]
        return "dayforce", _first(segs), {}
    if host.endswith(".dayforcehcm.com") or host.endswith(".ceridian.com"):
        if host.split(".")[0] in ("www", "dayforce"):
            return None
        return "dayforce", host.split(".")[0], {}
    return None


def _breezy(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"breezy\.hr")
    return ("breezy", tok, {}) if tok else None


def _jazzhr(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"applytojob\.com")
    if tok:
        return "applytojob", tok, {}
    if host in ("app.jazz.co", "jazz.co") or host.endswith(".theresumator.com"):
        return "jazzhr", _first(parts) if host.endswith("jazz.co") else host.split(".")[0], {}
    return None


def _rippling(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host == "ats.rippling.com":
        tok = _first(parts)
        return ("rippling-ats", tok, {}) if tok else None
    return None


def _gem(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host == "jobs.gem.com":
        tok = _first(parts)
        return ("gem", tok, {}) if tok else None
    return None


def _comeet(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host in ("www.comeet.com", "comeet.com", "www.comeet.co", "comeet.co") and parts[:1] == [
        "jobs"
    ]:
        return "comeet", (parts[1] if len(parts) > 1 else None), {}
    return None


def _pinpoint(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    tok = _sub(host, r"pinpointhq\.com")
    return ("pinpoint", tok, {}) if tok else None


def _hirebridge(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host == "hirebridge.com" or host.endswith(".hirebridge.com"):
        return "hirebridge", (q.get("cid") or [None])[0], {}
    return None


def _linkedin(host: str, parts: list[str], q: dict[str, list[str]]) -> Match | None:
    if host in ("linkedin.com", "www.linkedin.com") and len(parts) >= 2 and parts[0] == "company":
        if len(parts) >= 3 and parts[2] == "jobs":
            return LINKEDIN_ONLY, parts[1], {}
    return None


_RULES: tuple[Callable[[str, list[str], dict[str, list[str]]], Match | None], ...] = (
    _greenhouse,
    _lever,
    _ashby,
    _workable,
    _smartrecruiters,
    _recruitee,
    _teamtailor,
    _personio,
    _bamboohr,
    _workday,
    _icims,
    _successfactors,
    _oracle,
    _taleo,
    _jobvite,
    _adp,
    _ultipro,
    _paylocity,
    _paycom,
    _phenom,
    _avature,
    _eightfold,
    _brassring,
    _cornerstone,
    _dayforce,
    _breezy,
    _jazzhr,
    _rippling,
    _gem,
    _comeet,
    _pinpoint,
    _hirebridge,
    _linkedin,
)


def match_url(url: str) -> Match | None:
    """Return (ats, token_or_tenant, config) if `url` points at a known ATS, else None."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https", ""):
        return None
    host = (p.hostname or "").lower()
    if not host or p.username or "$" in url or "{" in url:
        return None  # userinfo (e.g. Sentry DSNs) and template placeholders are not boards
    parts = _segments(p.path)
    q = parse_qs(p.query)
    for rule in _RULES:
        hit = rule(host, parts, q)
        if hit:
            return hit
    return None


# Inline keyword signatures (page text / script bodies) when no URL is extractable.
TEXT_SIGNATURES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rx, re.I), ats)
    for rx, ats in (
        (r"Greenhouse\.jobBoard|grnhse_app|grnhse_iframe", "greenhouse"),
        (r"ashby_embed|ashby_jid", "ashby"),
        (r"lever-jobs-container", "lever"),
        (r"teamtailor", "teamtailor"),
        (r"myworkdayjobs\.com|myworkdaysite\.com", "workday"),
        (r"\bicims\.com", "icims"),
        (r"successfactors\.(?:com|eu)", "successfactors"),
        (r"oraclecloud\.com/hcmUI/CandidateExperience", "oracle"),
        (r"\btaleo\.net", "taleo"),
        (r"jobs\.jobvite\.com|jobvite\.com/", "jobvite"),
        (r"phenompeople|phenompro", "phenom"),
        (r"smartrecruiters\.com", "smartrecruiters"),
        (r"personio\.(?:de|com)", "personio"),
        (r"avature\.net", "avature"),
        (r"eightfold\.ai", "eightfold"),
        (r"csod\.com", "cornerstone"),
        (r"dayforcehcm\.com", "dayforce"),
    )
)


def match_text(text: str) -> tuple[str, str] | None:
    """First inline keyword signature found in `text`: (ats, matched snippet)."""
    for rx, ats in TEXT_SIGNATURES:
        m = rx.search(text)
        if m:
            return ats, m.group(0)
    return None
