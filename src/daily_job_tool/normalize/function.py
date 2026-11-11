"""Function classification (rule 4): title keywords first, then ATS department, then 'other'."""

import re
from functools import cache

from daily_job_tool.normalize.context import NormContext


@cache
def _kw(kw: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")


# Phrases missing from functions.yaml that a human maps unambiguously. Appended to the keyword
# list of the named function, so the YAML order still decides ties.
EXTRA_TITLE_KEYWORDS: dict[str, list[str]] = {
    "product": [
        "director of product",
        "vp of product",
        "vp product",
        "vp, product",
        "vice president of product",
        "vice president, product",
        "svp product",
        "chief product officer",
        "product leader",
    ],
    "data": ["head of data", "director of data", "vp of data", "vp data", "chief data officer"],
    "marketing": ["lead generation"],
}


def _hit(text: str, keywords: list[str]) -> bool:
    spaced = text.replace("-", " ")
    return any(_kw(k).search(text) or _kw(k).search(spaced) for k in keywords)


def classify_function(title_norm: str, department_raw: str | None, ctx: NormContext) -> str:
    if "member of technical staff" in title_norm:
        return "engineering"
    for entry in ctx.data.functions:
        kws = entry["title_keywords"] + EXTRA_TITLE_KEYWORDS.get(entry["function"], [])
        if kws and _hit(title_norm, kws):
            return entry["function"]
    if department_raw:
        dept = department_raw.lower()
        for entry in ctx.data.functions:
            if entry["department_keywords"] and _hit(dept, entry["department_keywords"]):
                return entry["function"]
    return "other"
