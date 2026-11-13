"""Saved searches: a name plus a serialized FeedFilters (no alerts, no tokens)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from daily_job_tool.web import queries
from daily_job_tool.web.queries import FeedFilters

MAX_NAME = 80
MAX_SAVED = 50


@dataclass
class SavedSearch:
    id: int
    name: str
    filters: FeedFilters | None  # None -> stored query failed validation
    error: str = ""
    matches: int = 0
    new: int = 0

    @property
    def href(self) -> str:
        qs = self.filters.query_string() if self.filters else ""
        return "/?" + qs if qs else "/"


def parse_query(raw: Any) -> tuple[FeedFilters | None, str]:
    """Validate a stored query JSON; unknown fields are ignored, invalid -> (None, message)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, "stored query is not valid JSON"
    if not isinstance(raw, dict):
        return None, "stored query is not an object"
    try:
        f = FeedFilters.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        return None, f"invalid filter {'.'.join(map(str, first['loc']))}: {first['msg']}"
    return f.model_copy(update={"offset": 0, "reason": ""}), ""


def clean_name(name: str) -> str:
    return " ".join((name or "").split())[:MAX_NAME]


async def list_saved(conn: AsyncConnection, since: Any, *, with_counts: bool = True):
    rows = (
        await conn.execute(
            text("SELECT id, name, query FROM saved_searches ORDER BY lower(name), id LIMIT :n"),
            {"n": MAX_SAVED},
        )
    ).all()
    out: list[SavedSearch] = []
    for r in rows:
        f, err = parse_query(r.query)
        item = SavedSearch(r.id, r.name, f, err)
        if f is not None and with_counts:
            item.matches, item.new = await queries.match_and_new_counts(conn, f, since)
        out.append(item)
    return out


async def create(conn: AsyncConnection, name: str, f: FeedFilters) -> int | None:
    n = (await conn.execute(text("SELECT count(*) FROM saved_searches"))).scalar_one()
    if n >= MAX_SAVED:
        return None
    return (
        await conn.execute(
            text(
                "INSERT INTO saved_searches (name, query) VALUES (:n, CAST(:q AS jsonb)) "
                "RETURNING id"
            ),
            {"n": name, "q": json.dumps(f.non_default())},
        )
    ).scalar_one()


async def rename(conn: AsyncConnection, sid: int, name: str) -> bool:
    res = await conn.execute(
        text("UPDATE saved_searches SET name = :n WHERE id = :i"), {"n": name, "i": sid}
    )
    return res.rowcount > 0


async def delete(conn: AsyncConnection, sid: int) -> bool:
    res = await conn.execute(text("DELETE FROM saved_searches WHERE id = :i"), {"i": sid})
    return res.rowcount > 0
