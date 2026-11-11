"""Verify seeded sources by fetching each board once (never writes jobs)."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from daily_job_tool.adapters import registry
from daily_job_tool.adapters.base import SourceRef
from daily_job_tool.http.client import CrawlClient
from daily_job_tool.http.errors import CrawlError, FetchError, HostCircuitOpen, NotFound

TRANSIENT = (HostCircuitOpen,)


@dataclass
class VerifyRow:
    source_id: int
    adapter: str
    token: str
    result: str  # ok | not_found | broken | error (transient; left unverified)
    board_name: str | None = None
    jobs: int = 0
    note: str = ""


async def verify_sources(
    engine: AsyncEngine,
    client: CrawlClient,
    *,
    limit: int | None = None,
    adapter: str | None = None,
    concurrency: int = 4,
) -> list[VerifyRow]:
    """Fetch each unverified active source once. Verified <=> detection_confidence IS NOT NULL."""
    async with engine.connect() as conn:
        srcs = (
            await conn.execute(
                text(
                    "SELECT s.id, s.adapter, s.token, s.config FROM sources s "
                    "JOIN companies c ON c.id = s.company_id "
                    "WHERE s.status = 'active' AND s.detection_confidence IS NULL "
                    "AND c.status = 'active' AND c.exclude_reason IS NULL "
                    "AND (CAST(:a AS text) IS NULL OR s.adapter = :a) ORDER BY s.id "
                    "LIMIT :n"
                ),
                {"a": adapter, "n": limit if limit is not None else 1_000_000},
            )
        ).all()
    sem = asyncio.Semaphore(concurrency)

    async def one(s) -> VerifyRow:
        row = VerifyRow(s.id, s.adapter, s.token, "error")
        async with sem:
            try:
                res = await registry.get_adapter(s.adapter).fetch_jobs(
                    SourceRef(id=s.id, adapter=s.adapter, token=s.token, config=s.config or {}),
                    client,
                )
                row.result, row.board_name, row.jobs = "ok", res.board_name, len(res.jobs)
            except asyncio.CancelledError:
                raise
            except NotFound as exc:
                row.result, row.note = "not_found", str(exc)[:200]
            except (CrawlError, KeyError, ValueError) as exc:
                transient = isinstance(exc, TRANSIENT) or (
                    isinstance(exc, FetchError) and not isinstance(exc, NotFound)
                )
                row.result = "error" if transient else "broken"
                row.note = f"{type(exc).__name__}: {exc}"[:200]
        await _record(engine, row)
        return row

    return list(await asyncio.gather(*(one(s) for s in srcs)))


async def _record(engine: AsyncEngine, row: VerifyRow) -> None:
    day = datetime.now(UTC).date().isoformat()
    async with engine.begin() as conn:
        if row.result == "ok":
            note = f"manual; verified {day} board={row.board_name!r} jobs={row.jobs}"
            await conn.execute(
                text(
                    "UPDATE sources SET detection_confidence = 1.0, detected_by = :d WHERE id = :i"
                ),
                {"d": note, "i": row.source_id},
            )
        elif row.result in ("not_found", "broken"):
            await conn.execute(
                text("UPDATE sources SET status = 'broken', detected_by = :d WHERE id = :i"),
                {
                    "d": f"manual; verify failed {day}: {row.result} {row.note}"[:500],
                    "i": row.source_id,
                },
            )
