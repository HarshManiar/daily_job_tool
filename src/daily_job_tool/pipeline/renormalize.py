"""Offline re-normalization of stored payloads (no HTTP)."""

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from daily_job_tool.adapters import registry
from daily_job_tool.adapters.base import Adapter
from daily_job_tool.db.schema import jobs
from daily_job_tool.match.profile import Profile, get_profile
from daily_job_tool.normalize import (
    NORMALIZER_VERSION,
    CompanyInfo,
    NormContext,
    SourceInfo,
    normalize,
)
from daily_job_tool.pipeline.ingest import (
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


async def renormalize(
    engine: AsyncEngine,
    ctx: NormContext,
    *,
    only_outdated: bool = True,
    profile: Profile | None = None,
) -> int:
    """Re-run normalize over open postings that are the primary of their job. Returns jobs updated.

    Skips postings with an empty payload and postings whose adapter cannot rebuild a RawJob.
    Postings already skipped as non-US (job_id NULL, payload `{"_skipped_v": N}`) are not
    visited: there is no raw job here to re-decide them with, so the next crawl re-evaluates
    them. A job that turns out non-US is detached from its posting (payload marker) and closed,
    exactly as in ingest. A changed dedupe key is applied in place (same job id).
    Bumps jobs.normalizer_version to the current version.
    """
    registry._load()  # noqa: SLF001
    profile = profile or get_profile()
    cache: dict[str, Adapter] = {}
    updated = 0
    now = datetime.now(UTC)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT j.id AS job_id, j.company_id, j.dedupe_key, p.id AS posting_id, "
                    "p.payload, s.adapter, s.token, "
                    "c.name, c.domain, c.hq_country, c.size_bucket, "
                    "c.industry, c.is_staffing, c.exclude_reason "
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
                name=r.name,
                domain=r.domain,
                hq_country=r.hq_country,
                size_bucket=r.size_bucket,
                industry=r.industry,
                is_staffing=r.is_staffing,
                exclude_reason=r.exclude_reason,
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
            values = _job_values(n, raw, company, profile)
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


async def rescore(
    engine: AsyncEngine, profile: Profile | None = None, *, company_id: int | None = None
) -> int:
    """Recompute match columns for open jobs (all, or one company's) without HTTP or re-parsing.

    The description-derived columns (visa, years, employment, norm_flags) are re-run on the stored
    description first, so edits to the profile's visa phrases and rule changes take effect, and
    the company row is re-read so company edits (exclude_reason, is_staffing, industry, name)
    apply. Employment: a stored 'full_time' is kept as the ATS value (the raw ATS string is not
    stored); contract/temporary/unknown are re-inferred from title and description.
    Run `daily_job_tool match rescore` after upgrading (alembic never does it).
    """
    async with engine.begin() as conn:
        return await rescore_conn(conn, profile, company_id=company_id)


async def rescore_conn(
    conn: AsyncConnection, profile: Profile | None = None, *, company_id: int | None = None
) -> int:
    from daily_job_tool.match.engine import match as run_match
    from daily_job_tool.normalize import text_signals
    from daily_job_tool.normalize.models import NormalizedJob
    from daily_job_tool.normalize.text import html_to_lines

    profile = profile or get_profile()
    fields = [f for f in NormalizedJob.model_fields if f not in {"normalizer_version"}]
    cols = ", ".join(f"j.{f}" for f in fields)
    rows = (
        (
            await conn.execute(
                text(
                    f"SELECT j.id AS job_id, {cols}, c.name AS c_name, "  # noqa: S608
                    "c.industry AS c_industry, "
                    "c.is_staffing AS c_staffing, c.exclude_reason AS c_excl "
                    "FROM jobs j JOIN companies c ON c.id = j.company_id "
                    "WHERE j.closed_at IS NULL AND (CAST(:cid AS bigint) IS NULL "
                    "OR j.company_id = CAST(:cid AS bigint))"
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .all()
    )
    phrases = profile.visa_phrases()
    for r in rows:
        data = {f: r[f] for f in fields}
        lines = html_to_lines(data["description_html"]) if data["description_html"] else None
        dtext = data["description_text"]
        stored_ft = data["employment_type"] == "full_time"
        data.update(
            text_signals(
                data["title"], "full_time" if stored_ft else None, lines or dtext, dtext, phrases
            )
        )
        n = NormalizedJob(**data)
        co = CompanyInfo(
            name=r["c_name"],
            industry=r["c_industry"],
            is_staffing=r["c_staffing"],
            exclude_reason=r["c_excl"],
        )
        m = run_match(n, co, profile)
        await conn.execute(
            jobs.update()
            .where(jobs.c.id == r["job_id"])
            .values(
                employment_type=n.employment_type,
                years_min=n.years_min,
                years_max=n.years_max,
                visa_status=n.visa_status,
                visa_evidence=n.visa_evidence,
                norm_flags=n.norm_flags,
                match_tier=m.tier,
                match_status=m.status,
                match_reasons=m.reasons,
                industry_rank=m.industry_rank,
                location_rank=m.location_rank,
                fit_score=m.fit_score,
            )
        )
    return len(rows)


__all__ = ["raw_from_payload", "renormalize", "rescore", "rescore_conn"]
