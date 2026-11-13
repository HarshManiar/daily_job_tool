"""Claim loop and worker (ARCHITECTURE.md section 8)."""

import asyncio
import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from daily_job_tool.adapters import registry
from daily_job_tool.config import get_user_agent
from daily_job_tool.http.client import CrawlClient
from daily_job_tool.normalize import NormContext
from daily_job_tool.ops.heartbeat import HeartbeatWriter
from daily_job_tool.ops.power import PowerAssertion
from daily_job_tool.pipeline.ingest import compute_next_crawl_at, ingest_source
from daily_job_tool.scheduler.breaker import apply_breaker

log = logging.getLogger(__name__)

LEASE = timedelta(minutes=15)
ORPHAN_AFTER = timedelta(minutes=30)
CRASH_RETRY = timedelta(hours=1)
SOURCE_TIMEOUT_SECONDS = 45 * 60.0
SHUTDOWN_GRACE_SECONDS = 60.0
SLEEP_WARN_SECONDS = 120.0  # wall-clock minus monotonic drift that implies the laptop slept
CAP_ERROR = "source exceeded wall-clock cap"
CANCEL_ERROR = "cancelled: worker shutdown"

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


async def _finish_abnormal(engine: AsyncEngine, c: Claimed, error: str) -> None:
    """Close a source's open crawl_runs row as 'timeout' and apply the failure backoff.

    Ingest commits per source in a single transaction, so a cancelled ingest has already been
    rolled back; the only thing left behind is the run row opened (and committed) at the start.
    """
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE crawl_runs SET outcome = 'timeout', finished_at = :now, complete = false, "
                "error = :err WHERE source_id = :i AND finished_at IS NULL"
            ),
            {"now": now, "err": error, "i": c.id},
        )
        if res.rowcount == 0:  # cancelled before ingest opened its row: still leave a record
            await conn.execute(
                text(
                    "INSERT INTO crawl_runs (source_id, adapter, started_at, finished_at, "
                    "outcome, complete, error) VALUES (:i, :a, :now, :now, 'timeout', false, :err)"
                ),
                {"i": c.id, "a": c.adapter, "now": now, "err": error},
            )
        ewma = (
            await conn.execute(
                text("SELECT new_postings_ewma FROM sources WHERE id = :i"), {"i": c.id}
            )
        ).scalar_one_or_none()
        await conn.execute(
            text(
                "UPDATE sources SET consecutive_failures = consecutive_failures + 1, "
                "last_crawled_at = :now, last_outcome = 'timeout', next_crawl_at = :t "
                "WHERE id = :i"
            ),
            {"now": now, "t": compute_next_crawl_at(float(ewma or 0.0), now), "i": c.id},
        )


async def crawl_one(
    engine: AsyncEngine,
    c: Claimed,
    ctx: NormContext,
    client: CrawlClient,
    *,
    us_only: bool = True,
    source_timeout: float | None = SOURCE_TIMEOUT_SECONDS,
) -> CrawlSummary:
    """Ingest one claimed source. Never raises (except cancellation); always unlocks.

    A source running longer than `source_timeout` wall-clock seconds is cancelled and recorded
    as outcome 'timeout'. External cancellation (shutdown) is recorded the same way, then
    re-raised.
    """
    summ = CrawlSummary(c.id, c.adapter, c.token, "error")
    wall0, mono0 = time.time(), time.monotonic()
    try:
        try:
            async with asyncio.timeout(source_timeout) as cm:
                res = await ingest_source(
                    engine, c.id, registry.get_adapter(c.adapter), client, ctx, us_only=us_only
                )
            summ = CrawlSummary(
                c.id, c.adapter, c.token, res.outcome, res.jobs_seen,
                res.postings_new, res.postings_closed, res.error,
            )  # fmt: skip
        except asyncio.CancelledError:
            try:
                await asyncio.shield(_finish_abnormal(engine, c, CANCEL_ERROR))
            except Exception:  # noqa: BLE001
                log.exception("could not record cancellation for source %s", c.id)
            raise
        except TimeoutError as exc:
            if not cm.expired():  # a TimeoutError from inside ingest, not our cap
                summ.error = f"{type(exc).__name__}: {exc}"[:500]
                await _crash_backoff(engine, c.id)
            else:
                log.warning("source %s (%s/%s) exceeded %ss cap", c.id, c.adapter, c.token,
                            source_timeout)  # fmt: skip
                summ = CrawlSummary(c.id, c.adapter, c.token, "timeout", error=CAP_ERROR)
                await asyncio.shield(_finish_abnormal(engine, c, CAP_ERROR))
        except Exception as exc:  # noqa: BLE001
            log.exception("source %s crashed", c.id)
            summ.error = f"{type(exc).__name__}: {exc}"[:500]
            await _crash_backoff(engine, c.id)
        drift = (time.time() - wall0) - (time.monotonic() - mono0)
        if drift > SLEEP_WARN_SECONDS:
            log.warning(
                "source %s: wall clock ran %.0fs ahead of monotonic time; machine likely slept",
                c.id, drift,
            )  # fmt: skip
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


async def _crash_backoff(engine: AsyncEngine, source_id: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE sources SET consecutive_failures = consecutive_failures + 1, "
                "next_crawl_at = :t WHERE id = :i"
            ),
            {"t": datetime.now(UTC) + CRASH_RETRY, "i": source_id},
        )


