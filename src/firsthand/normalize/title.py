"""Title to title_norm (rule 2)."""

import re
from collections.abc import Iterable

from firsthand.normalize.context import NormContext
from firsthand.normalize.text import clean_title, fold

# Code-level additions to titles.yaml strip_patterns (gender markers without parens, etc.).
_EXTRA_STRIP = [
    re.compile(r"\((?:all|any)\s+genders?\)"),
    re.compile(r"\(\s*(?:gn|d/f/m|w/m/d|m/f/d|m/w/d|f/m/d|m/f/x)\s*\)"),
    re.compile(r"(?<![\w/])[mfwdx]/[mfwdx]/[mfwdx](?![\w/])"),
    re.compile(r"\s*,\s*remote\b(?:\s*[-,(].*)?$"),
    re.compile(r"\(\s*\)"),
]
_ABBR_CACHE: dict[int, re.Pattern[str]] = {}
_SEP = re.compile(r"\s+[-–—|]\s+|\s*,\s*|\s*\(")
_TRIM = re.compile(r"^[\s\-–—|,/:;]+|[\s\-–—|,/:;]+$")


def _abbr_pattern(ctx: NormContext) -> re.Pattern[str]:
    key = id(ctx.data)
    pat = _ABBR_CACHE.get(key)
    if pat is None:
        keys = sorted(ctx.data.abbreviations, key=len, reverse=True)
        pat = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, keys)) + r")(?![\w-])\.?")
        _ABBR_CACHE[key] = pat
    return pat


def _is_place(s: str, loc_keys: set[str], ctx: NormContext) -> bool:
    f = fold(s.strip().rstrip(")"))
    if not f or len(f) < 2:
        return False
    d = ctx.data
    if f in loc_keys:
        return True
    return f in d.city_idx or f in d.country_idx or f in d.region_idx


def _strip_location_suffix(t: str, locations_raw: Iterable[str], ctx: NormContext) -> str:
    loc_keys: set[str] = set()
    for raw in locations_raw:
        for part in re.split(r"[|;/,•()]", raw):
            f = fold(part)
            if f and f not in {"remote", "hybrid", "onsite", "on site"}:
                loc_keys.add(f)
    for _ in range(3):
        seps = list(_SEP.finditer(t))
        if not seps:
            break
        m = seps[-1]
        prefix, suffix = t[: m.start()], t[m.end() :]
        if not prefix.strip() or not suffix.strip():
            break
        if _is_place(suffix, loc_keys, ctx):
            t = prefix
        else:
            break
    return t


def normalize_title(title: str, ctx: NormContext, locations_raw: Iterable[str] = ()) -> str:
    t = clean_title(title).lower()
    for pat in ctx.data.strip_patterns:
        t = pat.sub(" ", t)
    for pat in _EXTRA_STRIP:
        t = pat.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = _TRIM.sub("", t)
    t = _strip_location_suffix(t, list(locations_raw), ctx)
    t = _abbr_pattern(ctx).sub(lambda m: ctx.data.abbreviations[m.group(1)], t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _TRIM.sub("", t)
