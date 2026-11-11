"""Employment type (rule 8)."""

import re

_TITLE_TOKENS: list[tuple[str, re.Pattern[str]]] = [
    ("internship", re.compile(r"\b(?:intern|internship|co-?op|working student|werkstudent)\b")),
    ("apprenticeship", re.compile(r"\bapprentice(?:ship)?\b")),
    ("part_time", re.compile(r"\bpart[- ]?time\b")),
    (
        "temporary",
        re.compile(r"\b(?:temporary|temp|fixed[- ]term|seasonal)\b(?!\s+(?:agency|staffing))"),
    ),
    (
        "contract",
        re.compile(
            r"\b(?:contractor|freelance|contract(?:-to-hire)?)\b"
            r"(?!\s+(?:manager|specialist|administrator|attorney|counsel|analyst|lawyer|"
            r"management|negotiator|coordinator|compliance|designer for))"
        ),
    ),
]


def _from_ats(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^a-z]", "", raw.lower())
    if not s:
        return None
    if "intern" in s or "coop" in s:
        return "internship"
    if "apprentice" in s:
        return "apprenticeship"
    if "parttime" in s:
        return "part_time"
    if "fulltime" in s or s in {"permanent", "regular", "employee", "regularfulltime"}:
        return "full_time"
    if "fixedterm" in s or "temp" in s or "seasonal" in s:
        return "temporary"
    if "contract" in s or "freelance" in s or "consult" in s:
        return "contract"
    return None


def infer_employment_type(employment_type_raw: str | None, title: str) -> str:
    ats = _from_ats(employment_type_raw)
    if ats not in (None, "full_time"):
        return ats or "unknown"
    t = title.lower()
    for value, pat in _TITLE_TOKENS:
        if pat.search(t):
            return value
    return ats or "unknown"
