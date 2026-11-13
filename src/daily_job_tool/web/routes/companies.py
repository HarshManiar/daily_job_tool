from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from daily_job_tool.web import queries
from daily_job_tool.web.deps import Conn, templates
from daily_job_tool.web.format import trend_chart

router = APIRouter()


@router.get("/companies/search")
async def company_search(
    request: Request,
    conn: Conn,
    q: Annotated[str, Query(max_length=100)] = "",
    companies: Annotated[list[int], Query(max_length=queries.MAX_COMPANIES)] = [],  # noqa: B006
):
    """Typeahead for the Company filter: HTML options for HTMX, JSON otherwise."""
    items = await queries.search_companies(conn, q, companies) if q.strip() else []
    if request.headers.get("hx-request") == "true":
        return templates.TemplateResponse(
            request,
            "_company_options.html",
            {"items": items, "q": q.strip(), "full": len(companies) >= queries.MAX_COMPANIES},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})


@router.get("/companies/{slug}")
async def company(request: Request, slug: str, conn: Conn):
    co = await queries.fetch_company(conn, slug)
    if co is None:
        raise HTTPException(404)
    rows, now = await queries.company_jobs(conn, co["id"], excluded=False)
    ex_rows, _ = await queries.company_jobs(conn, co["id"], excluded=True)
    activity = await queries.company_activity(conn, co["id"])
    trend = await queries.company_trend(conn, co["id"])
    tiers = await queries.company_tier_counts(conn, co["id"])
    return templates.TemplateResponse(
        request,
        "company.html",
        {
            "page": "",
            "co": co,
            "rows": rows,
            "ex_rows": ex_rows,
            "now": now,
            "act": activity,
            "trend": trend,
            "tiers": tiers,
            "chart": trend_chart(trend),
        },
    )
