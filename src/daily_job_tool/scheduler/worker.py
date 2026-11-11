"""Claim loop and worker (ARCHITECTURE.md section 8)."""

import asyncio
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from daily_job_tool.adapters import registry
from daily_job_tool.config import get_settings
from daily_job_tool.http.client import CrawlClient
from daily_job_tool.normalize import NormContext
from daily_job_tool.pipeline.ingest import ingest_source
from daily_job_tool.scheduler.breaker import apply_breaker

log = logging.getLogger(__name__)

LEASE = timedelta(minutes=15)
ORPHAN_AFTER = timedelta(minutes=30)
CRASH_RETRY = timedelta(hours=1)

CLAIM_SQL = """
UPDATE sources s SET locked_until = CAST(:locked AS timestamptz)
WHERE s.id IN (
  SELECT s2.id FROM sources s2 JOIN companies c ON c.id = s2.company_id
  WHERE s2.status = 'active' AND s2.next_crawl_at <= CAST(:now AS timestamptz)
    AND (s2.circuit_open_until IS NULL OR s2.circuit_open_until < CAST(:now AS timestamptz))
    AND (s2.locked_until IS NULL OR s2.locked_until < CAST(:now AS timestamptz))
    AND c.status = 'active' AND NOT c.is_staffing AND c.exclude_reason IS NULL
    AND (CAST(:adapter AS text) IS NULL OR s2.adapter = :adapter)
    AND (CAST(:source_id AS bigint) IS NULL OR s2.id = :source_id)
    AND NOT (s2.id = ANY(CAST(:skip AS bigint[])))
  ORDER BY s2.priority, s2.next_crawl_at, s2.id
  LIMIT :n
  FOR UPDATE OF s2 SKIP LOCKED
)
RETURNING s.id, s.adapter, s.token, s.priority, s.next_crawl_at
"""


@dataclass
class Claimed:
    id: int
    adapter: str
    token: str
    priority: int


@dataclass
class CrawlSummary:
    source_id: int
    adapter: str
    token: str
    outcome: str
    jobs_seen: int = 0
    postings_new: int = 0
    postings_closed: int = 0
    error: str | None = None


async def claim_sources(
    engine: AsyncEngine,
    n: int,
    *,
    adapter: str | None = None,
    source_id: int | None = None,
    skip: set[int] | None = None,
    now: datetime | None = None,
) -> list[Claimed]:
    """Atomically lease up to n due sources (SKIP LOCKED); returned in priority order."""
    if n <= 0:
        return []
    now = now or datetime.now(UTC)
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(CLAIM_SQL),
                {
                    "now": now,
                    "locked": now + LEASE,
                    "adapter": adapter,
                    "source_id": source_id,
                    "skip": sorted(skip or ()),
                    "n": n,
                },
            )
        ).all()
    rows = sorted(rows, key=lambda r: (r.priority, r.next_crawl_at, r.id))
    claimed = [Claimed(r.id, r.adapter, r.token, r.priority) for r in rows]
    return claimed


async def cleanup_orphan_runs(engine: AsyncEngine, *, now: datetime | None = None) -> int:
    """Finish crawl_runs that started long ago and never finished (crashed worker)."""
    now = now or datetime.now(UTC)
    async with engine.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE crawl_runs SET outcome = 'timeout', finished_at = :now, "
                "error = 'orphaned: worker died before finishing' "
                "WHERE finished_at IS NULL AND started_at < :cutoff"
            ),
            {"now": now, "cutoff": now - ORPHAN_AFTER},
        )
        await conn.execute(
            text("UPDATE sources SET locked_until = NULL WHERE locked_until < :now"), {"now": now}
        )
    return res.rowcount


async def _release(engine: AsyncEngine, source_id: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE sources SET locked_until = NULL WHERE id = :i"), {"i": source_id}
        )


async def crawl_one(
    engine: AsyncEngine,
    c: Claimed,
    ctx: NormContext,
    client: CrawlClient,
    *,
    us_only: bool = True,
) -> CrawlSummary:
    """Ingest one claimed source. Never raises (except cancellation); always unlocks."""
    summ = CrawlSummary(c.id, c.adapter, c.token, "error")
    try:
        try:
            res = await ingest_source(
                engine, c.id, registry.get_adapter(c.adapter), client, ctx, us_only=us_only
            )
            summ = CrawlSummary(
                c.id, c.adapter, c.token, res.outcome, res.jobs_seen,
                res.postings_new, res.postings_closed, res.error,
            )  # fmt: skip
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("source %s crashed", c.id)
            summ.error = f"{type(exc).__name__}: {exc}"[:500]
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE sources SET consecutive_failures = consecutive_failures + 1, "
                        "next_crawl_at = :t WHERE id = :i"
                    ),
                    {"t": datetime.now(UTC) + CRASH_RETRY, "i": c.id},
                )
        try:
            async with engine.begin() as conn:
                await apply_breaker(conn, c.id)
        except Exception:  # noqa: BLE001
            log.exception("breaker failed for source %s", c.id)
    finally:
        try:
            await asyncio.shield(_release(engine, c.id))
        except Exception:  # noqa: BLE001
            log.exception("could not release lock for source %s", c.id)
    return summ


def _default_client_factory() -> CrawlClient:
    return CrawlClient(get_settings().user_agent)


async def run_worker(
    engine: AsyncEngine,
    ctx: NormContext,
    *,
    once: bool = False,
    limit: int | None = None,
    adapter: str | None = None,
    source_id: int | None = None,
    concurrency: int = 8,
    poll_seconds: float = 5,
    client_factory: Callable[[], CrawlClient] = _default_client_factory,
    us_only: bool = True,
    install_signals: bool = True,
) -> list[CrawlSummary]:
    """Claim and crawl due sources. once=True: stop when nothing is due (or limit reached)."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    if install_signals:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
                installed.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
    summaries: list[CrawlSummary] = []
    seen: set[int] = set()
    sem = asyncio.Semaphore(concurrency)
    client = client_factory()

    async def one(c: Claimed) -> CrawlSummary:
        async with sem:
            return await crawl_one(engine, c, ctx, client, us_only=us_only)

    try:
        await cleanup_orphan_runs(engine)
        while not stop.is_set():
            n = concurrency if limit is None else min(concurrency, limit - len(summaries))
            if n <= 0:
                break
            claimed = await claim_sources(
                engine, n, adapter=adapter, source_id=source_id, skip=seen if once else None
            )
            if not claimed:
                if once:
                    break
                try:
                    await asyncio.wait_for(stop.wait(), poll_seconds)
                except TimeoutError:
                    pass
                continue
            seen.update(c.id for c in claimed)
            # in-flight work always finishes, even on shutdown signal
            summaries.extend(await asyncio.gather(*(one(c) for c in claimed)))
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        await client.aclose()
    return summaries
