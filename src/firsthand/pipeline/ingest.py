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
from collections.abc import Callable, Mapping
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
REOPEN_WINDOW = timedelta(days=14)
DETAIL_FAIL_MIN = 3
DETAIL_FAIL_RATIO = 0.10


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
    detail_failures: int = 0
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
    def tier_of(name: str) -> int:
        return adapter.tier if name == adapter.name else registry.get_tier(name)

    return tier_of


def _resolver(adapter: Adapter) -> Callable[[str], Adapter | None]:
    def resolve(name: str) -> Adapter | None:
        if name == adapter.name:
            return adapter
        try:
            return registry.get_adapter(name)
        except KeyError:
            return None

    return resolve


def raw_from_payload(adapter: Adapter, payload: dict[str, Any]) -> RawJob | None:
    """Rebuild a RawJob from a stored payload via the adapter's `_map` (all Phase 1 adapters)."""
    mapper = getattr(adapter, "_map", None)
    if mapper is None or not payload or "_skipped_v" in payload:
        return None
    try:
        raw = mapper(payload)
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    return raw.model_copy(update={"needs_detail": False})


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
    relisted: bool = False
    posting_id: int | None = None
    job_id: int | None = None
    key: str | None = None


def _job_values(n: NormalizedJob, raw: RawJob) -> dict[str, Any]:
    v = n.model_dump()
    v["apply_url"] = n.apply_url or raw.apply_url or raw.url
    v["normalizer_version"] = NORMALIZER_VERSION
    return v


def _arr(name: str, typ: Any) -> Any:
    return bindparam(name, type_=ARRAY(typ))


# ---------------------------------------------------------------------------
# Job refresh (primary posting, closed state, first_seen/freshness)
# ---------------------------------------------------------------------------


