"""'Check these by hand': companies whose sites forbid or block automated reading.

data/manual_check.csv is loaded once and re-read only when its mtime changes. A per-company
"checked" date lives in app_state under `manual_checked:<domain-or-name-slug>`.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from daily_job_tool.registry.importer import normalize_domain, slugify
from daily_job_tool.web.format import safe_http_url

KEY_PREFIX = "manual_checked:"


def default_csv_path() -> Path:
    env = os.environ.get("DAILY_JOB_TOOL_MANUAL_CSV")
    return Path(env) if env else Path(__file__).resolve().parents[3] / "data" / "manual_check.csv"


REASON_TEXT = {
    "robots.txt forbids crawling": "Their robots.txt asks automated tools to stay out.",
    "bot protection blocks automated visitors": "Their site blocks automated visitors.",
    "owner decided not to build": "No reader built for this site (your decision).",
    "vendor API forbids crawlers; public pages not used": "The job-board vendor forbids crawlers.",
    "jobs only listed on LinkedIn": "Jobs are only posted on LinkedIn.",
    "source marked blocked in DB": "The site started blocking us.",
}


@dataclass(frozen=True)
class ManualRow:
    key: str
    name: str
    industry: str
    priority: int
    url: str | None  # http(s) only
    reason: str
    guessed: bool


def _parse(path: Path) -> list[ManualRow]:
    rows: list[ManualRow] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            name = (r.get("name") or "").strip()
            if not name:
                continue
            raw = (r.get("careers_url") or "").strip()
            url = safe_http_url(raw)
            dom = normalize_domain(raw) if url else None
            try:
                prio = int((r.get("priority") or "2").strip())
            except ValueError:
                prio = 2
            reason = (r.get("reason") or "").strip()
            rows.append(
                ManualRow(
                    key=KEY_PREFIX + (dom or slugify(name)),
                    name=name,
                    industry=(r.get("industry") or "").strip(),
                    priority=prio,
                    url=url,
                    reason=REASON_TEXT.get(reason, reason),
                    guessed="guessed path" in (r.get("source_note") or ""),
                )
            )
    rows.sort(key=lambda x: (x.priority, x.name.lower()))
    return rows


_cache: dict[Path, tuple[float, list[ManualRow]]] = {}


def load_rows(path: Path | None = None) -> list[ManualRow]:
    p = path or default_csv_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    hit = _cache.get(p)
    if hit is None or hit[0] != mtime:
        hit = (mtime, _parse(p))
        _cache[p] = hit
    return hit[1]


def filter_rows(rows: list[ManualRow], q: str) -> list[ManualRow]:
    q = q.strip().lower()
    if not q:
        return rows
    return [r for r in rows if q in r.name.lower() or q in r.industry.lower()]


async def checked_dates(conn: AsyncConnection) -> dict[str, date]:
    res = await conn.execute(
        text("SELECT key, value FROM app_state WHERE key LIKE :p"), {"p": KEY_PREFIX + "%"}
    )
    out: dict[str, date] = {}
    for r in res:
        try:
            out[r.key] = date.fromisoformat(r.value)
        except ValueError:
            continue
    return out


async def toggle(conn: AsyncConnection, key: str, today: date) -> date | None:
    """Mark checked today, or clear the mark when it is already set. Returns the new state."""
    cur = (await conn.execute(text("SELECT 1 FROM app_state WHERE key = :k"), {"k": key})).first()
    if cur is not None:
        await conn.execute(text("DELETE FROM app_state WHERE key = :k"), {"k": key})
        return None
    await conn.execute(
        text("INSERT INTO app_state (key, value) VALUES (:k, :v)"),
        {"k": key, "v": today.isoformat()},
    )
    return today


def checked_text(d: date | None, today: date) -> str:
    if d is None:
        return ""
    n = (today - d).days
    if n <= 0:
        return "checked today"
    if n == 1:
        return "checked yesterday"
    return f"checked {n} days ago"


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""
