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
    ("contract", re.compile(r"\bc2c\b|corp[- ]to[- ]corp|\bw-?2\s+(?:contract|only)\b|\(w-?2\)")),
    (
        "contract",
        re.compile(
            # 'contract' only as a qualifier of the position, never as part of the job's subject
            r"\b(?:contractor|freelance)\b|"
            r"[(\[]\s*contract\s*[)\]]|"
            r"(?:\s[-–—|]\s|,\s*)contract(?:\s+(?:role|position|only|basis))?\s*$|"
            r"\bcontract\s+(?:role|position|only|basis)\b|"
            r"\bcontract[- ]to[- ](?:hire|perm\w*)\b|"
            r"\b(?:analyst|engineer|developer|scientist|consultant)\s+contract\s*$|"
            r"^(?:(?:senior|sr\.?|jr\.?|junior|lead)\s+)?contract\s+"
            r"(?:data|software|bi|etl|python|sql|java|cloud|devops|ml|machine|full[- ]stack)\b"
            r"(?!\s+(?:operations|management|strategy|analytics|pricing))"
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


_NOT_QUAL = (
    r"(?!\s+(?:pricing|strategy|analytics|operations|management|administration|"
    r"negotiation|manager|analyst))"
)
_DESC_CONTRACT = re.compile(
    r"\bc2c\b|corp[- ]to[- ]corp|\bw-?2\s+contract|contract[- ]to[- ](?:hire|perm)|"
    r"\bthis\s+(?:is\s+a|role\s+is\s+a|position\s+is\s+a|job\s+is\s+a|opportunity\s+is\s+a)"
    r"\s+(?:[\w-]+\s+){0,2}(?:contract|1099)\b" + _NOT_QUAL + r"|"
    r"\bon\s+a\s+(?:[\w-]+\s+)?contract\s+basis\b|"
    r"\b(?:\d+|three|six|nine|twelve)[- ]months?\s+contract\b" + _NOT_QUAL + r"|"
    r"(?:employment|job|position)\s+type\s*:\s*contract\b|\bcontractor\s+(?:role|position)\b|"
    r"\b1099\s+(?:independent\s+)?(?:contractor|contract|basis|position|employee|only)\b|"
    r"\bthis\s+(?:role|position)\s+is\s+(?:a\s+)?(?:[\w-]+\s+){0,2}1099\b"
)
_DESC_TEMP = re.compile(r"temp[- ]to[- ](?:hire|perm)|(?:employment|job)\s+type\s*:\s*temp")
_DESC_FT = re.compile(r"\bfull[- ]time\b|\bpermanent\b", re.I)
_DESC_PT = re.compile(r"\bpart[- ]time\b", re.I)


def infer_employment_detail(
    employment_type_raw: str | None, title: str, description: str | None = None
) -> tuple[str, tuple[str, ...]]:
    """(employment_type, flags). An ATS 'full time' is never downgraded by title/description
    scans; a contradicting contract/temp signal there yields the flag 'employment:conflict'."""
    ats = _from_ats(employment_type_raw)
    if ats not in (None, "full_time"):
        return ats or "unknown", ()
    t = title.lower()
    hit: str | None = None
    for value, pat in _TITLE_TOKENS:
        if pat.search(t):
            hit = value
            break
    if hit is None:
        hl = (description or "")[:3072].lower()
        if _DESC_CONTRACT.search(hl):
            hit = "contract"
        elif _DESC_TEMP.search(hl):
            hit = "temporary"
    if ats == "full_time":
        if hit in ("internship", "apprenticeship", "part_time"):
            return hit, ()
        return "full_time", (("employment:conflict",) if hit else ())
    if hit:
        return hit, ()
    head = (description or "")[:3072]
    if _DESC_FT.search(head) and not _DESC_PT.search(head):
        return "full_time", ()
    return "unknown", ()


def infer_employment_type(
    employment_type_raw: str | None, title: str, description: str | None = None
) -> str:
    return infer_employment_detail(employment_type_raw, title, description)[0]
