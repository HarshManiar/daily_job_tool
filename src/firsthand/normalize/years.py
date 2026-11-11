"""Years-of-experience requirement extraction (pure).

`extract_years(text) -> (min, max)`; (None, None) when no experience requirement is found.
Rules: strictest REQUIRED statement wins (max of the minimums); preferred / nice-to-have text
is used only when nothing else is found; non-experience durations are ignored.
"""

import re

from firsthand.normalize.sections import IGNORE, REQUIRED, segments

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
    r"^\s*(?:old\b|ago\b|later\b|of (?:history|age|service|operation|innovation|excellence|"
    r"growth|success|combined|collective|serving|leadership|helping|empower)|in business|"
    r"in (?:the )?(?:market|industry)\b(?! experience)|and counting|of experience (?:helping|"
    r"serving|delivering|building|supporting) (?:clients|customers|organizations|companies))",
    re.I,
)
_BEFORE_NEG = re.compile(
    r"(?:founded|established|since|after|every|each|vest\w*|tenure of|for the (?:past|last)|"
    r"in the (?:past|last|next)|over the (?:past|last|next)|past|last|next|first|"
    r"we(?:['’]ve| have| are)|our (?:team|company|firm)|i(?:['’]ve| have)|"
    r"401\(?k\)?[^.]{0,30}|every)\s*(?:\w+\s+)?$",
    re.I,
)
_MAX_YEARS = 30


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
        strong = bool(m.group("plus") or m.group("b") or _STRONG_PREFIX.search(before) or a == 0)
        window = seg_text[max(0, m.start() - 30) : m.end() + 70]
        has_exp = bool(_EXP_CUE.search(after[:70]) or re.search(r"experience", before, re.I))
        if _LOOSE_PREFIX.search(before) and not re.search(r"experience", after[:50], re.I):
            continue  # "over 20 years we have grown" vs "over 5 years of experience"
        if not strong and not has_exp:
            continue
        if not has_exp and not _STRONG_PREFIX.search(before) and not m.group("b"):
            # bare "5+ years" with no experience-like context anywhere in the clause
            if not re.search(
                r"experience|exp\b|background|\bsql|python|analy|data|engineer", window, re.I
            ):
                continue
        yield a, b, m


def extract_years(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    pools: dict[str, list[tuple[int, int | None]]] = {"R": [], "N": [], "P": []}
    for seg in segments(text):
        if seg.kind == IGNORE:
            continue
        for a, b, _m in _candidates(seg.text):
            if seg.preferred:
                pools["P"].append((a, b))
            elif seg.kind == REQUIRED or _HARD_CUE.search(seg.text):
                pools["R"].append((a, b))
            else:
                pools["N"].append((a, b))
    for key in ("R", "N", "P"):
        if pools[key]:
            lo = max(a for a, _ in pools[key])
            his = [b for a, b in pools[key] if a == lo and b is not None]
            return lo, (max(his) if his else None)
    return None, None
