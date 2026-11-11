"""Years-of-experience requirement extraction (pure).

`extract_years(text) -> (min, max)`; (None, None) when no experience requirement is found.
Rules: strictest REQUIRED statement wins (max of the minimums); preferred / nice-to-have text
is used only when nothing else is found; non-experience durations are ignored.
"""

import re
from dataclasses import dataclass

from daily_job_tool.normalize.sections import IGNORE, REQUIRED, segments

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}  # fmt: skip
_NUM = r"\d{1,2}|" + "|".join(_WORDS)
_YEARS = re.compile(
    rf"(?<![\w$.,/])(?P<a>{_NUM})(?:\s*\(\d{{1,2}}\))?"
    rf"(?:(?P<plus>\s*\+|\s*plus\b|\s+or\s+more\b|\s+or\s+greater\b)"
    rf"|\s*(?:-|–|—|to)\s*(?P<b>{_NUM})(?P<bplus>\s*\+)?)?"
    r"\s*[-–]?\s*(?:years?|yrs?\.?)(?![a-z])",
    re.I,
)
_STRONG_PREFIX = re.compile(
    r"(?:minimum(?: of)?|min\.?|at least|no less than|a minimum of|requires?|requiring)\s*(?:of\s+)?$",
    re.I,
)
_HARD_CUE = re.compile(r"\b(?:minimum|at least|must|required|requires|requirement)\b", re.I)
_SKIP_PREFIX = re.compile(
    r"(?:up to|less than|fewer than|under|no more than|within|than)\s*(?:the\s+)?$", re.I
)
_LOOSE_PREFIX = re.compile(r"(?:over|more than|nearly|almost|about|around)\s*$", re.I)
_EXP_CUE = re.compile(
    r"experience|expertise|exp\b|background|hands[- ]on|proven|track record|proficien|"
    r"professional|industry|relevant|related|working|work\b|using|in (?:a|an|the)\b|"
    r"of (?:work|analytics|data|sql|python)|\bin\b|\bwith\b",
    re.I,
)
_AFTER_NEG = re.compile(
    r"^[\s-]*(?:old\b|(?:track record|history|legacy|heritage)\b(?!\s+of\s+(?:success\w*\s+)?(?:in|with)\b)|"
    r"of (?:(?:longitudinal|historical|patient[- ]level|patient|claims|real[- ]world|proprietary|rich|deep)\s+){1,3}"
    r"(?:data|history|records)\b|ago\b|later\b|of (?:history|age|service|operation|innovation|excellence|"
    r"growth|success|combined|collective|serving|leadership|helping|empower)|in business|"
    r"in (?:the )?(?:market|industry)\b(?! experience)|and counting|of experience (?:helping|"
    r"serving|delivering|building|supporting) (?:clients|customers|organizations|companies))",
    re.I,
)
_BEFORE_NEG = re.compile(
    r"(?:average(?: of)?|avg\.?|brings?|boasts?|bringing|backed by|combined|"
    r"(?:leader|founder|ceo|executive|director|vp|manager|supervisor|mentor|colleague|peer|teammate|"
    r"company|firm|organization|business|agency|group|startup)s?\s+with|founded|established|since|after|every|each|vest\w*|tenure of|for the (?:past|last)|"
    r"in the (?:past|last|next)|over the (?:past|last|next)|past|last|next|first|"
    r"we(?:['’]ve| have| are)|our (?:team|company|firm)|i(?:['’]ve| have)|"
    r"401\(?k\)?[^.]{0,30}|every)\s*(?:\w+\s+)?$",
    re.I,
)
_COMPANY_HAS = re.compile(
    r"^(?!(?i:(?:the\s+|a\s+)?(?:ideal\s+|successful\s+|right\s+)?(?:candidates?|you|applicants?|person|"
    r"individual|hire|incumbent|they|he|she|must|should|will|we|i|someone|anyone|who)\b))[A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,2}"
    r"\s+(?:has|have|brings|boasts)\s+(?:over\s+|more than\s+)?$"
)
_DEGREE = re.compile(
    r"bachelor|master|ph\.?d|\bms\b|\bbs\b|\bba\b|\bmba\b|m\.s\.|b\.s\.|degree|doctorate|diploma|msc",
    re.I,
)
_ALT_BARE = re.compile(
    r"(?<![\w$.,/])(\d{1,2})\s*\+?\s*(?:years?\s+)?(?:with|for|if|and)\s+(?:a\s+|an\s+)?"
    r"(?:bs|ba|b\.s\.|ms|m\.s\.|msc|mba|ph\.?d\.?|master\S*|bachelor\S*|doctorate|advanced\s+degree)\b",
    re.I,
)
_OR_DEGREE_NUM = re.compile(
    r"\bor\s+(?:a\s+|an\s+)?(?:ms|m\.s\.|msc|mba|ph\.?d\.?|master\S*|advanced\s+degree)\s*\+\s*(\d{1,2})\b",
    re.I,
)
_TIER_BEFORE = re.compile(
    r"(?:senior|sr\.?|junior|jr\.?|mid|entry|lead|principal|staff|associate|manager|director|analyst)"
    r"(?:[- ]level)?\s*[:(\-–]\s*$",
    re.I,
)
_TIER_AFTER = re.compile(
    r"^\s*\(\s*(?:senior|sr\.?|junior|jr\.?|mid|entry|lead|principal|staff|associate|manager|"
    r"director|analyst)(?:[- ]level)?\s*\)",
    re.I,
)
_HEDGE_CUE = re.compile(
    r"\bmay\s+(?:be\s+)?(?:considered|qualify)|\bconsidered\s+for\b|\bcould\s+be\s+considered", re.I
)
_MAX_YEARS = 30
MAYBE_FLAG = "years:maybe_7+"  # non-required pool value >= 7: never excluded, only flagged


