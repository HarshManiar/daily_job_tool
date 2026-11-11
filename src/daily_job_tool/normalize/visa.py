"""Visa sponsorship classification (pure).

`classify_visa(text, phrases)` returns a `VisaResult`, a str subclass equal to one of
'offered' | 'not_available' | 'not_stated' that also carries `.evidence` (the matched sentence).
Profile phrases (hard_filters.visa) are applied together with a built-in supplement for common
wordings the profile does not spell out. Near-misses (EEO boilerplate, hedged or negated
sentences) never exclude; a wrongly excluded job is the costly failure.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

_SP = r"sponsor(?!ed\b)"
_OBJ = r"(?:provide\s+|offer\s+|support\s+)?(?:[\w-]+\s+(?:or|and)\s+)?(?:(?!require\b)\w+\s+)?"
_EXTRA_EXCLUDE = [
    rf"(?:unable|not able|cannot|can\s?not|can['’]t|won['’]t|isn['’]t able|is unable)\s+to\s+{_OBJ}{_SP}",
    rf"(?:do|does|did)(?:\s+not|n['’]t)\s+(?:currently\s+)?{_OBJ}{_SP}",
    rf"(?:will|would|can|could)(?:\s+not|n['’]t)\s+(?:currently\s+)?{_OBJ}{_SP}",
    r"(?:are|is|am)(?:\s+not|n['’]t)\s+(?:currently\s+)?(?:sponsoring|offering|providing|supporting)\b",
    r"will\s+not\s+be\s+(?:sponsoring|offering|providing)\b",
    rf"(?:not|n['’]t)\s+(?:eligible|available)\s+for\s+(?:\w+\s+)?{_SP}",
    rf"\bno\s+(?:h-?1b|work\s+visa|immigration|employment)\s+{_SP}",
    rf"{_SP}\w*\s+(?:will\s+|is\s+|can\s+)?not\s+(?:be\s+)?(?:available|offered|provided)",
    r"(?:must|should|will)\s+not\s+(?:[^.]{0,50}?\s)?require\s+(?:\w+\s+){0,2}sponsorship",
    rf"not\s+(?:able\s+to\s+)?(?:consider|hire|accept)\w*[^.]{{0,60}}{_SP}",
    r"without\s+(?:(?!regard\b)[\w-]+\s+){0,8}sponsorship",
    r"requir\w*\s+(?:\w+\s+){0,3}sponsorship[^.]{0,40}(?:not|won['’]t)\s+be\s+considered",
    r"not\s+open\s+to\s+(?:[\w-]+\s+){0,5}sponsorship",
    r"(?:citizens?|citizenship)\s+(?:is\s+)?required(?!\s*[:?]?\s*[-–]?\s*no\b)",
    r"must\s+be\s+(?:a\s+)?(?:u\.?s\.?\s+)?citizen",
    r"only\s+(?:u\.?s\.?\s+)?citizens",
]
_EXTRA_FLAG = [
    r"sponsorship\s+(?:may|might|can|could)\s+be\s+(?:available|offered|provided)",
    r"\b(?:can|may|able to|willing to)\s+sponsor(?!ed)",
    r"\b(?:offer|provide)s?\s+(?:[\w-]+\s+){0,2}sponsorship",
    r"\bwe\s+(?:do\s+|can\s+|will\s+)?sponsor(?!ed)\w*\b",
    r"\b(?:is|are)\s+(?:eligible|open)\s+(?:for|to)\s+(?:[\w-]+\s+)?sponsor",
    r"sponsorship\s*:\s*(?:yes|available|offered)",
    r"welcome\s+(?:applicants|candidates)\s+who\s+(?:will\s+)?need\s+(?:\w+\s+)?sponsorship",
]
# a positive H-1B transfer mention overrides any refusal elsewhere in the document
_TRANSFER_POS = [
    re.compile(p, re.I)
    for p in (
        r"h-?1b\s*(?:visa\s+)?transfers?(?:\s+(?:candidates?|applicants?|cases?))?\b[^.]{0,40}"
        r"\b(?:welcome\w*|accept\w*|consider\w*|support\w*|ok|okay|eligible|encouraged)\b",
        r"\b(?:can|will|able to|happy to|glad to|open to|do|does)\s+(?:also\s+)?"
        r"(?:support|accept|consider|welcome|sponsor)\w*[^.]{0,50}\btransfers?\b",
        r"\bsponsor\w*\s+h-?1b\s+transfers?\b",
        r"\bh-?1b\s+transfers?\s*:\s*(?:yes|ok|okay|accepted|welcome)",
        r"\bexcept\w*\s+(?:for\s+)?h-?1b\s+(?:visa\s+)?transfers?\b",
    )
]
_NEG_WORD = re.compile(r"\b(?:not|no|never|cannot)\b|n['’]t", re.I)
_HEDGE = re.compile(
    r"\bexception(?:s|al)?\b|\bexcept\b|\bunless\b|case[- ]by[- ]case|in some cases|right candidate",
    re.I,
)
_BOILER = re.compile(r"discriminat\w*|regardless of|without regard to|irrespective of", re.I)
_NEG_BEFORE = re.compile(
    r"\b(?:not|no|never|nor|neither|without|don['’]t|doesn['’]t)\b\s+(?:\w+\s+){0,2}$", re.I
)
_HAS_NEG = re.compile(r"\bno\b|\bnot\b|n['’]t|without|unable|cannot|never", re.I)
# sentences whose sponsorship is not about work authorization
_NONVISA = re.compile(
    r"\b(?:tuition|events?|conferences?|content|budgets?|scholarships?|hackathons?|webinars?|"
    r"meetups?|sponsored)\b",
    re.I,
)
_VISA_CTX = re.compile(
    r"visa|h-?1b|immigration|work\s+(?:permit|authori)|employment[- ]based|green\s*card", re.I
)
_GENERIC_VISA = re.compile(
    r"visa|h-?1b\s+(?!petition|transfer)|immigration|work\s+(?:permit|authori)|employment[- ]based|green\s*card",
    re.I,
)
# refusal whose only object is a student status / a brand-new petition: not an exclusion
_OPT_ONLY = re.compile(
    r"\b(?:cpt|opt|stem[- ]opt|f-?1|j-?1|tn|students?|new\s+h-?1b\s+petitions?|new\s+petitions?|"
    r"cap[- ]subject)\b",
    re.I,
)
_EXPORT = re.compile(
    r"\bITAR\b|u\.?s\.?\s+persons?\b|export[- ]control\w*|"
    r"(?:secret|top\s+secret|ts/sci|public\s+trust|security)\s+clearance|"
    r"clearance\s+(?:is\s+)?(?:required|needed)|obtain\s+(?:a\s+)?(?:security\s+)?clearance",
    re.I,
)
_SENT = re.compile(
    r"\n+|\s*[•·▪●]\s*|(?<=[!?])\s+(?!(?i:no|yes)\b)|(?<=\.)(?<!\b[A-Za-z]\.)\s+(?=[A-Z0-9(\"“])"
)


@dataclass(frozen=True)
class VisaPhrases:
    exclude: tuple[str, ...] = ()
    flag: tuple[str, ...] = ()


class VisaResult(str):
    evidence: str | None
    flags: tuple[str, ...]

    def __new__(cls, status: str, evidence: str | None = None, flags: tuple[str, ...] = ()):
        o = super().__new__(cls, status)
        o.evidence = evidence
        o.flags = flags
        return o


@lru_cache(maxsize=64)
def _compile(exclude: tuple[str, ...], flag: tuple[str, ...]):
    c = lambda ps: [re.compile(p, re.I) for p in ps]  # noqa: E731
    return c(exclude + tuple(_EXTRA_EXCLUDE)), c(flag + tuple(_EXTRA_FLAG))


def _phrases(p) -> VisaPhrases:
    if isinstance(p, VisaPhrases):
        return p
    if isinstance(p, dict):
        return VisaPhrases(
            tuple(p.get("exclude_if_phrases", ())), tuple(p.get("flag_if_phrases", ()))
        )
    return VisaPhrases(
        tuple(getattr(p, "exclude_if_phrases", ()) or ()),
        tuple(getattr(p, "flag_if_phrases", ()) or ()),
    )


def _transfer_evidence(text: str) -> str | None:
    """A positive H-1B transfer mention anywhere in the document (negation-aware)."""
    for rx in _TRANSFER_POS:
        for m in rx.finditer(text):
            if _NEG_WORD.search(m.group(0)):
                continue
            if _NEG_BEFORE.search(text[max(0, m.start() - 30) : m.start()]):
                continue
            a = max(text.rfind(c, 0, m.start()) for c in ".!?\n;") + 1
            b = [x for x in (text.find(c, m.end()) for c in ".!?\n") if x != -1]
            return text[a : min(b) + 1 if b else len(text)].strip()[:300]
    return None


def classify_visa(text: str | None, profile_phrases=None) -> VisaResult:
    if not text:
        return VisaResult("not_stated")
    ph = _phrases(profile_phrases or VisaPhrases())
    exc, flg = _compile(ph.exclude, ph.flag)
    flags: list[str] = []
    exp = _EXPORT.search(text)
    if exp and not _NEG_BEFORE.search(text[max(0, exp.start() - 30) : exp.start()]):
        flags.append("visa:export_control")
    tr = _transfer_evidence(text)
    if tr:
        return VisaResult("offered", tr, tuple(flags))
    offered: str | None = None
    for sent in (s.strip() for s in _SENT.split(text)):
        if not sent:
            continue
        hedged = bool(_HEDGE.search(sent))
        refused = False
        nonvisa = bool(_NONVISA.search(sent)) and not _VISA_CTX.search(sent)
        if not nonvisa:
            for rx in exc:
                for m in rx.finditer(sent):
                    if _BOILER.search(sent[: m.start()]) or hedged:
                        continue
                    if re.search(r"h-?1b\s+(?:visa\s+)?transfer", sent, re.I):
                        continue  # transfer context: never exclude
                    if sent[m.end() : m.end() + 2].lower() == "ed":
                        continue  # "not sponsored content"
                    if not _HAS_NEG.search(m.group(0)) and _NEG_BEFORE.search(
                        sent[max(0, m.start() - 30) : m.start()]
                    ):
                        continue
                    if _OPT_ONLY.search(sent) and not _GENERIC_VISA.search(sent):
                        if "visa:opt_only" not in flags:
                            flags.append("visa:opt_only")
                        refused = True
                        continue
                    return VisaResult("not_available", sent[:300])
        if offered is None and not nonvisa and not refused:
            for rx in flg:
                for m in rx.finditer(sent):
                    if _NEG_BEFORE.search(sent[max(0, m.start() - 25) : m.start()]):
                        continue
                    offered = sent[:300]
                    break
                if offered:
                    break
    evid = offered or (exp.group(0) if exp and "visa:export_control" in flags else None)
    return VisaResult("offered" if offered else "not_stated", evid, tuple(flags))
