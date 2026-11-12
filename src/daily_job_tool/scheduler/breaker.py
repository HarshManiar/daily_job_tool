"""Per-source circuit breaker (ARCHITECTURE.md section 8)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

FAILURE_THRESHOLD = 5
BASE_PAUSE = timedelta(hours=6)
MAX_PAUSE = timedelta(days=7)
STREAK = 3
BLOCKED_STREAK = 2
BLOCKED_MARKER = "blocked by site"  # crawl_runs.error prefix written by ingest


def circuit_pause(failures: int) -> timedelta | None:
    """min(2^(failures-5) * 6h, 7d) once failures >= 5, else None."""
    if failures < FAILURE_THRESHOLD:
        return None
    return min(BASE_PAUSE * 2 ** min(failures - FAILURE_THRESHOLD, 10), MAX_PAUSE)


async def apply_breaker(
    conn: AsyncConnection, source_id: int, *, now: datetime | None = None
) -> dict[str, Any]:
    """Apply breaker rules after an ingest. Returns what changed (empty dict = nothing)."""
    now = now or datetime.now(UTC)
    failures = (
        await conn.execute(
            text("SELECT consecutive_failures FROM sources WHERE id = :i FOR UPDATE"),
            {"i": source_id},
        )
    ).scalar_one_or_none()
    if failures is None:
        return {}
    changes: dict[str, Any] = {}
    pause = circuit_pause(failures)
    if pause is not None:
        until = now + pause
        await conn.execute(
            text("UPDATE sources SET circuit_open_until = :u WHERE id = :i"),
            {"u": until, "i": source_id},
        )
        changes["circuit_open_until"] = until
    recent = (
        (
            await conn.execute(
                text(
                    "SELECT outcome::text FROM crawl_runs WHERE source_id = :i "
                    "AND finished_at IS NOT NULL ORDER BY started_at DESC, id DESC LIMIT :n"
                ),
                {"i": source_id, "n": STREAK},
            )
        )
        .scalars()
        .all()
    )
    last_runs = (
        await conn.execute(
            text(
                "SELECT outcome::text AS o, error FROM crawl_runs WHERE source_id = :i "
                "AND finished_at IS NOT NULL ORDER BY started_at DESC, id DESC LIMIT :n"
            ),
            {"i": source_id, "n": BLOCKED_STREAK},
        )
    ).all()
    if len(last_runs) == BLOCKED_STREAK and all(
        r.o == "http_error" and (r.error or "").startswith(BLOCKED_MARKER) for r in last_runs
    ):
        await conn.execute(
            text(
                "UPDATE sources SET status = CAST('blocked' AS source_status) "
                "WHERE id = :i AND status = 'active'"
            ),
            {"i": source_id},
        )
        changes["status"] = "blocked"
        return changes
    if len(recent) == STREAK:
        status = {"not_found": "broken", "robots_disallowed": "blocked"}.get(recent[0])
        if status and all(o == recent[0] for o in recent):
            await conn.execute(
                text("UPDATE sources SET status = CAST(:s AS source_status) WHERE id = :i"),
                {"s": status, "i": source_id},
            )
            changes["status"] = status
    return changes
