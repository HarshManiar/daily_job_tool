from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from firsthand.web import queries
from firsthand.web.deps import Conn, templates

router = APIRouter()


@router.get("/companies/{slug}")
async def company(request: Request, slug: str, conn: Conn):
    co = await queries.fetch_company(conn, slug)
    if co is None:
        raise HTTPException(404)
    rows, now = await queries.company_jobs(conn, co["id"], excluded=False)
    ex_rows, _ = await queries.company_jobs(conn, co["id"], excluded=True)
    activity = await queries.company_activity(conn, co["id"])
    return templates.TemplateResponse(
        request,
        "company.html",
        {"page": "", "co": co, "rows": rows, "ex_rows": ex_rows, "now": now, "act": activity},
    )
