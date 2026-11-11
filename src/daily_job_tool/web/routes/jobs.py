from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from daily_job_tool.web import queries
from daily_job_tool.web.deps import Conn, templates

router = APIRouter()


@router.get("/jobs/{job_id}")
async def job_detail(request: Request, job_id: int, conn: Conn):
    if job_id > 2**62:
        raise HTTPException(404)
    job = await queries.fetch_job(conn, job_id)
    if job is None:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request, "job.html", {"page": "", "j": job, "now": job["_now"]}
    )