def _default_client_factory() -> CrawlClient:
    return CrawlClient(get_user_agent())


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
    source_timeout_seconds: float | None = SOURCE_TIMEOUT_SECONDS,
    shutdown_grace_seconds: float = SHUTDOWN_GRACE_SECONDS,
    stop: asyncio.Event | None = None,
    force_stop: asyncio.Event | None = None,
    power: PowerAssertion | None = None,
    heartbeat: HeartbeatWriter | None = None,
) -> list[CrawlSummary]:
    """Claim and crawl due sources with a continuous pool of up to `concurrency` in flight.

    once=True: end when nothing is due and nothing is in flight (or limit reached).
    `stop` (first SIGINT/SIGTERM): stop claiming, give in-flight sources `shutdown_grace_seconds`,
    then cancel them. `force_stop` (second signal): cancel immediately.
    """
    stop = stop if stop is not None else asyncio.Event()
    force_stop = force_stop if force_stop is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []

    def on_signal() -> None:
        if stop.is_set():
            force_stop.set()
        stop.set()

    if install_signals:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, on_signal)
                installed.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
    summaries: list[CrawlSummary] = []
    seen: set[int] = set()
    started = 0
    in_flight: dict[asyncio.Task[CrawlSummary], Claimed] = {}
    client = client_factory()
    stop_waiter = asyncio.ensure_future(stop.wait())
    force_waiter = asyncio.ensure_future(force_stop.wait())

    def pulse(*, force: bool = False) -> None:
        if power is not None:
            power.update(len(in_flight))
        if heartbeat is not None:
            heartbeat.tick(len(in_flight), force=force)

    def collect(done: set[asyncio.Task[CrawlSummary]]) -> None:
        for t in done:
            c = in_flight.pop(t)
            if heartbeat is not None:
                heartbeat.last_finished_source = f"{c.adapter}/{c.token}"
            if t.cancelled():
                summaries.append(
                    CrawlSummary(c.id, c.adapter, c.token, "timeout", error=CANCEL_ERROR)
                )
            elif t.exception() is not None:
                log.error("source %s task failed: %r", c.id, t.exception())
            else:
                summaries.append(t.result())

    try:
        await cleanup_orphan_runs(engine)
        exhausted = False
        while not stop.is_set():
            free = concurrency - len(in_flight)
            if limit is not None:
                free = min(free, limit - started)
            if free > 0 and not exhausted:
                skip = set(seen) if once else set()
                skip |= {c.id for c in in_flight.values()}
                claimed = await claim_sources(
                    engine, free, adapter=adapter, source_id=source_id, skip=skip
                )
                if heartbeat is not None:
                    heartbeat.poll()
                if not claimed and once:
                    exhausted = True
                for c in claimed:
                    seen.add(c.id)
                    started += 1
                    task = asyncio.ensure_future(
                        crawl_one(
                            engine,
                            c,
                            ctx,
                            client,
                            us_only=us_only,
                            source_timeout=source_timeout_seconds,
                        )  # fmt: skip
                    )
                    in_flight[task] = c
            pulse()
            limit_hit = limit is not None and started >= limit
            exhausted = exhausted or limit_hit
            if not in_flight and (once or limit_hit) and exhausted:
                break
            # wake on: a source finishing (refill), a stop signal, or the poll interval
            # (only needed while slots are free and more work may appear)
            can_refill = not exhausted and len(in_flight) < concurrency
            timeout = poll_seconds if can_refill else None
            if heartbeat is not None:
                timeout = (
                    heartbeat.interval if timeout is None else min(timeout, heartbeat.interval)
                )
            await asyncio.wait(
                {*in_flight, stop_waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            collect({t for t in in_flight if t.done()})
            pulse()

        # shutdown path (also reached with nothing in flight)
        if in_flight and not force_stop.is_set():
            log.info("stopping: waiting up to %ss for %d source(s)", shutdown_grace_seconds,
                     len(in_flight))  # fmt: skip
            await _wait_all(in_flight, force_waiter, shutdown_grace_seconds)
            collect({t for t in in_flight if t.done()})
        if in_flight:
            log.warning("cancelling %d in-flight source(s)", len(in_flight))
            for t in in_flight:
                t.cancel()
            await asyncio.gather(*in_flight, return_exceptions=True)
            collect(set(in_flight))
    finally:
        if power is not None:
            power.stop()
        if heartbeat is not None:
            heartbeat.clear()
        for t in (stop_waiter, force_waiter):
            t.cancel()
        for t in list(in_flight):  # only on unexpected exit
            t.cancel()
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)
        for sig in installed:
            loop.remove_signal_handler(sig)
        await client.aclose()
    return summaries


async def _wait_all(
    tasks: dict[asyncio.Task[CrawlSummary], Claimed], force: asyncio.Future[bool], grace: float
) -> None:
    """Wait until every task is done, `force` fires, or `grace` seconds pass."""
    deadline = time.monotonic() + grace
    while any(not t.done() for t in tasks) and not force.done():
        left = deadline - time.monotonic()
        if left <= 0:
            return
        await asyncio.wait(
            {t for t in tasks if not t.done()} | {force},
            timeout=left,
            return_when=asyncio.FIRST_COMPLETED,
        )
