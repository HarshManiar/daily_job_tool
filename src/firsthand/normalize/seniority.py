"""Seniority (rule 3): first match wins over titles.yaml patterns, then level suffixes."""

import re

from firsthand.normalize.context import NormContext

SMALL_COMPANY = {"1-10", "11-50", "51-200"}
IC_FUNCTIONS = {"engineering", "data", "ml_ai", "design", "product", "research", "security", "it"}
_IC_NOUN = re.compile(
    r"\b(?:engineer|developer|programmer|scientist|analyst|designer|architect|researcher|"
    r"product manager|product owner|sdet|tester|devops|sre|member of technical staff|"
    r"administrator)\b"
)
_SUFFIX = re.compile(r"(?:^|\s)(?:level\s+)?([ivx]+|\d)\s*$")
_CLEAN_TRAIL = re.compile(r"[\s,;:/\-–—|]+$")


def infer_seniority(
    title_norm: str, function: str, size_bucket: str | None, ctx: NormContext
) -> str:
    s = title_norm
    is_ic = function in IC_FUNCTIONS and bool(_IC_NOUN.search(s))
    s = s.replace("member of technical staff", "member technical")
    s = re.sub(r"\b(?:svp|evp|avp)\b", "vp", s)
    s = s.replace("i/o", "io")
    for level, patterns in ctx.data.seniority:
        matched = [p for p in patterns if p.search(s)]
        if not matched:
            continue
        if level == "exec" and "head of" in s and not re.search(r"\bchief\b|\bc[a-z]o\b", s):
            return "director" if size_bucket in SMALL_COMPANY else "exec"
        return level
    if re.search(r"\bassociate\b", s) and is_ic:
        return "entry"
    m = _SUFFIX.search(_CLEAN_TRAIL.sub("", s))
    if m and m.group(1) in ctx.data.level_suffixes:
        return ctx.data.level_suffixes[m.group(1)]
    return "mid" if is_ic else "unknown"
