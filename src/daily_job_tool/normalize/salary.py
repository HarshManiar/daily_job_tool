"""Salary (rule 7): structured first, then description regex; annualize, convert, sanity-check."""

import re
from dataclasses import dataclass

from daily_job_tool.adapters.base import SalaryRaw
from daily_job_tool.normalize.context import NormContext

ANNUAL_FACTOR = {"hour": 2080, "day": 260, "week": 52, "month": 12, "year": 1}
MIN_USD, MAX_USD, MAX_RATIO = 10_000, 2_000_000, 4

_SYMBOLS = {
    "US$": "USD",
    "CA$": "CAD",
    "C$": "CAD",
    "AU$": "AUD",
    "A$": "AUD",
    "NZ$": "NZD",
    "SG$": "SGD",
    "S$": "SGD",
    "HK$": "HKD",
    "R$": "BRL",
    "MX$": "MXN",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "Rs.": "INR",
    "Rs": "INR",
    "¥": "JPY",
    "￥": "JPY",
    "₩": "KRW",
    "₪": "ILS",
    "₽": "RUB",
    "฿": "THB",
    "₱": "PHP",
    "₺": "TRY",
    "zł": "PLN",
    "$": "USD",
}
_CODES = (
    "USD EUR GBP CAD AUD NZD CHF SEK NOK DKK PLN CZK HUF RON BGN ISK TRY JPY CNY RMB INR SGD "
    "HKD KRW THB MYR IDR PHP BRL MXN ZAR ILS AED ARS COP CLP RUB"
).split()
_SYM_ALT = "|".join(re.escape(s) for s in sorted(_SYMBOLS, key=len, reverse=True))
_CODE_ALT = "|".join(_CODES)
_NUM = (
    r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d{1,3}(?:\.\d{3})+(?:,\d+)?|"
    r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![\d])"
)
_SUF = r"(?:[kK](?![A-Za-z])|LPA|lpa|[lL]akhs?|[lL]acs?|L(?![A-Za-z])|[cC]rores?)"


def _price(i: int) -> str:
    return (
        rf"(?:(?P<pc{i}>{_CODE_ALT})\s?)?(?:(?P<ps{i}>(?<![A-Za-z]){_SYM_ALT})\s?)?"
        rf"(?P<n{i}>{_NUM})\s?(?P<s{i}>{_SUF})?(?:\s?(?P<po{i}>{_CODE_ALT})\b)?"
    )


_UNIT_MID = r"(?:\s?(?:/|per\s+)\s?(?:yr|year|hr|hour|mo|month|wk|week|annum)\b\.?)?"
_RANGE_RE = re.compile(
    _price(1) + r"(?:" + _UNIT_MID + r"\s*(?:-|–|—|~|\bto\b|\band\b)\s*" + _price(2) + r")?"
)
_AFTER_PERIOD = re.compile(
    r"^\s*(?:(?:gross|net|brutto|base|before tax|pre-tax)\s+)*"
    r"(?:(?:/|per\s+|a\s+|an\s+|each\s+|every\s+)(?:the\s+)?"
    r"(?P<u>hour|hr|h|day|week|wk|month|mo|year|yr|annum)\b"
    r"|(?P<adj>hourly|daily|weekly|monthly|yearly|annually|annual|p\.a\.?|pa)\b)",
    re.I,
)
_BEFORE_PERIOD = re.compile(
    r"\b(hourly|daily|weekly|monthly|yearly|annual(?:ized|ised)?|per hour|per day|per week|"
    r"per month|per year|per annum)\b",
    re.I,
)
_PERIOD_WORDS = {
    "hour": "hour",
    "hr": "hour",
    "h": "hour",
    "hourly": "hour",
    "day": "day",
    "daily": "day",
    "week": "week",
    "wk": "week",
    "weekly": "week",
    "month": "month",
    "mo": "month",
    "monthly": "month",
    "year": "year",
    "yr": "year",
    "yearly": "year",
    "annum": "year",
    "annual": "year",
    "annualized": "year",
    "annualised": "year",
    "annually": "year",
    "pa": "year",
    "p.a": "year",
    "p.a.": "year",
}
_EXCL_WORDS = re.compile(
    r"\b(?:bonus|equity|stock|options?|rsus?|commissions?|signing|sign-on|relocation|stipend|"
    r"allowance|budget|funding|raised|revenue|valuation|grant|reimbursement|401\s?\(?k\)?|"
    r"match|referral|profit|stipends?)\b",
    re.I,
)
_PAY_WORDS = re.compile(r"\b(?:salary|base|pay|compensation|range|ote|wage|remuneration)\b", re.I)
_AFTER_EXCL = re.compile(
    r"^\s*(?:in |of )?(?:annual |signing |sign-on |performance |target |relocation |"
    r"stock |equity |cash |lunch |meal |wellness |learning |home office |phone |internet |gym )*"
    r"(?:bonus|equity|stock|rsus?|options|relocation|stipend|"
    r"in funding|in revenue|funding|raised|commission|allowance|match)",
    re.I,
)
# money words that make an open-ended "up to $N" a benefit/budget, not pay
_BENEFIT_CTX = re.compile(
    r"\b(?:match|matching|401|contribut\w*|reimburs\w*|budget|tuition|fund(?:ing|s)?|hsa|fsa|"
    r"perks?|benefits?|per employee|allowance|credit)\b",
    re.I,
)
# "(this does not include bonus, equity and benefits): $76,000" -> the excluded items are not
# what the number is
_NEGATED_EXCL = re.compile(
    r"\([^)]*\)\s*:?|(?:does\s+not|doesn['’]t|do\s+not|not)\s+(?:include|including)[^$€£]*|"
    r"(?:excluding|excludes?|exclusive\s+of|before|without)\s[^$€£]*",
    re.I,
)
_AFTER_BIG = re.compile(r"^\s?(?:m\b|mm\b|bn?\b|million|billion|%)", re.I)
_PAY_BLOCK = re.compile(
    r"salary|base pay|pay range|compensation|\bote\b|base salary|pay transparency|remuneration",
    re.I,
)


