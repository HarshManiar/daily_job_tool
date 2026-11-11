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
    ("contract", re.compile(r"\bc2c\b|corp[- ]to[- ]corp|\bw-?2\s+(?:contract|only)\b")),
    (
        "contract",
        re.compile(
            r"\b(?:contractor|freelance|contract(?:-to-(?:hire|perm\w*))?)\b"
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


_DESC_CONTRACT = re.compile(
    r"\bc2c\b|corp[- ]to[- ]corp|\bw-?2\s+contract|\b1099\b|contract[- ]to[- ](?:hire|perm)|"
    r"(?:this|the)\s+(?:is\s+a\s+|role\s+is\s+a\s+|position\s+is\s+a\s+)?(?:[\w-]+\s+)?"
    r"contract\s+(?:role|position|opportunity|assignment|basis|engagement)\b|"
    r"\b(?:\d+|three|six|nine|twelve)[- ]months?\s+contract|"
    r"(?:employment|job)\s+type\s*:\s*contract|\bcontractor\s+(?:role|position)\b"
)
_DESC_TEMP = re.compile(r"temp[- ]to[- ](?:hire|perm)|(?:employment|job)\s+type\s*:\s*temp")
_DESC_FT = re.compile(r"\bfull[- ]time\b|\bpermanent\b", re.I)
_DESC_PT = re.compile(r"\bpart[- ]time\b", re.I)


def infer_employment_type(
    employment_type_raw: str | None, title: str, description: str | None = None
) -> str:
    ats = _from_ats(employment_type_raw)
    if ats not in (None, "full_time"):
        return ats or "unknown"
    t = title.lower()
    for value, pat in _TITLE_TOKENS:
        if pat.search(t):
            return value
    head = (description or "")[:3072]
    hl = head.lower()
    if _DESC_CONTRACT.search(hl):
        return "contract"
    if _DESC_TEMP.search(hl):
        return "temporary"
    if ats:
        return ats
    if _DESC_FT.search(head) and not _DESC_PT.search(head):
        return "full_time"
    return "unknown"
