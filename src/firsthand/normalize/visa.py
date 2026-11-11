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

from firsthand.normalize.sections import split_sentences

_EXTRA_EXCLUDE = [
    r"(?:unable|not able|cannot|can\s?not|can['’]t|won['’]t|isn['’]t able|is unable)\s+to\s+(?:provide\s+|offer\s+)?(?:\w+\s+)?sponsor",
    r"(?:do|does|did)(?:\s+not|n['’]t)\s+(?:currently\s+)?(?:provide\s+|offer\s+)?(?:\w+\s+)?sponsor",
    r"(?:will|would|can|could)(?:\s+not|n['’]t)\s+(?:provide\s+|offer\s+)?(?:\w+\s+)?sponsor",
    r"(?:not|n['’]t)\s+(?:eligible|available)\s+for\s+(?:\w+\s+)?sponsorship",
    r"\bno\s+(?:h-?1b|work\s+visa|immigration|employment)\s+sponsorship",
    r"sponsorship\s+(?:will\s+|is\s+|can\s+)?not\s+(?:be\s+)?(?:available|offered|provided)",
    r"(?:must|should|will)\s+not\s+(?:now\s+or\s+in\s+the\s+future\s+)?require\s+(?:\w+\s+)?sponsorship",
    r"not\s+(?:able\s+to\s+)?(?:consider|hire|accept)\w*[^.]{0,60}sponsor",
    r"(?:citizens?|citizenship)\s+(?:is\s+)?required",
    r"must\s+be\s+(?:a\s+)?(?:u\.?s\.?\s+)?citizen",
    r"only\s+(?:u\.?s\.?\s+)?citizens",
]
_EXTRA_FLAG = [
    r"sponsorship\s+(?:may|might|can|could)\s+be\s+(?:available|offered|provided)",
    r"(?:can|may|able to|willing to)\s+sponsor",
]
_HEDGE = re.compile(
    r"\bexception(?:s|al)?\b|\bexcept\b|\bunless\b|case[- ]by[- ]case|in some cases|right candidate",
    re.I,
)
_BOILER = re.compile(r"discriminat\w*|regardless of|without regard to|irrespective of", re.I)
_NEG_BEFORE = re.compile(
    r"\b(?:not|no|never|nor|neither|without|don['’]t|doesn['’]t)\b\s+(?:\w+\s+){0,2}$", re.I
)
_HAS_NEG = re.compile(r"\bno\b|\bnot\b|n['’]t|without|unable|cannot|never", re.I)


@dataclass(frozen=True)
class VisaPhrases:
    exclude: tuple[str, ...] = ()
    flag: tuple[str, ...] = ()


class VisaResult(str):
    evidence: str | None

    def __new__(cls, status: str, evidence: str | None = None):
        o = super().__new__(cls, status)
        o.evidence = evidence
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


def classify_visa(text: str | None, profile_phrases=None) -> VisaResult:
    if not text:
        return VisaResult("not_stated")
    ph = _phrases(profile_phrases or VisaPhrases())
    exc, flg = _compile(ph.exclude, ph.flag)
    offered: str | None = None
    for sent in split_sentences(text):
        hedged = bool(_HEDGE.search(sent))
        for rx in exc:
            for m in rx.finditer(sent):
                if _BOILER.search(sent[: m.start()]) or hedged:
                    continue
                if not _HAS_NEG.search(m.group(0)) and _NEG_BEFORE.search(
                    sent[max(0, m.start() - 30) : m.start()]
                ):
                    continue
                return VisaResult("not_available", sent[:300])
        if offered is None:
            for rx in flg:
                for m in rx.finditer(sent):
                    if _NEG_BEFORE.search(sent[max(0, m.start() - 25) : m.start()]):
                        continue
                    offered = sent[:300]
                    break
                if offered:
                    break
    return VisaResult("offered", offered) if offered else VisaResult("not_stated")