def _num(tok: str) -> int:
    t = tok.lower()
    return _WORDS[t] if t in _WORDS else int(t)


def _candidates(seg_text: str):
    for m in _YEARS.finditer(seg_text):
        a = _num(m.group("a"))
        b = _num(m.group("b")) if m.group("b") else None
        if a > _MAX_YEARS or (b is not None and (b < a or b > _MAX_YEARS)):
            continue
        before = seg_text[max(0, m.start() - 40) : m.start()]
        after = seg_text[m.end() : m.end() + 60]
        if _SKIP_PREFIX.search(before) or _AFTER_NEG.match(after) or _BEFORE_NEG.search(before):
            continue
        if _COMPANY_HAS.match(seg_text[: m.start()]):
            continue
        strong = bool(m.group("plus") or m.group("b") or _STRONG_PREFIX.search(before) or a == 0)
        window = seg_text[max(0, m.start() - 30) : m.end() + 70]
        has_exp = bool(_EXP_CUE.search(after[:70]) or re.search(r"experience", before, re.I))
        if _LOOSE_PREFIX.search(before) and not re.search(r"experience", after[:50], re.I):
            continue  # "over 20 years we have grown" vs "over 5 years of experience"
        if not strong and not has_exp:
            continue
        if not has_exp and not _STRONG_PREFIX.search(before) and not m.group("b"):
            # bare "5+ years" with no experience-like context anywhere in the clause
            if not (
                re.search(
                    r"experience|exp\b|background|\bsql|python|analy|data|engineer", window, re.I
                )
                or _DEGREE.search(window)
            ):
                continue
        yield a, b, m


@dataclass(frozen=True)
class YearsResult:
    min: int | None
    max: int | None
    pool: str | None  # 'R' (required) | 'N' (neutral) | 'P' (preferred / hedged) | None
    flags: tuple[str, ...] = ()

    @property
    def values(self) -> tuple[int | None, int | None]:
        return self.min, self.max


