"""List sources marked 'blocked' so the owner can review them manually."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass
class BlockedSource:
    source_id: int
    company: str
    adapter: str
    token: str
    last_error: str | None
    last_crawled_at: datetime | None


async def list_blocked(engine: AsyncEngine) -> list[BlockedSource]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT s.id, c.name, s.adapter, s.token, s.last_crawled_at, "
                    "(SELECT r.error FROM crawl_runs r WHERE r.source_id = s.id "
                    " AND r.error IS NOT NULL ORDER BY r.id DESC LIMIT 1) AS err "
                    "FROM sources s JOIN companies c ON c.id = s.company_id "
                    "WHERE s.status = 'blocked' ORDER BY s.last_crawled_at DESC NULLS LAST, s.id"
                )
            )
        ).all()
    return [BlockedSource(r.id, r.name, r.adapter, r.token, r.err, r.last_crawled_at) for r in rows]