@dataclass
class Parsed:
    min: float | None
    max: float | None
    currency: str | None
    period: str | None


@dataclass
class SalaryResult:
    min: float | None
    max: float | None
    currency: str | None
    period: str | None
    min_usd: int | None
    max_usd: int | None
    source: str


def to_float(s: str) -> float:
    """Parse '120,000', '60.000', '1.234,56', '1,234.56', '12,00,000', '120 000', '3.5', '1,5'."""
    s = re.sub(r"[   ]", "", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            return float(s.replace(".", "").replace(",", "."))
        return float(s.replace(",", ""))
    if "," in s:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}", s):
            return float(s.replace(",", ""))
        return float(s.replace(",", "."))
    if "." in s:
        if s.count(".") > 1 or (re.fullmatch(r"[1-9]\d{0,2}\.\d{3}", s)):
            return float(s.replace(".", ""))
    return float(s)


def _suffix_mult(suf: str | None) -> float:
    if not suf:
        return 1.0
    s = suf.lower()
    if s == "k":
        return 1e3
    if s.startswith("cr"):
        return 1e7
    return 1e5  # lpa, lakh, lac, l


def norm_period(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^a-z.]", " ", raw.lower()).strip()
    for word in s.split():
        if word in _PERIOD_WORDS:
            return _PERIOD_WORDS[word]
    return None


def _currency_from(*groups: tuple[str | None, str | None, str | None]) -> str | None:
    codes = [g[0] for g in groups if g[0]] + [g[2] for g in groups if g[2]]
    if codes:
        code = codes[-1] if len(codes) > 1 and codes[0] == codes[-1] else codes[0]
        return "CNY" if code == "RMB" else code
    for g in groups:
        if g[1]:
            return _SYMBOLS.get(g[1])
    return None


def annualize(value: float, period: str) -> float:
    return value * ANNUAL_FACTOR[period]


def finalize(
    mn: float | None, mx: float | None, currency: str | None, period: str | None, ctx: NormContext
) -> tuple[int | None, int | None] | None:
    """Annualize and convert to USD; None if the sanity window fails. Currency None: no USD."""
    if period is None or (mn is None and mx is None):
        return None
    rate = ctx.fx_rates.get(currency) if currency else None
    if rate is None:
        # no USD figure, but a yearly amount under 1,000 in any currency is not a salary
        if any(annualize(v, period) < 1000 for v in (mn, mx) if v is not None):
            return None
        return (None, None)
    lo = annualize(mn, period) * rate if mn is not None else None
    hi = annualize(mx, period) * rate if mx is not None else None
    vals = [v for v in (lo, hi) if v is not None]
    if any(v < MIN_USD or v > MAX_USD for v in vals):
        return None
    if lo is not None and hi is not None:
        if lo > hi or (lo > 0 and hi / lo > MAX_RATIO):
            return None
    return (round(lo) if lo is not None else None, round(hi) if hi is not None else None)


