"""Remote type and remote regions (rule 6)."""

import re

from daily_job_tool.normalize.location import Frag

_REMOTE_TOKEN = re.compile(
    r"\bremote(?:ly)?\b(?!\s+(?:sensing|control|desktop|access|monitoring|sales rep))"
    r"|\bwork(?:ing)? from home\b|\bwfh\b|\bhome[ -]?based\b|\btelecommut\w*"
)
_HYBRID_TOKEN = re.compile(r"\bhybrid\b")
_ONSITE_TOKEN = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|\bin[- ]person\b|\boffice[- ]based\b")
_WORLD_LOC = re.compile(r"\b(?:anywhere|worldwide|global)\b")

_NEGATED = re.compile(
    r"\b(?:not|isn't|is not|no|non)[ -](?:a |an |fully |100% )?(?:remote|hybrid)\b"
    r"|\bnot (?:be )?(?:a )?(?:fully )?remote\b"
)
_DESC_HYBRID = re.compile(
    r"\bhybrid (?:role|position|work|working|model|schedule|environment|setup|arrangement|policy|"
    r"opportunity|office)\b|\bis a hybrid\b|\bhybrid\s*[:(-]"
    r"|\b(?:one|two|three|four|1|2|3|4)(?:\s*-\s*\d)?\s+days?\s+(?:a|per|each|every)\s+week\s+"
    r"(?:in|at|from)\s+(?:the|our)\s+office\b"
    r"|\bin (?:the|our) office\s+(?:one|two|three|four|1|2|3|4)\s+days?\b"
    r"|\b(?:one|two|three|four|1|2|3|4)(?:\s*-\s*\d)?\s+days?\s+(?:a|per)\s+week\s+on[- ]?site\b"
)
_DESC_REMOTE = re.compile(
    r"\b(?:fully|100%|entirely|completely|totally)[ -]remote\b"
    r"|\bthis (?:is )?(?:a |an )?(?:fully |100% )?remote (?:role|position|job|opportunity)\b"
    r"|\b(?:role|position|job|opportunity) is (?:fully |100% )?remote\b"
    r"|\bremote[- ]first\b|\bwork from anywhere\b|\bwork (?:fully )?remotely\b"
    r"|\b(?:can|may) be (?:done|performed|worked) remotely\b|\bremote[- ]only\b"
)
_DESC_ONSITE = re.compile(
    r"\bon[- ]?site (?:role|position|job)\b|\bthis is an? (?:on[- ]?site|in[- ]office)\b"
    r"|\b(?:five|5) days (?:a|per) week (?:in|at) the office\b"
    r"|\bmust (?:be|work) (?:on[- ]?site|in the office)\b|\bin[- ]office (?:role|position)\b"
)
_RESTRICT = re.compile(
    r"\bmust (?:be )?(?:located|based|reside|living|resident)\b"
    r"|\b(?:open|available) (?:only )?to (?:candidates|applicants|residents)\b"
    r"|\b(?:authorized|eligible|legally entitled) to work in\b"
    r"|\b(?:us|u\.s\.|uk|eu|canada|german|european)[- ](?:based|residents|only|citizens)\b"
    r"|\bwithin (?:the )?(?:us|u\.s\.|united states|uk|eu|europe|canada)\b"
    r"|\b(?:time ?zones?|timezones?)\b.*\b(?:est|pst|cet|gmt|utc|et|pt)\b"
    r"|\bremote (?:within|in) (?:the )?[a-z]+\b"
)

_STRUCTURED = [
    ("hybrid", re.compile(r"hybrid")),
    ("remote", re.compile(r"remote|wfh|work from home|telecommut")),
    ("onsite", re.compile(r"on[- ]?site|in[- ]office|office|in[- ]person|on premise")),
]


def _structured(workplace_raw: str | None) -> str | None:
    if not workplace_raw:
        return None
    s = workplace_raw.lower().replace("_", " ")
    for value, pat in _STRUCTURED:
        if pat.search(s):
            return value
    return None


_TITLE_HYBRID = re.compile(r"[(\-\u2013|,/]\s*hybrid\b|\bhybrid\s*[)]")


def _tokens(text: str, hybrid: re.Pattern[str] = _HYBRID_TOKEN) -> set[str]:
    found = set()
    if hybrid.search(text):
        found.add("hybrid")
    if _REMOTE_TOKEN.search(text):
        found.add("remote")
    if _ONSITE_TOKEN.search(text):
        found.add("onsite")
    return found


def infer_remote_type(
    workplace_raw: str | None,
    title: str,
    locations_raw: list[str],
    description_text: str | None,
    frags: list[Frag],
) -> str:
    s = _structured(workplace_raw)
    if s:
        return s
    found = _tokens(title.lower(), _TITLE_HYBRID) | _tokens(" | ".join(locations_raw).lower())
    if any(f.res and f.res.kind == "world" for f in frags):
        found.add("remote")
    if "hybrid" in found:
        return "hybrid"
    if "remote" in found:
        return "remote"
    if "onsite" in found:
        return "onsite"
    if description_text:
        head = _NEGATED.sub(" ", description_text[:2048].lower())
        if _DESC_HYBRID.search(head):
            return "hybrid"
        if _DESC_REMOTE.search(head):
            return "remote"
        if _DESC_ONSITE.search(head):
            return "onsite"
    return "unknown"


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def derive_remote_regions(
    remote_type: str, frags: list[Frag], description_text: str | None
) -> list[str]:
    if remote_type != "remote":
        return []
    explicit: list[str] = []
    for f in frags:
        r = f.res
        if not (f.remote and r):
            continue
        if r.kind == "world":
            explicit.append("WORLD")
        elif r.kind == "region" and r.label:
            explicit.append(r.label)
        elif r.kind == "place" and r.country:
            explicit.append(r.country)
    if explicit:
        return _dedupe(explicit)
    for f in frags:
        r = f.res
        if r and r.kind == "world":
            return ["WORLD"]
    others = [f.res.country for f in frags if f.res and f.res.kind == "place" and f.res.country]
    if others:
        return _dedupe([c for c in others if c])
    others = [f.res.label for f in frags if f.res and f.res.kind == "region" and f.res.label]
    if others:
        return _dedupe([c for c in others if c])
    if description_text and _RESTRICT.search(description_text[:2048].lower()):
        return []
    return ["WORLD"]
