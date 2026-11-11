"""Offline re-normalization of stored payloads (no HTTP)."""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from firsthand.adapters import registry
from firsthand.adapters.base import Adapter, RawJob
from firsthand.db.schema import jobs
from firsthand.normalize import NORMALIZER_VERSION, CompanyInfo, NormContext, SourceInfo, normalize
from firsthand.pipeline.ingest import _job_values, is_us_eligible

log = logging.getLogger(__name__)


def raw_from_payload(adapter: Adapter, payload: dict[str, Any]) -> RawJob | None:
    """Rebuild a RawJob from a stored payload via the adapter's `_map` (all Phase 1 adapters)."""
    mapper = getattr(adapter, "_map", None)
    if mapper is None or not payload:
        return None
    raw = mapper(payload)
    if raw is None:
        return None
    return raw.model_copy(update={"needs_detail": False})


async def renormalize(engine: AsyncEngine, ctx: NormContext, *, only_outdated: bool = True) -> int:
    """Re-run normalize over open postings that are the primary of their job. Returns jobs updated.

    Skips postings with an empty payload (non-US) and postings whose adapter cannot rebuild a
    RawJob. Bumps jobs.normalizer_version to the current version.
    """
    registry._load()  # noqa: SLF001
    cache: dict[str, Adapter] = {}
    updated = 0
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT j.id AS job_id, p.id AS posting_id, p.payload, s.adapter, s.token, "
                    "c.name, c.domain, c.hq_country, c.size_bucket "
                    "FROM jobs j JOIN postings p ON p.id = j.primary_posting_id "
                    "JOIN sources s ON s.id = p.source_id JOIN companies c ON c.id = j.company_id "
                    "WHERE j.closed_at IS NULL AND p.closed_at IS NULL "
                    "AND p.payload <> '{}'::jsonb "
                    "AND (:all OR j.normalizer_version <> :v) ORDER BY j.id"
                ),
                {"all": not only_outdated, "v": NORMALIZER_VERSION},
            )
        ).all()
    async with engine.begin() as conn:
        for r in rows:
            adapter = cache.get(r.adapter)
            if adapter is None:
                try:
                    adapter = cache[r.adapter] = registry.get_adapter(r.adapter)
                except KeyError:
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
                continue  # never silently rewrite a job into a non-US shape; leave as is
            if unknown:
                from firsthand.pipeline.ingest import _mark_unknown

                _mark_unknown(n, raw)
            await conn.execute(
                jobs.update().where(jobs.c.id == r.job_id).values(**_job_values(n, raw))
            )
            updated += 1
    return updated
