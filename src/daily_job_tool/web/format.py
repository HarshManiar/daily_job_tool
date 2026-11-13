"""Pure presentation helpers (no I/O): money, dates, reasons, locations, chips."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

EXCLUSION_TEXT = {
    "visa:not_available": "No visa sponsorship",
    "employment:contract": "Contract role",
    "company:excluded": "Excluded employer",
    "company:staffing": "Staffing agency",
}

# Attention flags shown as warn chips in the feed.
WARN_TEXT = {
    "pay:below_min": "pay < $120K",
    "date:undated": "undated",
    "years:maybe_7+": "maybe 7+ yrs",
    "visa:export_control": "export control",
    "visa:opt_only": "OPT only",
    "employment:conflict": "type conflict",
    "years:below_range": "below your yrs range",
}

# Informational reasons shown as a visually quiet chip (never an attention flag).
QUIET_TEXT = {"employment:unknown": "type not stated"}

INFO_TEXT = {
    "visa:offered": "Employer offers visa sponsorship",
    "title:not_target": "Title is not one of your targets",
    "title:tier3_weak_fit": "Tier 3 title with weak fit",
    "stale": "Stale posting",
    "date:first_crawl": "Date is first-seen (board newly added)",
    "location_unknown": "Location could not be parsed",
}

LOCATION_RANK_TEXT = {0: "New Jersey", 1: "NYC area", 2: "US remote", 3: "Other US"}
INDUSTRY_RANK_TEXT = {0: "Priority industry", 1: "Other industry"}
TIER_TEXT = {1: "Tier 1 target title", 2: "Tier 2 target title", 3: "Tier 3 target title"}


def money_compact(n: int | float | None) -> str:
    """120000 -> '$120K', 1250000 -> '$1.25M', 95500 -> '$95.5K'."""
    if n is None:
        return ""
    n = float(n)
    if n >= 1_000_000:
        return "$" + f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return "$" + f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "K"
    return f"${n:.0f}"


def pay_range(lo: int | float | None, hi: int | float | None) -> str:
    """Annual USD range: '$120–150K', '$95K', '$95K+', '' when unknown."""
    if lo is None and hi is None:
        return ""
    if lo is None or hi is None or lo == hi:
        v = hi if lo is None else lo
        return money_compact(v) + ("+" if lo is not None and hi is None else "")
    a, b = money_compact(lo), money_compact(hi)
    if a.endswith("K") and b.endswith("K"):
        return f"{a[:-1]}–{b[1:]}"  # "$120–150K"
    return f"{a}–{b}"


def relative_date(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return ""
    days = (now - dt).total_seconds() / 86400
    if days < 1:
        return "today"
    d = int(days)
    if d == 1:
        return "yesterday"
    if d < 14:
        return f"{d}d ago"
    if d < 60:
        return f"{d // 7}w ago"
    if d < 730:
        return f"{d // 30}mo ago"
    return f"{d // 365}y ago"


def posted_text(posted_at: datetime | None, freshness_at: datetime | None, now: datetime) -> str:
    if posted_at is not None:
        return relative_date(posted_at, now)
    if freshness_at is not None:
        return f"first seen {relative_date(freshness_at, now)}"
    return ""


def years_chip(lo: int | None, hi: int | None) -> str:
    if lo is None and hi is None:
        return ""
    if hi is None:
        return f"{lo}+ yrs"
    if lo is None or lo == hi:
        return f"up to {hi} yrs" if lo is None else f"{lo} yrs"
    return f"{lo}–{hi} yrs"


def location_text(locations: Sequence[Mapping[str, Any]] | None, remote_type: str | None) -> str:
    parts: list[str] = []
    for loc in locations or []:
        city, st, raw = loc.get("city"), loc.get("admin1"), loc.get("raw")
        label = f"{city}, {st}" if city and st else (city or st or raw)
        if label and label not in parts:
            parts.append(str(label))
    # State-only entries are redundant when a city in that state is listed.
    parts = [p for p in parts if not any(q != p and q.endswith(f", {p}") for q in parts)]
    text = "; ".join(parts[:2]) + (f" +{len(parts) - 2}" if len(parts) > 2 else "")
    if remote_type == "remote":
        return f"Remote ({text})" if text else "Remote"
    return text or ("Remote" if remote_type == "remote" else "Location not stated")


def safe_http_url(url: str | None) -> str | None:
    """Return url only if it is an absolute http(s) URL (blocks javascript:, data:, etc.)."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme in ("http", "https") and parts.netloc:
        return url.strip()
    return None


def warn_chips(reasons: Sequence[str] | None) -> list[str]:
    return [WARN_TEXT[r] for r in reasons or [] if r in WARN_TEXT]


def quiet_chips(reasons: Sequence[str] | None) -> list[str]:
    return [QUIET_TEXT[r] for r in reasons or [] if r in QUIET_TEXT]


def reason_text(reason: str) -> str:
    if reason in EXCLUSION_TEXT:
        return EXCLUSION_TEXT[reason]
    if reason.startswith("years:min_"):
        return f"Requires {reason[len('years:min_') :]} years"
    if reason in WARN_TEXT:
        return WARN_TEXT[reason].capitalize()
    return QUIET_TEXT.get(reason) or INFO_TEXT.get(reason, reason)


def exclusion_reasons(reasons: Sequence[str] | None) -> list[str]:
    """Machine reasons that caused exclusion."""
    return [r for r in reasons or [] if r in EXCLUSION_TEXT or r.startswith("years:min_")]


def tier_label(tier: int | None) -> str:
    return f"T{tier}" if tier else ""


def trend_chart(trend: list[dict], width: int = 520, height: int = 120) -> dict:
    """Geometry for the server-rendered weekly opened-vs-closed bar chart (inline SVG).

    Returns {"w","h","bars":[{x,w,y,h,kind,label}], "max", "summary"}; pure so it is testable.
    """
    n = max(len(trend), 1)
    top = max([int(t["opened"]) for t in trend] + [int(t["closed"]) for t in trend] + [1])
    pad_b, pad_t = 4, 4
    plot_h = height - pad_b - pad_t
    slot = width / n
    bw = max(slot * 0.38, 1.0)
    bars = []
    for i, t in enumerate(trend):
        for j, kind in enumerate(("opened", "closed")):
            v = int(t[kind])
            h = round(v / top * plot_h, 1)
            bars.append(
                {
                    "x": round(i * slot + slot * 0.08 + j * (bw + 1), 1),
                    "y": round(height - pad_b - h, 1),
                    "w": round(bw, 1),
                    "h": h,
                    "kind": kind,
                    "label": f"week of {t['week']}: {v} {kind}",
                }
            )
    so, sc = sum(int(t["opened"]) for t in trend), sum(int(t["closed"]) for t in trend)
    return {
        "w": width,
        "h": height,
        "bars": bars,
        "max": top,
        "summary": f"{so} opened and {sc} closed over the last {len(trend)} weeks",
    }
