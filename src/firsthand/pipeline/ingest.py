"""Ingest: one crawl of one source, end to end (ARCHITECTURE.md sections 0, 7, 8).

Flow: fetch (no DB transaction held) -> read existing postings -> detail fetch + normalize
(still no write transaction) -> ONE write transaction that upserts postings, upserts/revives
jobs, applies the closing rules and finishes the crawl_runs/sources rows.
The crawl_runs row is inserted and committed first, so a crash leaves evidence.
"""

import asyncio
import hashlib
import json
import logging
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ARRAY, BigInteger, Text, bindparam, literal_column, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from firsthand.adapters import registry
from firsthand.adapters.base import Adapter, RawJob, SourceRef
from firsthand.db.schema import crawl_runs, jobs, postings, sources
from firsthand.http.client import CrawlClient
from firsthand.http.errors import (
    Blocked,
    CrawlDelayTooLong,
    CrawlError,
    DomainBlocked,
    FetchError,
    HostCircuitOpen,
    NotFound,
    RobotsDisallowed,
)
from firsthand.normalize import NORMALIZER_VERSION, CompanyInfo, NormContext, SourceInfo, normalize
from firsthand.normalize.models import NormalizedJob

log = logging.getLogger(__name__)

US_REMOTE_REGIONS = frozenset({"US", "NA", "AMER", "WORLD"})
MISSED_TO_CLOSE = 2
SUSPICIOUS_RETRY = timedelta(hours=2)
GUARD_MIN_OPEN_EMPTY = 5
GUARD_MIN_OPEN_RATIO = 20
GUARD_LOSS_RATIO = 0.6
EWMA_ALPHA = 0.3
MIN_INTERVAL = timedelta(hours=4)
MAX_INTERVAL = timedelta(hours=24)
CHUNK = 500


@dataclass
class IngestResult:
    source_id: int
    run_id: int
    outcome: str
    complete: bool = False
    jobs_seen: int = 0
    postings_new: int = 0
    postings_updated: int = 0
    postings_closed: int = 0
    jobs_created: int = 0
    jobs_closed: int = 0
    skipped_non_us: int = 0
    detail_calls: int = 0
    http_requests: int = 0
    duration_ms: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def payload_hash(payload: Any) -> bytes:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).digest()


def compute_next_crawl_at(
    src: Mapping[str, Any] | float, now: datetime, *, rng: random.Random | None = None
) -> datetime:
    """now + clamp(24h / (1 + ewma), 4h, 24h), +/-10% jitter. `src` is a row or the ewma."""
    ewma = float(src if isinstance(src, int | float) else src["new_postings_ewma"] or 0.0)
    interval = min(MAX_INTERVAL, max(MIN_INTERVAL, timedelta(hours=24) / (1 + max(ewma, 0.0))))
    return now + interval * (1 + (rng or random).uniform(-0.1, 0.1))


def is_us_eligible(n: NormalizedJob) -> tuple[bool, bool]:
    """Returns (eligible, location_unknown)."""
    if "US" in n.countries or any(loc.get("country") == "US" for loc in n.locations):
        return True, False
    if n.remote_type == "remote" and US_REMOTE_REGIONS & set(n.remote_regions):
        return True, False
    resolved = bool(n.countries) or any(loc.get("country") for loc in n.locations)
    if not resolved and not n.remote_regions:
        return True, True
    return False, False


def location_key(n: NormalizedJob) -> str:
    parts: set[str] = set()
    for loc in n.locations:
        if loc.get("unknown"):
            continue
        if loc.get("geonameid"):
            parts.add(f"g{loc['geonameid']}")
        elif loc.get("country"):
            # No gazetteer ids yet (Phase 1): fall back to country/admin1/city so that
            # "SWE, New York" and "SWE, San Francisco" do not share a key.
            parts.add(
                "l:"
                + "/".join(str(loc.get(k) or "").lower() for k in ("country", "admin1", "city"))
            )
    if parts:
        return ",".join(sorted(parts))
    if n.remote_regions:
        return "remote:" + ",".join(sorted(set(n.remote_regions)))
    return "?"