async def refresh_jobs(
    conn: AsyncConnection,
    job_ids: set[int],
    now: datetime,
    tier_of,
    changed: dict[int, int] | None = None,
) -> dict[int, int | None]:
    """Recompute primary posting, closed_at, first_seen_at, freshness_at. Returns job -> primary.

    Job rows are locked first (ordered by id, so concurrent crawls cannot deadlock here) and the
    postings are read afterwards, so a concurrent crawl's committed changes are visible.
    `changed` (out-param) receives job -> new primary for jobs whose primary posting moved.
    """
    if not job_ids:
        return {}
    ids = sorted(job_ids)
    old_primary = {
        r.id: r.primary_posting_id
        for r in (
            await conn.execute(
                text(
                    "SELECT id, primary_posting_id FROM jobs WHERE id = ANY(:ids) "
                    "ORDER BY id FOR UPDATE"
                ).bindparams(_arr("ids", BigInteger)),
                {"ids": ids},
            )
        ).all()
    }
    rows = (
        await conn.execute(
            text(
                "SELECT p.id, p.job_id, p.closed_at, p.first_seen_at, s.adapter "
                "FROM postings p JOIN sources s ON s.id = p.source_id WHERE p.job_id = ANY(:ids)"
            ).bindparams(_arr("ids", BigInteger)),
            {"ids": ids},
        )
    ).all()
    by_job: dict[int, list[Any]] = {}
    for r in rows:
        by_job.setdefault(r.job_id, []).append(r)
    params: list[dict[str, Any]] = []
    primaries: dict[int, int | None] = {}
    for jid in ids:
        ps = by_job.get(jid, [])
        open_ = [p for p in ps if p.closed_at is None]
        pool = open_ or ps
        best = (
            min(pool, key=lambda p: (tier_of(p.adapter), p.first_seen_at, p.id)) if pool else None
        )
        primaries[jid] = best.id if best else None
        if changed is not None and best is not None and old_primary.get(jid) != best.id:
            changed[jid] = best.id
        params.append(
            {
                "id": jid,
                "pp": best.id if best else None,
                "fs": min(p.first_seen_at for p in ps) if ps else None,
                "now": now,
            }
        )
    await conn.execute(
        text(
            "UPDATE jobs SET primary_posting_id = CAST(:pp AS bigint), "
            "closed_at = CASE WHEN NOT EXISTS ("
            "SELECT 1 FROM postings WHERE job_id = jobs.id AND closed_at IS NULL) "
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
        {"ids": ids},
    )
    return primaries


async def rebuild_job_from_posting(
    conn: AsyncConnection,
    job_id: int,
    posting_id: int,
    ctx: NormContext,
    resolve: Callable[[str], Adapter | None],
) -> bool:
    """Rewrite a job's normalized fields from a posting's stored payload (no HTTP)."""
    r = (
        await conn.execute(
            text(
                "SELECT p.payload, s.adapter, s.token, c.name, c.domain, c.hq_country, "
                "c.size_bucket FROM postings p JOIN sources s ON s.id = p.source_id "
                "JOIN jobs j ON j.id = :j JOIN companies c ON c.id = j.company_id "
                "WHERE p.id = :p"
            ),
            {"j": job_id, "p": posting_id},
        )
    ).first()
    if r is None:
        return False
    adapter = resolve(r.adapter)
    raw = raw_from_payload(adapter, r.payload) if adapter is not None else None
    if raw is None:
        return False
    company = CompanyInfo(
        name=r.name, domain=r.domain, hq_country=r.hq_country, size_bucket=r.size_bucket
    )
    try:
        n = normalize(raw, company, SourceInfo(adapter=r.adapter, token=r.token), ctx)
    except Exception as exc:  # noqa: BLE001
        log.warning("rebuild job %s from posting %s failed: %r", job_id, posting_id, exc)
        return False
    if is_us_eligible(n)[1]:
        _mark_unknown(n, raw)
    await conn.execute(jobs.update().where(jobs.c.id == job_id).values(**_job_values(n, raw)))
    return True


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
                    "s.last_success_at, "
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

    # TODO(1.8): backoff/breaker on failures lives in scheduler.apply_breaker; the backoff here
    # is only the EWMA-based next_crawl_at.
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

    async def build(e: _Entry) -> None:
        """Fill e.raw / e.stored_payload / e.norm, using stored payloads where possible."""
        raw, ex = e.raw, e.ex
        if e.kind in ("reopen", "refresh") and ex is not None and ex.job_id is not None:
            rb = raw_from_payload(adapter, ex.payload)
            if rb is not None:  # hash unchanged: no HTTP needed
                e.raw, e.stored_payload = rb, dict(ex.payload)
                e.norm = normalize(rb, company, sinfo, ctx)
                return
        if e.kind in ("reopen", "refresh") and ex is not None and ex.job_id is None:
            # previously skipped as non-US: re-evaluate; detail only if now eligible
            n0 = normalize(raw, company, sinfo, ctx)
            if not raw.needs_detail or (us_only and not is_us_eligible(n0)[0]):
                e.stored_payload, e.norm = raw.payload, n0
                return
        if raw.needs_detail:
            e.raw = await adapter.fetch_detail(ref, raw, client)
            e.detail_at = now
            res.detail_calls += 1
        e.stored_payload = e.raw.payload
        e.norm = normalize(e.raw, company, sinfo, ctx)

    try:
        with client.accounting() as acct:
            try:
                result = await adapter.fetch_jobs(ref, client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                res.http_requests = acct.requests
                return await fail(exc)

            fetched = sorted({j.external_id for j in result.jobs})
            async with engine.connect() as conn:
                existing = {
                    r.external_id: r
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT p.id, p.external_id, p.payload_hash, p.payload, p.job_id, "
                                "p.closed_at, p.missed_crawls, "
                                "j.dedupe_key, j.normalizer_version AS jver "
                                "FROM postings p LEFT JOIN jobs j ON j.id = p.job_id "
                                "WHERE p.source_id = :s "
                                "AND (p.closed_at IS NULL OR p.external_id = ANY(:ext))"
                            ).bindparams(_arr("ext", Text)),
                            {"s": source_id, "ext": fetched},
                        )
                    ).all()
                }
                last_guard = (
                    await conn.execute(
                        text(
                            "SELECT outcome FROM crawl_runs WHERE source_id = :s AND id < :r "
                            "AND outcome IN ('ok', 'suspicious') ORDER BY id DESC LIMIT 1"
                        ),
                        {"s": source_id, "r": run_id},
                    )
                ).scalar_one_or_none()

            latest: dict[str, RawJob] = {j.external_id: j for j in result.jobs}
            relist_cut = now - REOPEN_WINDOW
            for ext, raw in latest.items():
                h = payload_hash(raw.payload)
                ex = existing.get(ext)
                if ex is None:
                    kind = "new"
                elif bytes(ex.payload_hash) != h:
                    kind = "changed"
                elif ex.closed_at is not None:
                    kind = "reopen"
                elif ex.job_id is not None and ex.jver != NORMALIZER_VERSION:
                    kind = "refresh"
                elif (
                    ex.job_id is None and (ex.payload or {}).get("_skipped_v") != NORMALIZER_VERSION
                ):
                    kind = "refresh"  # non-US decision made by an older normalizer: re-decide
                else:
                    kind = "same"
                e = _Entry(raw=raw, hash=h, kind=kind, ex=ex)
                e.relisted = bool(ex is not None and ex.closed_at and ex.closed_at < relist_cut)
                if kind != "same":
                    try:
                        await build(e)
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
    res.detail_failures = len(failed_ext)
    # A few permanently failing detail calls must not stop closing (their postings count as
    # seen); only a large failure share makes the crawl partial.
    too_many_failures = res.detail_failures > max(DETAIL_FAIL_MIN, DETAIL_FAIL_RATIO * len(latest))
    complete = result.complete and not too_many_failures
    open_before = [r for r in existing.values() if r.closed_at is None]
    lost = [r for r in open_before if r.external_id not in seen]
    # The guard fires at most once per episode: not again while a suspicious run is the latest
    # confirmed verdict, nor once misses have already been recorded by a confirmed run.
    guard_armed = last_guard != "suspicious" and not any(r.missed_crawls >= 1 for r in open_before)
    suspicious = False
    if complete and guard_armed:
        if len(open_before) >= GUARD_MIN_OPEN_EMPTY and not result.jobs:
            suspicious = True
        elif (
            len(open_before) >= GUARD_MIN_OPEN_RATIO
            and len(lost) / len(open_before) > GUARD_LOSS_RATIO
        ):
            suspicious = True
    do_close = complete and not suspicious

    try:
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
                            "payload": e.stored_payload
                            if e.eligible
                            else {"_skipped_v": NORMALIZER_VERSION},
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
                        "first_seen_at": text(
                            "CASE WHEN postings.closed_at < CAST(:relist_cut AS timestamptz) "
                            "THEN excluded.first_seen_at ELSE postings.first_seen_at END"
                        ).bindparams(relist_cut=now - REOPEN_WINDOW),
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
                        "UPDATE postings SET last_seen_at = :now, missed_crawls = 0, "
                        "closed_at = NULL WHERE source_id = :s AND external_id = ANY(:ids)"
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
                cur_key = e.ex.dedupe_key if e.ex is not None else None
                if e.ex is not None and e.ex.job_id is not None and cur_key and e.key != cur_key:
                    # re-key in place (keeps job id / first_seen_at) when this posting is the
                    # job's only open posting and the new key is free
                    moved = (
                        await conn.execute(
                            text(
                                "UPDATE jobs SET dedupe_key = :k WHERE id = :j "
                                "AND NOT EXISTS (SELECT 1 FROM jobs WHERE company_id = :c "
                                "AND dedupe_key = :k) "
                                "AND NOT EXISTS (SELECT 1 FROM postings WHERE job_id = :j "
                                "AND id <> :p AND closed_at IS NULL) RETURNING id"
                            ),
                            {"k": e.key, "j": e.ex.job_id, "c": src.company_id, "p": e.posting_id},
                        )
                    ).first()
                    if moved is not None:
                        e.job_id = e.ex.job_id
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
                            "WHERE source_id = :s AND closed_at IS NULL "
                            "AND external_id <> ALL(:seen) "
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
            changed: dict[int, int] = {}
            primaries = await refresh_jobs(conn, affected, now, tier_of, changed)
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
            applied: set[int] = set()
            for e in elig:
                if e.job_id is not None and primaries.get(e.job_id) == e.posting_id:
                    assert e.norm is not None
                    await conn.execute(
                        jobs.update()
                        .where(jobs.c.id == e.job_id)
                        .values(**_job_values(e.norm, e.raw))
                    )
                    applied.add(e.job_id)
            resolve = _resolver(adapter)
            for jid, pid in changed.items():  # primary moved to a posting not processed now
                if jid not in applied:
                    await rebuild_job_from_posting(conn, jid, pid, ctx, resolve)
            seen_jobs = sorted({e.job_id for e in live if e.job_id is not None})
            if seen_jobs:
                await conn.execute(
                    text("UPDATE jobs SET last_seen_at = :now WHERE id = ANY(:ids)").bindparams(
                        _arr("ids", BigInteger)
                    ),
                    {"now": now, "ids": seen_jobs},
                )

            # 3h. counts, crawl_runs, sources
            res.postings_new = sum(1 for e in proc if e.kind == "new" or e.relisted)
            res.postings_updated = sum(1 for e in proc if e.kind == "changed" and not e.relisted)
            res.jobs_seen = len(latest)
            res.complete = complete
            res.outcome = "suspicious" if suspicious else ("ok" if complete else "partial")
            res.detail_failures = len(failed_ext)
            res.duration_ms = int((time.monotonic() - t0) * 1000)
            open_count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM postings WHERE source_id = :s AND closed_at IS NULL"
                    ),
                    {"s": source_id},
                )
            ).scalar_one()

            ewma = float(src.new_postings_ewma or 0.0)
            if src.last_success_at is not None and res.outcome == "ok":
                # time since the last GOOD crawl, so failed/partial runs do not inflate the rate
                hours = max((now - src.last_success_at).total_seconds() / 3600, 1.0)
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
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("source %s write phase failed", source_id)
        for f in (
            "jobs_seen", "postings_new", "postings_updated", "postings_closed", "jobs_created",
            "jobs_closed", "skipped_non_us",
        ):  # fmt: skip
            setattr(res, f, 0)
        return await fail(exc)
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
