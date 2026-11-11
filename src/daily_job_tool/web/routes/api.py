from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text

from daily_job_tool.web import format as fmt
from daily_job_tool.web import queries
from daily_job_tool.web.deps import Conn
from daily_job_tool.web.queries import FeedFilters

router = APIRouter(prefix="/api/v1")


@router.get("/jobs")
async def api_jobs(f: Annotated[FeedFilters, Query()], conn: Conn):
    rows, has_more, total, _ = await queries.fetch_feed(conn, f)
    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "company": r["company_name"],
            "company_slug": r["company_slug"],
            "location": fmt.location_text(r["locations"], r["remote_type"]),
            "remote_type": r["remote_type"],
            "posted_at": r["posted_at"],
            "freshness_at": r["freshness_at"],
            "salary_min_usd": r["salary_min_usd"],
            "salary_max_usd": r["salary_max_usd"],
            "match_tier": r["match_tier"],
            "match_status": r["match_status"],
            "match_reasons": r["match_reasons"],
            "visa_status": r["visa_status"],
            "years_min": r["years_min"],
            "years_max": r["years_max"],
            "fit_score": r["fit_score"],
            "apply_url": fmt.safe_http_url(r["apply_url"]),
        }
        for r in rows
    ]
    return JSONResponse(
        jsonable_encoder(
            {
                "total": total,
                "limit": f.limit,
                "offset": f.offset,
                "has_more": has_more,
                "items": items,
            }  # fmt: skip
        )
    )


@router.get("/health")
async def api_health(conn: Conn):
    r = (
        await conn.execute(
            text(
                "SELECT (SELECT count(*) FROM jobs WHERE closed_at IS NULL) AS open_jobs,"
                " (SELECT count(*) FROM sources WHERE status = 'active') AS active_sources,"
                " (SELECT max(started_at) FROM crawl_runs) AS last_crawl"
            )
        )
    ).one()
    return JSONResponse(
        jsonable_encoder(
            {
                "status": "ok",
                "open_jobs": r.open_jobs,
                "active_sources": r.active_sources,
                "last_crawl": r.last_crawl,
            }  # fmt: skip
        )
    )