def base_dedupe_key(n: NormalizedJob) -> str:
    return hashlib.sha1(f"{n.title_norm}|{location_key(n)}".encode()).hexdigest()  # noqa: S324


def _tier_lookup(adapter: Adapter):
    registry._load()  # noqa: SLF001

    def tier_of(name: str) -> int:
        if name == adapter.name:
            return adapter.tier
        cls = registry._REGISTRY.get(name)  # noqa: SLF001
        return cls.tier if cls is not None else 99

    return tier_of


def classify_failure(exc: BaseException) -> tuple[str, int | None]:
    """Map an exception from the adapter to (crawl_outcome, http_status)."""
    if isinstance(exc, NotFound):
        return "not_found", exc.status
    if isinstance(exc, RobotsDisallowed | DomainBlocked | CrawlDelayTooLong):
        return "robots_disallowed", None
    if isinstance(exc, HostCircuitOpen):
        return "circuit_open", None
    if isinstance(exc, Blocked):
        return "http_error", exc.status
    if isinstance(exc, FetchError):
        if "timeout" in exc.message.lower():
            return "timeout", exc.status
        if exc.message == "invalid JSON":
            return "parse_error", exc.status
        return "http_error", exc.status
    if isinstance(exc, CrawlError):
        return "http_error", None
    if isinstance(exc, TimeoutError):
        return "timeout", None
    return "parse_error", None


async def find_explicit_link_job(conn: AsyncConnection, company_id: int, raw: RawJob) -> int | None:
    """Hook for ARCH section 7 rule 3 (page linking to a known ATS posting). Phase 1: none."""
    return None


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _Entry:
    raw: RawJob
    hash: bytes
    kind: str  # new | changed | refresh | same
    ex: Mapping[str, Any] | None = None
    norm: NormalizedJob | None = None
    eligible: bool = True
    stored_payload: dict[str, Any] = field(default_factory=dict)
    detail_at: datetime | None = None
    posting_id: int | None = None
    job_id: int | None = None
    key: str | None = None


def _job_values(n: NormalizedJob, raw: RawJob) -> dict[str, Any]:
    v = n.model_dump()
    v["apply_url"] = n.apply_url or raw.apply_url or raw.url
    return v


def _arr(name: str, typ: Any) -> Any:
    return bindparam(name, type_=ARRAY(typ))


# ---------------------------------------------------------------------------
# Job refresh (primary posting, closed state, first_seen/freshness)
# ---------------------------------------------------------------------------