def _groups(seg_text: str) -> list[tuple[int, int | None, bool]]:
    """Candidates of one segment as (a, b, is_tiered); degree alternatives joined by 'or' collapse
    to their minimum (a candidate may satisfy the role with the lowest alternative)."""
    cs = [(a, b, m) for a, b, m in _candidates(seg_text)]
    if not cs:
        return []
    extra: list[tuple[int, int | None]] = []
    if _DEGREE.search(seg_text):
        extra += [
            (int(m.group(1)), None)
            for m in _ALT_BARE.finditer(seg_text)
            if int(m.group(1)) <= _MAX_YEARS
        ]
    extra += [(int(m.group(1)), None) for m in _OR_DEGREE_NUM.finditer(seg_text)]
    groups: list[list[tuple[int, int | None]]] = []
    prev_end = None
    for a, b, m in cs:
        between = seg_text[prev_end : m.start()] if prev_end is not None else ""
        alt = (
            prev_end is not None
            and re.search(r"\bor\b", between, re.I)
            and not (re.search(r"\band\b", between, re.I) and not _DEGREE.search(seg_text))
        )
        if alt and groups:
            groups[-1].append((a, b))
        else:
            groups.append([(a, b)])
        prev_end = m.end()
    if extra and groups:
        groups[-1].extend(extra)
    out = []
    for g, (_, _, m0) in zip(groups, cs, strict=False):
        lo = min(g, key=lambda t: t[0])
        before = seg_text[max(0, m0.start() - 40) : m0.start()]
        tiered = bool(_TIER_BEFORE.search(before) or _TIER_AFTER.match(seg_text[m0.end() :]))
        out.append((lo[0], lo[1], tiered))
    return out


def extract_years_detail(text: str | None) -> YearsResult:
    if not text:
        return YearsResult(None, None, None)
    pools: dict[str, list[tuple[int, int | None]]] = {"R": [], "N": [], "P": []}
    tiers: list[tuple[str, int, int | None]] = []
    prev_group: tuple[str, list[tuple[int, int | None, bool]]] | None = None
    for seg in segments(text):
        if seg.kind == IGNORE:
            continue
        if seg.preferred or _HEDGE_CUE.search(seg.text):
            pool = "P"
        elif seg.kind == REQUIRED or _HARD_CUE.search(seg.text):
            pool = "R"
        else:
            pool = "N"
        gs = _groups(seg.text)
        if re.match(r"or\b|alternatively\b", seg.text, re.I) and prev_group and gs:
            # "...; or Master's and 5 years": an alternative of the previous segment
            pk, pv = prev_group
            lo = min(gs, key=lambda t: t[0])
            for lst in (pools[pk],):
                if pv and (pv[-1][0], pv[-1][1]) in lst:
                    lst.remove((pv[-1][0], pv[-1][1]))
                    lst.append(min([(pv[-1][0], pv[-1][1]), (lo[0], lo[1])], key=lambda t: t[0]))
                    break
            continue
        for a, b, tiered in gs:
            if tiered:
                tiers.append((pool, a, b))
            else:
                pools[pool].append((a, b))
        prev_group = (pool, [g for g in gs if not g[2]]) if gs else prev_group
    if len(tiers) >= 2:  # "Senior: 7-10; mid: 3-5" -> the lowest tier
        k, a, b = min(tiers, key=lambda t: t[1])
        pools[k].append((a, b))
    else:
        for k, a, b in tiers:
            pools[k].append((a, b))
    for key in ("R", "N", "P"):
        if pools[key]:
            lo = max(a for a, _ in pools[key])
            his = [b for a, b in pools[key] if a == lo and b is not None]
            flags = (MAYBE_FLAG,) if key != "R" and lo >= 7 else ()
            return YearsResult(lo, (max(his) if his else None), key, flags)
    return YearsResult(None, None, None)


def extract_years(text: str | None) -> tuple[int | None, int | None]:
    return extract_years_detail(text).values
