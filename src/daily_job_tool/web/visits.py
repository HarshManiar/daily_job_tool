"""'New since your last visit' state, stored in app_state.

Goal: markers persist while the owner browses and reloads during a day, and a new day starts
a fresh "since last visit".

Two keys (ISO-8601 UTC strings):
  last_activity_at  last time the home page was opened (touched on each full page load)
  last_visit_at     the baseline: jobs with first_seen_at > baseline are "new"

Rule (`effective_since`, pure, takes `now` so tests inject a clock):
  * no activity recorded yet (first ever visit)  -> no baseline, nothing is marked new;
  * now - last_activity_at > SESSION_GAP (12 h)  -> a NEW session: baseline = last_activity_at
    (i.e. "since I was last here");
  * otherwise (same session)                     -> baseline stays last_visit_at, so a reload
    does not erase the markers.
`touch` (called by POST /visit/touch from the page after it renders, so GET stays free of
writes) persists that decision: on first visit or a rolled session it sets last_visit_at, and it
always bumps last_activity_at to now. "Mark all seen" sets both to now. The render and the touch
use the same function, so what the owner saw is what gets persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

SESSION_GAP = timedelta(hours=12)
K_VISIT = "last_visit_at"
K_ACTIVITY = "last_activity_at"


def _parse(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def effective_since(state: dict[str, str], now: datetime) -> tuple[datetime | None, bool]:
    """Return (baseline or None, session_rolls). See the module docstring."""
    activity = _parse(state.get(K_ACTIVITY))
    visit = _parse(state.get(K_VISIT))
    if activity is None:
        return None, True
    if now - activity > SESSION_GAP:
        return activity, True
    return (visit or activity), False


async def read_state(conn: AsyncConnection) -> dict[str, str]:
    res = await conn.execute(
        text("SELECT key, value FROM app_state WHERE key = ANY(:k)"),
        {"k": [K_VISIT, K_ACTIVITY]},
    )
    return {r.key: r.value for r in res}


async def _put(conn: AsyncConnection, key: str, value: datetime) -> None:
    await conn.execute(
        text(
            "INSERT INTO app_state (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"k": key, "v": value.astimezone(UTC).isoformat()},
    )


async def touch(conn: AsyncConnection, now: datetime) -> None:
    """Persist the session decision and record activity (RW connection, in a transaction)."""
    state = await read_state(conn)
    since, rolls = effective_since(state, now)
    if rolls:
        await _put(conn, K_VISIT, since or now)
    await _put(conn, K_ACTIVITY, now)


async def mark_all_seen(conn: AsyncConnection, now: datetime) -> None:
    await _put(conn, K_VISIT, now)
    await _put(conn, K_ACTIVITY, now)


def since_text(since: datetime | None) -> str:
    if since is None:
        return ""
    local: Any = since.astimezone()
    return local.strftime("%a %b %-d, %-I:%M %p")
