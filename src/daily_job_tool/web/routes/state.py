"""State-changing routes (the only writers): visit markers and saved searches.

All are POST (rename/delete also accept their natural verbs) and pass the same-origin check in
app.SameOriginForWrites. They use the separate read-write engine via RWConn.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from daily_job_tool.web import saved, visits
from daily_job_tool.web.deps import Conn, RWConn, templates
from daily_job_tool.web.queries import FeedFilters

router = APIRouter()


def _safe_next(nxt: str) -> str:
    """Only same-site relative paths ('/...' but not '//...' or '/\\...')."""
    if nxt.startswith("/") and not nxt.startswith(("//", "/\\")) and "\n" not in nxt:
        return nxt
    return "/"


def _see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("/saved")
async def saved_page(request: Request, conn: Conn):
    """Manage saved searches; any filter querystring passed in is offered for saving."""
    try:
        f = FeedFilters.from_qs(request.url.query)
    except ValueError:
        f = FeedFilters()
    since, _ = visits.effective_since(await visits.read_state(conn), request.app.state.clock())
    return templates.TemplateResponse(
        request,
        "saved.html",
        {
            "page": "saved",
            "saved": await saved.list_saved(conn, since),
            "save_qs": f.query_string(offset=0),
            "since": since,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/visit/touch")
async def visit_touch(request: Request, rw: RWConn):
    await visits.touch(rw, request.app.state.clock())
    return Response(status_code=204)


@router.post("/visit/seen")
async def visit_seen(request: Request, rw: RWConn, next: Annotated[str, Form()] = "/"):
    await visits.mark_all_seen(rw, request.app.state.clock())
    return _see_other(_safe_next(next))


@router.post("/saved")
async def saved_create(
    rw: RWConn, name: Annotated[str, Form(max_length=400)], qs: Annotated[str, Form()] = ""
):
    n = saved.clean_name(name)
    if not n:
        raise HTTPException(422, "A name is required")
    try:
        f = FeedFilters.from_pairs(parse_qsl(qs[:4000], keep_blank_values=True))
    except ValueError as exc:
        raise HTTPException(422, f"Invalid filters: {exc}") from exc
    if await saved.create(rw, n, f) is None:
        raise HTTPException(409, f"At most {saved.MAX_SAVED} saved searches")
    shown = f.query_string()
    return _see_other("/?" + shown if shown else "/")


@router.post("/saved/{sid}/rename")
async def saved_rename(rw: RWConn, sid: int, name: Annotated[str, Form(max_length=400)]):
    n = saved.clean_name(name)
    if not n:
        raise HTTPException(422, "A name is required")
    if sid > 2**62 or not await saved.rename(rw, sid, n):
        raise HTTPException(404)
    return _see_other("/")


@router.post("/saved/{sid}/delete")
@router.delete("/saved/{sid}")
async def saved_delete(rw: RWConn, sid: int):
    if sid > 2**62 or not await saved.delete(rw, sid):
        raise HTTPException(404)
    return _see_other("/")
