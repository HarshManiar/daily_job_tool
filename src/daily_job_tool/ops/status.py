"""Operational status snapshot, shared by the `status` command (and later the web UI)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from daily_job_tool.ops.backup import default_backup_dir, last_backup_time
from daily_job_tool.ops.heartbeat import classify, default_heartbeat_path, read_heartbeat

DUE_SQL = """
SELECT count(*) FROM sources s JOIN companies c ON c.id = s.company_id
WHERE s.status = 'active' AND s.next_crawl_at <= :now
  AND (s.circuit_open_until IS NULL OR s.circuit_open_until < :now)
  AND (s.locked_until IS NULL OR s.locked_until < :now)
  AND c.status = 'active' AND NOT c.is_staffing AND c.exclude_reason IS NULL
"""


async def collect_status(
    engine: AsyncEngine,
    now: datetime | None = None,
    *,
    backup_dir: Path | None = None,
    heartbeat_path: Path | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    async with engine.connect() as conn:

        async def one(sql: str) -> Any:
            return (await conn.execute(text(sql), {"now": now})).scalar()

        last_finished = await one("SELECT max(finished_at) FROM crawl_runs")
        due = await one(DUE_SQL)
        in_flight = await one("SELECT count(*) FROM sources WHERE locked_until > :now")
        failing = await one(
            "SELECT count(*) FROM sources WHERE status = 'active' AND consecutive_failures > 0"
        )
        blocked = await one("SELECT count(*) FROM sources WHERE status = 'blocked'")
        paused = await one("SELECT count(*) FROM sources WHERE status = 'paused'")
        active = await one("SELECT count(*) FROM sources WHERE status = 'active'")
        open_jobs = await one("SELECT count(*) FROM jobs WHERE closed_at IS NULL")
        db_size = await one("SELECT pg_database_size(current_database())")
    hb = read_heartbeat(heartbeat_path or default_heartbeat_path())
    backup_at = last_backup_time(backup_dir or default_backup_dir())
    return {
        "now": now,
        "last_crawl_finished_at": last_finished,
        "last_crawl_age_seconds": (now - last_finished).total_seconds() if last_finished else None,
        "sources_active": active,
        "sources_due": due,
        "sources_in_flight": in_flight,
        "sources_failing": failing,
        "sources_blocked": blocked,
        "sources_paused": paused,
        "open_jobs": open_jobs,
        "db_size_bytes": db_size,
        "last_backup_at": backup_at,
        "worker_state": classify(hb, now),
        "worker": hb,
    }


def human_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(max(seconds, 0))
    if s < 90:
        return f"{s}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    if s < 172800:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def human_size(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{x:.0f} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024
    return f"{n} B"


def format_status(st: dict[str, Any], bot_contact: str) -> str:
    now = st["now"]
    w = st["worker"] or {}
    lines = [
        f"last crawl finished : {human_age(st['last_crawl_age_seconds'])}",
        f"sources             : {st['sources_active']} active, {st['sources_due']} due now, "
        f"{st['sources_in_flight']} in flight",
        f"problems            : {st['sources_failing']} failing, {st['sources_blocked']} blocked, "
        f"{st['sources_paused']} paused",
        f"open jobs           : {st['open_jobs']}",
        f"database size       : {human_size(st['db_size_bytes'])}",
    ]
    b = st["last_backup_at"]
    lines.append(f"last backup         : {human_age((now - b).total_seconds()) if b else 'never'}")
    wline = f"worker              : {st['worker_state']}"
    if w and st["worker_state"] != "not running":
        wline += f" (pid {w.get('pid')}, in flight {w.get('in_flight')}"
        if w.get("last_finished_source"):
            wline += f", last finished {w['last_finished_source']}"
        wline += ")"
    lines.append(wline)
    if not bot_contact.strip():
        lines.append("WARNING: BOT_CONTACT is not set; set it in .env (email or https URL)")
    return "\n".join(lines)
