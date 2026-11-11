"""Offline re-normalization of stored payloads (no HTTP)."""

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from firsthand.adapters import registry
from firsthand.adapters.base import Adapter
from firsthand.db.schema import jobs
from firsthand.normalize import NORMALIZER_VERSION, CompanyInfo, NormContext, SourceInfo, normalize
from firsthand.pipeline.ingest import (
    _job_values,
    _mark_unknown,
    base_dedupe_key,
    is_us_eligible,
    raw_from_payload,
    rebuild_job_from_posting,
    refresh_jobs,
)

log = logging.getLogger(__name__)


async def _free_key(conn, company_id: int, job_id: int, base: str) -> str:
    taken = set(
        (
            await conn.execute(
                text(
                    "SELECT dedupe_key FROM jobs WHERE company_id = :c AND id <> :j "
                    "AND (dedupe_key = :b OR dedupe_key LIKE :b || '#%')"
                ),
                {"c": company_id, "j": job_id, "b": base},
            )
        ).scalars()
    )
    n = 1
    while (k := base if n == 1 else f"{base}#{n}") in taken:
        n += 1
    return k


async def renormalize(engine: AsyncEngine, ctx: NormContext, *, only_outdated: bool = True) -> int:
    """Re-run normalize over open postings that are the primary of their job. Returns jobs updated.

    Skips postings with an empty payload and postings whose adapter cannot rebuild a RawJob.
    Postings already skipped as non-US (job_id NULL, payload `{"_skipped_v": N}`) are not
    visited: there is no raw job here to re-decide them with, so the next crawl re-evaluates
    them. A job that turns out non-US is detached from its posting (payload marker) and closed,
    exactly as in ingest. A changed dedupe key is applied in place (same job id).
    Bumps jobs.normalizer_version to the current version.
    """
    registry._load()  # noqa: SLF001
    cache: dict[str, Adapter] = {}
    updated = 0
    now = datetime.now(UTC)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT j.id AS job_id, j.company_id, j.dedupe_key, p.id AS posting_id, "
                    "p.payload, s.adapter, s.token, "
                    "c.name, c.domain, c.hq_country, c.size_bucket "
                    "FROM jobs j JOIN postings p ON p.id = j.primary_posting_id "
                    "JOIN sources s ON s.id = p.source_id JOIN companies c ON c.id = j.company_id "
                    "WHERE j.closed_at IS NULL AND p.closed_at IS NULL "
                    "AND p.payload <> '{}'::jsonb AND NOT p.payload ? '_skipped_v' "
                    "AND (:all OR j.normalizer_version <> :v) ORDER BY j.id"
                ),
                {"all": not only_outdated, "v": NORMALIZER_VERSION},
            )
        ).all()

    def resolve(name: str) -> Adapter | None:
        if name not in cache:
            try:
                cache[name] = registry.get_adapter(name)
            except KeyError:
                return None
        return cache[name]

    async with engine.begin() as conn:
        affected: set[int] = set()
        for r in rows:
            adapter = resolve(r.adapter)
            if adapter is None:
                continue
            raw = raw_from_payload(adapter, r.payload)
            if raw is None:
                continue
            company = CompanyInfo(
                name=r.name, domain=r.domain, hq_country=r.hq_country, size_bucket=r.size_bucket
            )
            try:
                n = normalize(raw, company, SourceInfo(adapter=r.adapter, token=r.token), ctx)
            except Exception as exc:  # noqa: BLE001
                log.warning("renormalize job %s failed: %r", r.job_id, exc)
                continue
            eligible, unknown = is_us_eligible(n)
            if not eligible:
                # same as ingest: detach the posting (marker keeps the decision versioned) and
                # let refresh_jobs close the job if nothing else keeps it open
                await conn.execute(
                    text(
                        "UPDATE postings SET job_id = NULL, "
                        "payload = CAST(:pl AS jsonb) WHERE id = :p"
                    ),
                    {"pl": f'{{"_skipped_v": {NORMALIZER_VERSION}}}', "p": r.posting_id},
                )
                affected.add(r.job_id)
                continue
            if unknown:
                _mark_unknown(n, raw)
            values = _job_values(n, raw)
            base = base_dedupe_key(n)
            if r.dedupe_key.split("#")[0] != base:
                values["dedupe_key"] = await _free_key(conn, r.company_id, r.job_id, base)
            await conn.execute(jobs.update().where(jobs.c.id == r.job_id).values(**values))
            updated += 1
        if affected:
            changed: dict[int, int] = {}
            await refresh_jobs(conn, affected, now, registry.get_tier, changed)
            for jid, pid in changed.items():
                if await rebuild_job_from_posting(conn, jid, pid, ctx, resolve):
                    updated += 1
    return updated


__all__ = ["raw_from_payload", "renormalize"]
