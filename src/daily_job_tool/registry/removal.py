"""Honor an employer's removal request: stop crawling, hide jobs, block re-seeding.

`remove_company` runs in ONE transaction and is idempotent. The domain goes into
`blocked_domains` (reason 'removal_request'), which the importer and the crawler's host
blocklist both consult, so re-importing a seed row can never bring the company back.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from daily_job_tool.registry.importer import normalize_domain, slugify

BLOCK_REASON = "removal_request"


class CompanyNotFound(LookupError):
    pass


@dataclass
class CompanyInfo:
    id: int
    name: str
    slug: str
    domain: str | None
    status: str
    sources: int
    open_jobs: int


@dataclass
class RemovalResult:
    company: str
    domain: str | None
    already_removed: bool
    sources_removed: int
    postings_closed: int
    jobs_closed: int


async def find_company(conn: AsyncConnection, key: str) -> CompanyInfo | None:
    """Look up by domain/URL (registrable domain) or by exact company name / slug."""
    key = (key or "").strip()
    if not key:
        return None
    sel = (
        "SELECT c.id, c.name, c.slug::text AS slug, c.domain::text AS domain, "
        "c.status::text AS status, "
        "(SELECT count(*) FROM sources s WHERE s.company_id = c.id AND s.status <> 'removed') "
        "AS sources, "
        "(SELECT count(*) FROM jobs j WHERE j.company_id = c.id AND j.closed_at IS NULL) "
        "AS open_jobs FROM companies c WHERE "
    )
    row = None
    dom = normalize_domain(key) if "." in key else None
    if dom:
        row = (await conn.execute(text(sel + "c.domain = :d"), {"d": dom})).first()
    if row is None:
        row = (
            await conn.execute(
                text(sel + "lower(c.name) = lower(:n) OR c.slug = :s ORDER BY c.id LIMIT 1"),
                {"n": key, "s": slugify(key) or key},
            )
        ).first()
    if row is None:
        return None
    return CompanyInfo(
        row.id, row.name, row.slug, row.domain, row.status, row.sources, row.open_jobs
    )


async def remove_company_conn(
    conn: AsyncConnection, key: str, reason: str, requester: str | None = None
) -> RemovalResult:
    info = await find_company(conn, key)
    if info is None:
        raise CompanyNotFound(key)
    reason = " ".join((reason or "").split()) or "not given"
    already = info.status == "removed"
    n_src = n_post = n_jobs = 0
    if not already:
        await conn.execute(
            text(
                "UPDATE companies SET status = 'removed', "
                "exclude_reason = :r, updated_at = now() WHERE id = :id"
            ),
            {"r": f"removal request: {reason}", "id": info.id},
        )
    # always (re)apply the closing steps so a half-finished earlier state is healed
    n_src = (
        await conn.execute(
            text(
                "UPDATE sources SET status = 'removed', locked_until = NULL "
                "WHERE company_id = :id AND status <> 'removed'"
            ),
            {"id": info.id},
        )
    ).rowcount
    n_post = (
        await conn.execute(
            text(
                "UPDATE postings SET closed_at = now() WHERE closed_at IS NULL AND source_id IN "
                "(SELECT id FROM sources WHERE company_id = :id)"
            ),
            {"id": info.id},
        )
    ).rowcount
    n_jobs = (
        await conn.execute(
            text("UPDATE jobs SET closed_at = now() WHERE company_id = :id AND closed_at IS NULL"),
            {"id": info.id},
        )
    ).rowcount
    if info.domain:
        await conn.execute(
            text(
                "INSERT INTO blocked_domains (domain, reason) VALUES (:d, :r) "
                "ON CONFLICT (domain) DO NOTHING"
            ),
            {"d": info.domain, "r": BLOCK_REASON},
        )
    if not already:
        await conn.execute(
            text(
                "INSERT INTO removal_requests (company_domain, requester_email, message, "
                "verify_token, verified_at, resolved_at, resolution) "
                "VALUES (:d, :e, :m, 'owner-initiated', now(), now(), 'removed')"
            ),
            {"d": info.domain or info.slug, "e": requester or "owner (local)", "m": reason},
        )
    return RemovalResult(info.name, info.domain, already, n_src, n_post, n_jobs)


async def remove_company(
    engine: AsyncEngine, domain_or_name: str, reason: str, requester: str | None = None
) -> RemovalResult:
    async with engine.begin() as conn:
        return await remove_company_conn(conn, domain_or_name, reason, requester)
