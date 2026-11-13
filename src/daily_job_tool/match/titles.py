"""Title tier classification on title_norm (token-subsequence matching)."""

import re
from dataclasses import dataclass
from functools import lru_cache

from daily_job_tool.match.profile import DEFAULT_BLOCKED_LEVELS, Profile, compile_rx

_STOP = {"and", "of", "the", "for", "in", "a"}
_MODIFIERS = {"senior", "junior", "i", "ii", "iii", "iv", "1", "2", "3", "4", "sr", "jr"}
_SOFT_EXTRA = _MODIFIERS | {"associate"}  # tolerated as extra tokens in a job title
_HEAD_NOUNS = {
    "analyst", "engineer", "consultant", "developer", "scientist", "owner", "manager",
    "specialist", "architect",
}  # fmt: skip
_ALIASES = {"engineering": "engineer", "rwe": "real world evidence"}
_MAX_GAP = 2
_BLOCK = frozenset(DEFAULT_BLOCKED_LEVELS)  # default; a profile's blocked_levels overrides


def _stem(t: str) -> str:
    t = _ALIASES.get(t, t)
    return t[:-1] if len(t) > 4 and t.endswith("s") else t


def _tokens(s: str) -> list[str]:
    s = s.lower().replace("health care", "healthcare").replace("&", " ")
    out: list[str] = []
    for t in re.findall(r"[a-z0-9]+", s):
        if t in _STOP:
            continue
        out.extend(_stem(x) for x in _ALIASES.get(t, t).split())
    return out


def expand_target(title: str) -> list[list[str]]:
    """Expand parentheticals (dropped) and slash alternations into token lists."""
    t = re.sub(r"\([^)]*\)", " ", title)
    shared = ""
    if "," in t:
        t, shared = t.split(",", 1)
    parts = [p.strip() for p in t.split("/") if p.strip()]
    variants: list[str]
    if len(parts) < 2:
        variants = parts or [t]
    else:
        a, b = parts[0], parts[1]
        at, bt = a.lower().split(), b.lower().split()
        b_core = [x for x in bt if x not in _MODIFIERS]
        if at[-1] in _HEAD_NOUNS:
            if len(b_core) == 1 and b_core[0] == at[-1]:
                variants = [a, " ".join(at[:-1] + bt)]
            else:
                variants = [a, b]
        else:
            variants = [" ".join(at + bt[len(at) :]), b]
        variants += parts[2:]
    out = [_tokens(f"{v} {shared}") for v in variants]
    if shared.strip():  # "X, Y" targets also match in "Y X" order
        out += [_tokens(f"{shared} {v}") for v in variants]
    return out


@lru_cache(maxsize=1024)
def _targets(titles: tuple[str, ...]) -> tuple[tuple[tuple[str, ...], ...], ...]:
    out = []
    for t in titles:
        for toks in expand_target(t):
            core = tuple(x for x in toks if x not in _MODIFIERS)
            if core:
                out.append(core)
    return tuple(out)


def _span_match(
    job: list[str], tgt: tuple[str, ...], block: frozenset[str] | set[str] = _BLOCK
) -> bool:
    def rec(ti: int, start: int, first: int | None) -> bool:
        if ti == len(tgt):
            last = start - 1
            outside = job[:first] + job[last + 1 :]
            return not any(x in block for x in outside)
        lo = start
        hi = len(job) if ti == 0 else min(len(job), start + _MAX_GAP + 1)
        for i in range(lo, hi):
            if job[i] != tgt[ti]:
                continue
            if ti > 0 and any(x in block for x in job[start:i]):
                continue
            if rec(ti + 1, i + 1, i if first is None else first):
                return True
        return False

    return rec(0, 0, None)


_SPLIT = re.compile(r"\s[-–—]\s|,")


def _segments(title_norm: str) -> list[str]:
    return [x.strip() for x in _SPLIT.split(title_norm) if x.strip()]


def _job_variants(title_norm: str) -> list[list[str]]:
    segs = _segments(title_norm)
    orders = [title_norm]
    if len(segs) > 1:
        orders.append(" ".join(reversed(segs)))
    vs: list[list[str]] = []
    for o in orders:
        toks = _tokens(o)
        vs.append(toks)
        if "associate" in toks:  # "Commercial Analytics Associate" ~ "... Analyst"
            vs.append(["analyst" if t == "associate" else t for t in toks])
    return vs


def _raw_variants(title_norm: str) -> list[str]:
    """Plain-text orderings (level words dropped) for the generic title regexes."""
    segs = _segments(title_norm)
    orders = [title_norm] + ([" ".join(reversed(segs))] if len(segs) > 1 else [])
    orders.append(re.sub(r"[()]", " ", title_norm))  # "research (heor) analyst" -> "heor analyst"
    drop = re.compile(r"\b(?:senior|sr\.?|junior|jr\.?|associate|i{1,3}|iv)\b", re.I)
    out = []
    for o in orders:
        out.append(o)
        out.append(re.sub(r"\s+", " ", drop.sub(" ", o)).strip())
    return out


@dataclass(frozen=True)
class TitleMatch:
    tier: int | None
    reason: str | None = None  # title:level_out_of_range | None (caller adds not_target)
    target: str | None = None


def _exclude_rx(profile: Profile) -> list[re.Pattern[str]]:
    out = []
    for e in profile.generic_data_roles.exclude_title_regex:
        out.append(compile_rx(rf"\b{e}(?:s|ship)?\b" if e.isalpha() else e))
    return out


def classify_title(title_norm: str, profile: Profile) -> TitleMatch:
    """Tier from target lists (best wins) or generic regexes. Tier 3 weak-fit is applied later."""
    jobs = _job_variants(title_norm)
    block = frozenset(profile.target_titles.blocked_levels)
    best: int | None = None
    for tier, titles in ((1, profile.target_titles.tier1), (2, profile.target_titles.tier2),
                         (3, profile.target_titles.tier3)):  # fmt: skip
        for tgt in _targets(tuple(titles)):
            if any(_span_match(j, tgt, block) for j in jobs):
                best = tier
                break
        if best:
            break
    if best in (1, 2):
        return TitleMatch(best)
    raws = _raw_variants(title_norm)
    generic = any(
        compile_rx(r).search(v) for r in profile.generic_data_roles.title_regex for v in raws
    )
    if generic:
        if any(rx.search(title_norm) for rx in _exclude_rx(profile)):
            return (
                TitleMatch(3, None) if best == 3 else TitleMatch(None, "title:level_out_of_range")
            )
        return TitleMatch(2)
    return TitleMatch(best)