def _candidates(text: str) -> list[tuple[Parsed, int, bool]]:
    out: list[tuple[Parsed, int, bool]] = []
    for m in _RANGE_RE.finditer(text):
        start, end = m.start(), m.end()
        s1, s2 = m.group("s1"), m.group("s2")
        has2 = m.group("n2") is not None
        cur = _currency_from(
            (m.group("pc1"), m.group("ps1"), m.group("po1")),
            (m.group("pc2"), m.group("ps2"), m.group("po2")),
        )
        lakh = any(
            x and x.lower() in {"lpa"} or (x and x.lower().startswith(("lakh", "lac")))
            for x in (s1, s2)
        )
        if cur is None and lakh:
            cur = "INR"
        if cur is None:
            continue
        # bare symbol-less "number" ranges are rejected above; also skip percents and M/B amounts
        tail = text[end : end + 12]
        if _AFTER_BIG.match(tail):
            continue
        before_seg = re.split(r"[.;\n]", text[max(0, start - 160) : start])[-1]
        before_seg = _NEGATED_EXCL.sub(" ", before_seg)[-40:]
        excl = list(_EXCL_WORDS.finditer(before_seg))
        if excl:
            pay = list(_PAY_WORDS.finditer(before_seg))
            if not pay or pay[-1].start() < excl[-1].start():
                continue
        if _AFTER_EXCL.match(text[end : end + 40]):
            continue
        v1 = to_float(m.group("n1"))
        mult1 = _suffix_mult(s1)
        if has2:
            v2 = to_float(m.group("n2"))
            mult2 = _suffix_mult(s2)
            if not s1 and s2 and v1 < 1000:
                mult1 = mult2
            lo, hi = v1 * mult1, v2 * mult2
            if lo > hi:
                continue
        else:
            lo = hi = v1 * mult1
            pre = before_seg.lower().rstrip()
            if re.search(r"(?:up to|upto|maximum(?: of)?|max\.?|not exceeding|capped at)$", pre):
                lo = None
            elif re.search(r"(?:from|starting at|starts at|minimum(?: of)?|min\.?|at least)$", pre):
                hi = None
            elif text[end : end + 1] == "+":
                hi = None
        if lo is None or hi is None:
            ctx150 = text[max(0, start - 150) : start]
            if _BENEFIT_CTX.search(ctx150) and not _PAY_WORDS.search(ctx150):
                continue  # open-ended "up to $15,000": a 401k match / budget, not pay
        pm = _AFTER_PERIOD.match(text[end : end + 60])
        period = None
        if pm:
            period = _PERIOD_WORDS.get((pm.group("u") or pm.group("adj")).lower().rstrip("."))
        if period is None:
            if lakh:
                period = "year"
            else:
                bm = list(_BEFORE_PERIOD.finditer(text[max(0, start - 45) : start]))
                if bm:
                    period = norm_period(bm[-1].group(1))
        near = bool(_PAY_BLOCK.search(text[max(0, start - 150) : start]))
        out.append((Parsed(lo, hi, cur, period or "year"), start, near))
    return out


def parse_salary_text(text: str, ctx: NormContext) -> SalaryResult | None:
    """Parse the first sane salary in free text (pay-transparency blocks win over position)."""
    if not text:
        return None
    cands = _candidates(text)
    cands.sort(key=lambda c: (not c[2], c[1]))
    for p, _, _ in cands:
        fin = finalize(p.min, p.max, p.currency, p.period, ctx)
        if fin is None:
            continue
        return SalaryResult(p.min, p.max, p.currency, p.period, fin[0], fin[1], "parsed")
    return None


def structured_salary(sal: SalaryRaw, ctx: NormContext) -> SalaryResult | None:
    if sal.min is None and sal.max is None:
        return None
    cur = sal.currency.upper() if sal.currency and len(sal.currency) == 3 else None
    period = norm_period(sal.period)
    options = [period] if period else ["year", "hour", "month"]
    for p in options:
        fin = finalize(sal.min, sal.max, cur, p, ctx)
        if fin is not None:
            return SalaryResult(sal.min, sal.max, cur, p, fin[0], fin[1], "structured")
    return None


def extract_salary(
    sal: SalaryRaw | None, description_text: str | None, ctx: NormContext
) -> SalaryResult | None:
    if sal is not None:
        r = structured_salary(sal, ctx)
        if r:
            return r
        if sal.text:
            r = parse_salary_text(sal.text, ctx)
            if r:
                return r
    if description_text:
        return parse_salary_text(description_text, ctx)
    return None
