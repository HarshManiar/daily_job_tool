"""Job detail (full page or HTMX partial) and the per-job state writers (viewed / dismiss)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from daily_job_tool.web import queries
from daily_job_tool.web.deps import Conn, RWConn, templates
from daily_job_tool.web.routes.state import _safe_next

router = APIRouter()
VARY = {"Vary": "HX-Request", "Cache-Control": "no-store"}


def _check_id(job_id: int) -> None:
    if job_id > 2**62:
        raise HTTPException(404)


@router.get("/jobs/{job_id}")
async def job_detail(request: Request, job_id: int, conn: Conn):
    _check_id(job_id)
    job = await queries.fetch_job(conn, job_id)
    if job is None:
        raise HTTPException(404)
    htmx = request.headers.get("hx-request") == "true"
    ctx = {"page": "", "j": job, "now": job["_now"], "pane": htmx, "track_view": True}
    return templates.TemplateResponse(
        request, "_detail.html" if htmx else "job.html", ctx, headers=VARY
    )


async def _exists(rw, job_id: int) -> None:
    _check_id(job_id)
    ok = (await rw.execute(text("SELECT 1 FROM jobs WHERE id = :i"), {"i": job_id})).first()
    if ok is None:
        raise HTTPException(404)


@router.post("/jobs/{job_id}/viewed")
async def mark_viewed(rw: RWConn, job_id: int):
    await _exists(rw, job_id)
    await rw.execute(
        text(
            "INSERT INTO job_state (job_id, viewed_at) VALUES (:i, now()) "
            "ON CONFLICT (job_id) DO UPDATE SET viewed_at = now()"
        ),
        {"i": job_id},
    )
    return Response(status_code=204)


@router.post("/jobs/{job_id}/dismiss")
async def dismiss(request: Request, rw: RWConn, job_id: int, next: Annotated[str, Form()] = "/"):
    await _exists(rw, job_id)
    await rw.execute(
        text(
            "INSERT INTO job_state (job_id, dismissed_at) VALUES (:i, now()) "
            "ON CONFLICT (job_id) DO UPDATE SET dismissed_at = now()"
        ),
        {"i": job_id},
    )
    if request.headers.get("hx-request") != "true":
        return RedirectResponse(_safe_next(next), status_code=303)
    card = await queries.fetch_card(rw, job_id)
    return templates.TemplateResponse(
        request, "_card_dismissed.html", {"r": card, "next": _safe_next(next)}, headers=VARY
    )


@router.post("/jobs/{job_id}/undismiss")
async def undismiss(request: Request, rw: RWConn, job_id: int, next: Annotated[str, Form()] = "/"):
    await _exists(rw, job_id)
    await rw.execute(
        text("UPDATE job_state SET dismissed_at = NULL WHERE job_id = :i"), {"i": job_id}
    )
    if request.headers.get("hx-request") != "true":
        return RedirectResponse(_safe_next(next), status_code=303)
    card = await queries.fetch_card(rw, job_id)
    return templates.TemplateResponse(
        request,
        "_card_one.html",
        {"r": card, "now": card["_now"], "since": None, "next": _safe_next(next)},
        headers=VARY,
    )