async def refresh_jobs(
    conn: AsyncConnection, job_ids: set[int], now: datetime, tier_of
) -> dict[int, int | None]:
    """Recompute primary posting, closed_at, first_seen_at, freshness_at. Returns job -> primary."""
    if not job_ids:
        return {}
    rows = (
        await conn.execute(
            text(
                "SELECT p.id, p.job_id, p.closed_at, p.first_seen_at, s.adapter "
                "FROM postings p JOIN sources s ON s.id = p.source_id WHERE p.job_id = ANY(:ids)"
            ).bindparams(_arr("ids", BigInteger)),
            {"ids": sorted(job_ids)},
        )
    ).all()
    by_job: dict[int, list[Any]] = {}
    for r in rows:
        by_job.setdefault(r.job_id, []).append(r)
    params: list[dict[str, Any]] = []
    primaries: dict[int, int | None] = {}
    for jid in job_ids:
        ps = by_job.get(jid, [])
        open_ = [p for p in ps if p.closed_at is None]
        pool = open_ or ps
        best = (
            min(pool, key=lambda p: (tier_of(p.adapter), p.first_seen_at, p.id)) if pool else None
        )
        primaries[jid] = best.id if best else None
        params.append(
            {
                "id": jid,
                "pp": best.id if best else None,
                "close": not open_,
                "fs": min(p.first_seen_at for p in ps) if ps else None,
                "now": now,
            }
        )
    await conn.execute(
        text(
            "UPDATE jobs SET primary_posting_id = CAST(:pp AS bigint), "
            "closed_at = CASE WHEN CAST(:close AS boolean) "
            "THEN COALESCE(closed_at, CAST(:now AS timestamptz)) ELSE NULL END, "
            "first_seen_at = LEAST("
            "first_seen_at, COALESCE(CAST(:fs AS timestamptz), first_seen_at)) "
            "WHERE id = :id"
        ),
        params,
    )
    await conn.execute(
        text(
            "UPDATE jobs SET freshness_at = "
            "LEAST(COALESCE(posted_at, first_seen_at), first_seen_at) "
            "WHERE id = ANY(:ids)"
        ).bindparams(_arr("ids", BigInteger)),
        {"ids": sorted(job_ids)},
    )
    return primaries


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def ingest_source(
    engine: AsyncEngine,
    source_id: int,
    adapter: Adapter,
    client: CrawlClient,
    ctx: NormContext,
    *,
    now: datetime | None = None,
    us_only: bool = True,
) -> IngestResult:
    now = now or datetime.now(UTC)
    t0 = time.monotonic()
    tier_of = _tier_lookup(adapter)

    # 1. load source + company, open the crawl_runs row (committed immediately)
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT s.id, s.company_id, s.adapter, s.token, s.config, s.last_crawled_at, "
                    "s.new_postings_ewma, s.consecutive_failures, c.name, c.domain, c.hq_country, "
                    "c.size_bucket FROM sources s JOIN companies c ON c.id = s.company_id "
                    "WHERE s.id = :id"
                ),
                {"id": source_id},
            )
        ).first()
        if row is None:
            raise LookupError(f"source {source_id} not found")
        run_id = (
            await conn.execute(
                crawl_runs.insert()
                .values(source_id=source_id, adapter=adapter.name, started_at=now)
                .returning(crawl_runs.c.id)
            )
        ).scalar_one()
    src = row
    ref = SourceRef(id=source_id, adapter=src.adapter, token=src.token, config=src.config or {})
    company = CompanyInfo(
        name=src.name, domain=src.domain, hq_country=src.hq_country, size_bucket=src.size_bucket
    )
    sinfo = SourceInfo(adapter=src.adapter, token=src.token)
    res = IngestResult(source_id=source_id, run_id=run_id, outcome="ok")

    async def fail(exc: BaseException) -> IngestResult:
        outcome, status = classify_failure(exc)
        res.outcome, res.error = outcome, f"{type(exc).__name__}: {exc}"[:2000]
        res.duration_ms = int((time.monotonic() - t0) * 1000)
        async with engine.begin() as c:
            await c.execute(
                crawl_runs.update()
                .where(crawl_runs.c.id == run_id)
                .values(
                    finished_at=now, outcome=outcome, complete=False, error=res.error,
                    http_status=status, http_requests=res.http_requests,
                    duration_ms=res.duration_ms, jobs_seen=0, jobs_new=0, jobs_updated=0,
                    jobs_closed=0,
                )
            )  # fmt: skip
            await c.execute(
                sources.update()
                .where(sources.c.id == source_id)
                .values(
                    last_crawled_at=now,
                    last_outcome=outcome,
                    consecutive_failures=sources.c.consecutive_failures + 1,
                    next_crawl_at=compute_next_crawl_at(src.new_postings_ewma, now),
                )
            )
        return res

    # 2. fetch + detail + normalize, no DB transaction held
    entries: list[_Entry] = []
    failed_ext: set[str] = set()
    try:
        with client.accounting() as acct:
            try:
                result = await adapter.fetch_jobs(ref, client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                res.http_requests = acct.requests
                return await fail(exc)

            async with engine.connect() as conn:
                existing = {
                    r.external_id: r
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT p.id, p.external_id, p.payload_hash, p.job_id, "
                                "p.closed_at, "
                                "j.dedupe_key, j.normalizer_version AS jver "
                                "FROM postings p LEFT JOIN jobs j ON j.id = p.job_id "
                                "WHERE p.source_id = :s"
                            ),
                            {"s": source_id},
                        )
                    ).all()
                }
                prev = (
                    await conn.execute(
                        text(
                            "SELECT outcome FROM crawl_runs WHERE source_id = :s AND id < :r "
                            "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
                        ),
                        {"s": source_id, "r": run_id},
                    )
                ).scalar_one_or_none()

            latest: dict[str, RawJob] = {j.external_id: j for j in result.jobs}
            for ext, raw in latest.items():
                h = payload_hash(raw.payload)
                ex = existing.get(ext)
                if ex is None:
                    kind = "new"
                elif bytes(ex.payload_hash) != h:
                    kind = "changed"
                elif ex.job_id is not None and ex.jver != NORMALIZER_VERSION:
                    kind = "refresh"
                else:
                    kind = "same"
                e = _Entry(raw=raw, hash=h, kind=kind, ex=ex)
                if kind != "same":
                    try:
                        if raw.needs_detail:
                            e.raw = await adapter.fetch_detail(ref, raw, client)
                            e.detail_at = now
                            res.detail_calls += 1
                        e.stored_payload = e.raw.payload
                        e.norm = normalize(e.raw, company, sinfo, ctx)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        log.warning("source %s job %s skipped: %r", source_id, ext, exc)
                        res.warnings.append(f"{ext}: {type(exc).__name__}: {exc}"[:300])
                        failed_ext.add(ext)
                        e.kind = "same" if ex is not None else "failed"
                        e.norm = None
                    if e.norm is not None:
                        e.eligible = True
                        unknown = False
                        if us_only:
                            e.eligible, unknown = is_us_eligible(e.norm)
                        if unknown:
                            _mark_unknown(e.norm, e.raw)
                entries.append(e)
        res.http_requests = acct.requests
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return await fail(exc)

    # 3. write transaction
    seen = set(latest)
    complete = result.complete and not failed_ext
    open_before = [r for r in existing.values() if r.closed_at is None]
    lost = [r for r in open_before if r.external_id not in seen]
    suspicious = False
    if result.complete and prev != "suspicious":
        if len(open_before) >= GUARD_MIN_OPEN_EMPTY and not result.jobs:
            suspicious = True
        elif (
            len(open_before) >= GUARD_MIN_OPEN_RATIO
            and len(lost) / len(open_before) > GUARD_LOSS_RATIO
        ):
            suspicious = True
    do_close = complete and not suspicious

    async with engine.begin() as conn:
        affected: set[int] = set()
        live = [e for e in entries if e.kind != "failed"]
        proc = [e for e in live if e.kind != "same"]
        same = [e for e in live if e.kind == "same"]

        # 3a. postings upsert
        for i in range(0, len(proc), CHUNK):
            chunk = proc[i : i + CHUNK]
            stmt = pg_insert(postings).values(
                [
                    {
                        "source_id": source_id,
                        "external_id": e.raw.external_id,
                        "url": e.raw.url,
                        "apply_url": e.raw.apply_url,
                        "payload": e.stored_payload if e.eligible else {},
                        "payload_hash": e.hash,
                        "detail_fetched_at": e.detail_at,
                        "first_seen_at": now,
                        "last_seen_at": now,
                        "missed_crawls": 0,
                        "closed_at": None,
                    }
                    for e in chunk
                ]
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["source_id", "external_id"],
                set_={
                    "url": stmt.excluded.url,
                    "apply_url": stmt.excluded.apply_url,
                    "payload": stmt.excluded.payload,
                    "payload_hash": stmt.excluded.payload_hash,
                    "detail_fetched_at": text(
                        "COALESCE(excluded.detail_fetched_at, postings.detail_fetched_at)"
                    ),
                    "last_seen_at": stmt.excluded.last_seen_at,
                    "missed_crawls": 0,
                    "closed_at": None,
                },
            ).returning(postings.c.id, postings.c.external_id)
            ids = {r.external_id: r.id for r in (await conn.execute(stmt)).all()}
            for e in chunk:
                e.posting_id = ids[e.raw.external_id]
        if same:
            await conn.execute(
                text(
                    "UPDATE postings SET last_seen_at = :now, missed_crawls = 0, closed_at = NULL "
                    "WHERE source_id = :s AND external_id = ANY(:ids)"
                ).bindparams(_arr("ids", Text)),
                {"now": now, "s": source_id, "ids": [e.raw.external_id for e in same]},
            )
        for e in same:
            if e.ex is not None:
                e.posting_id = e.ex.id
                e.job_id = e.ex.job_id

        # 3b. skipped (non-US) accounting
        res.skipped_non_us = sum(1 for e in proc if not e.eligible)

        # 3c. dedupe keys for eligible entries
        elig = [e for e in proc if e.eligible and e.norm is not None]
        proc_ids = {e.posting_id for e in proc}
        occupied = {
            r.dedupe_key
            for r in existing.values()
            if r.job_id is not None
            and r.dedupe_key
            and r.id not in proc_ids
            and (r.closed_at is None or r.external_id in seen)
        }
        groups: dict[str, list[_Entry]] = {}
        for e in elig:
            assert e.norm is not None
            groups.setdefault(base_dedupe_key(e.norm), []).append(e)
        for base, grp in groups.items():
            grp.sort(key=lambda e: e.raw.external_id)
            pending: list[_Entry] = []
            for e in grp:  # keep an existing key when it still belongs to this base
                cur = e.ex.dedupe_key if e.ex is not None else None
                if cur and cur.split("#")[0] == base and cur not in occupied:
                    e.key = cur
                    occupied.add(cur)
                else:
                    pending.append(e)
            for e in pending:
                n = 1
                while (k := base if n == 1 else f"{base}#{n}") in occupied:
                    n += 1
                e.key = k
                occupied.add(k)

        # 3d. jobs upsert / revive
        for e in elig:
            assert e.norm is not None and e.key is not None
            link = await find_explicit_link_job(conn, src.company_id, e.raw)
            if link is not None:
                e.job_id = link
                continue
            vals = _job_values(e.norm, e.raw)
            first = now
            fresh = min(e.norm.posted_at or first, first)
            stmt = (
                pg_insert(jobs)
                .values(
                    **vals,
                    company_id=src.company_id,
                    dedupe_key=e.key,
                    first_seen_at=first,
                    last_seen_at=now,
                    freshness_at=fresh,
                    closed_at=None,
                )  # fmt: skip
                .on_conflict_do_update(
                    index_elements=["company_id", "dedupe_key"],
                    set_={"closed_at": None, "last_seen_at": now},
                )
                .returning(jobs.c.id, literal_column("(xmax = 0)").label("inserted"))
            )
            jr = (await conn.execute(stmt)).one()
            e.job_id = jr.id
            res.jobs_created += 1 if jr.inserted else 0

        # 3e. link postings -> jobs (None detaches non-US / failed-to-qualify postings)
        for e in proc:
            if e.ex is not None and e.ex.job_id is not None and e.ex.job_id != e.job_id:
                affected.add(e.ex.job_id)
            if e.job_id is not None:
                affected.add(e.job_id)
        for e in same:
            if e.job_id is not None:
                affected.add(e.job_id)
        if proc:
            await conn.execute(
                text(
                    "UPDATE postings SET job_id = v.jid FROM "
                    "unnest(CAST(:pids AS bigint[]), CAST(:jids AS bigint[])) AS v(pid, jid) "
                    "WHERE postings.id = v.pid"
                ).bindparams(_arr("pids", BigInteger), _arr("jids", BigInteger)),
                {"pids": [e.posting_id for e in proc], "jids": [e.job_id for e in proc]},
            )

        # 3f. closing
        closed_ids: list[Any] = []
        if do_close:
            closed_ids = (
                await conn.execute(
                    text(
                        "UPDATE postings SET missed_crawls = missed_crawls + 1, "
                        "closed_at = CASE WHEN missed_crawls + 1 >= :n "
                        "THEN CAST(:now AS timestamptz) "
                        "ELSE NULL END "
                        "WHERE source_id = :s AND closed_at IS NULL AND external_id <> ALL(:seen) "
                        "RETURNING id, job_id, closed_at"
                    ).bindparams(_arr("seen", Text)),
                    {"n": MISSED_TO_CLOSE, "now": now, "s": source_id, "seen": sorted(seen)},
                )
            ).all()
            for r in closed_ids:
                if r.closed_at is not None:
                    res.postings_closed += 1
                    if r.job_id is not None:
                        affected.add(r.job_id)

        # 3g. refresh jobs; apply normalized fields where this posting is the primary
        orphans = (
            await conn.execute(
                text(
                    "SELECT id FROM jobs WHERE company_id = :c AND primary_posting_id IS NULL "
                    "AND closed_at IS NULL"
                ),
                {"c": src.company_id},
            )
        ).scalars()
        affected.update(orphans)
        before_closed = set()
        if affected:
            before_closed = set(
                (
                    await conn.execute(
                        text(
                            "SELECT id FROM jobs WHERE id = ANY(:ids) AND closed_at IS NOT NULL"
                        ).bindparams(_arr("ids", BigInteger)),
                        {"ids": sorted(affected)},
                    )
                ).scalars()
            )
        primaries = await refresh_jobs(conn, affected, now, tier_of)
        if affected:
            now_closed = set(
                (
                    await conn.execute(
                        text(
                            "SELECT id FROM jobs WHERE id = ANY(:ids) AND closed_at IS NOT NULL"
                        ).bindparams(_arr("ids", BigInteger)),
                        {"ids": sorted(affected)},
                    )
                ).scalars()
            )
            res.jobs_closed = len(now_closed - before_closed)
        for e in elig:
            if e.job_id is not None and primaries.get(e.job_id) == e.posting_id:
                assert e.norm is not None
                await conn.execute(
                    jobs.update().where(jobs.c.id == e.job_id).values(**_job_values(e.norm, e.raw))
                )
        seen_jobs = sorted({e.job_id for e in live if e.job_id is not None})
        if seen_jobs:
            await conn.execute(
                text("UPDATE jobs SET last_seen_at = :now WHERE id = ANY(:ids)").bindparams(
                    _arr("ids", BigInteger)
                ),
                {"now": now, "ids": seen_jobs},
            )

        # 3h. counts, crawl_runs, sources
        res.postings_new = sum(1 for e in proc if e.kind == "new")
        res.postings_updated = sum(1 for e in proc if e.kind == "changed")
        res.jobs_seen = len(latest)
        res.complete = complete
        res.outcome = "suspicious" if suspicious else ("ok" if complete else "partial")
        res.duration_ms = int((time.monotonic() - t0) * 1000)
        open_count = (
            await conn.execute(
                text("SELECT count(*) FROM postings WHERE source_id = :s AND closed_at IS NULL"),
                {"s": source_id},
            )
        ).scalar_one()

        ewma = float(src.new_postings_ewma or 0.0)
        if src.last_crawled_at is not None and not suspicious:
            hours = max((now - src.last_crawled_at).total_seconds() / 3600, 1.0)
            ewma = EWMA_ALPHA * (res.postings_new / hours * 24) + (1 - EWMA_ALPHA) * ewma
        upd: dict[str, Any] = {
            "last_crawled_at": now,
            "last_outcome": res.outcome,
            "open_postings": open_count,
            "new_postings_ewma": ewma,
            "next_crawl_at": now + SUSPICIOUS_RETRY
            if suspicious
            else compute_next_crawl_at(ewma, now),
        }
        if res.outcome in ("ok", "partial"):
            upd["consecutive_failures"] = 0
        if res.outcome == "ok":
            upd["last_success_at"] = now
        await conn.execute(sources.update().where(sources.c.id == source_id).values(**upd))
        await conn.execute(
            crawl_runs.update()
            .where(crawl_runs.c.id == run_id)
            .values(
                finished_at=now, outcome=res.outcome, complete=complete,
                http_requests=res.http_requests, jobs_seen=res.jobs_seen,
                jobs_new=res.postings_new, jobs_updated=res.postings_updated,
                jobs_closed=res.postings_closed, duration_ms=res.duration_ms,
                error="; ".join(res.warnings)[:2000] or None,
            )
        )  # fmt: skip
    return res


def _mark_unknown(n: NormalizedJob, raw: RawJob) -> None:
    if not n.locations:
        n.locations = [{"raw": "; ".join(raw.locations_raw), "unknown": True}]
    else:
        for loc in n.locations:
            loc["unknown"] = True


__all__ = [
    "IngestResult",
    "base_dedupe_key",
    "classify_failure",
    "compute_next_crawl_at",
    "find_explicit_link_job",
    "ingest_source",
    "is_us_eligible",
    "location_key",
    "payload_hash",
    "refresh_jobs",
]
