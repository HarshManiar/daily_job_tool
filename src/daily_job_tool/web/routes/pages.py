"""Manual-check list, removal flow and the static about/privacy pages."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from daily_job_tool.config import get_settings
from daily_job_tool.registry import removal
from daily_job_tool.web import manual
from daily_job_tool.web.deps import Conn, RWConn, templates

router = APIRouter()


def _today(request: Request):
    return request.app.state.clock().astimezone().date()


@router.get("/manual")
async def manual_page(request: Request, conn: Conn, q: Annotated[str, Query(max_length=100)] = ""):
    rows = manual.filter_rows(manual.load_rows(request.app.state.manual_csv), q)
    today = _today(request)
    done = await manual.checked_dates(conn)
    groups: dict[int, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.priority, []).append(
            {
                "r": r,
                "checked": done.get(r.key),
                "text": manual.checked_text(done.get(r.key), today),
            }
        )
    return templates.TemplateResponse(
        request,
        "manual.html",
        {"page": "manual", "groups": sorted(groups.items()), "q": q, "n": len(rows)},
    )


@router.post("/manual/check")
async def manual_check(request: Request, rw: RWConn, key: Annotated[str, Form(max_length=300)]):
    rows = {r.key: r for r in manual.load_rows(request.app.state.manual_csv)}
    if key not in rows:
        raise HTTPException(404)
    today = _today(request)
    state = await manual.toggle(rw, key, today)
    if request.headers.get("hx-request") != "true":
        return RedirectResponse("/manual", status_code=303)
    return templates.TemplateResponse(
        request,
        "_manual_row.html",
        {"it": {"r": rows[key], "checked": state, "text": manual.checked_text(state, today)}},
    )


def _contact() -> str:
    return get_settings().bot_contact.strip()


@router.get("/remove")
async def remove_page(
    request: Request, conn: Conn, target: Annotated[str, Query(max_length=200)] = ""
):
    ctx: dict = {"page": "", "contact": _contact(), "target": target, "info": None, "done": None}
    if target.strip():
        info = await removal.find_company(conn, target)
        ctx["info"] = info
        ctx["missing"] = info is None
    return templates.TemplateResponse(request, "remove.html", ctx)


@router.post("/remove/confirm")
async def remove_confirm(
    request: Request,
    rw: RWConn,
    target: Annotated[str, Form(max_length=200)],
    reason: Annotated[str, Form(max_length=300)] = "",
):
    try:
        res = await removal.remove_company_conn(rw, target, reason or "owner decision")
    except removal.CompanyNotFound as exc:
        raise HTTPException(404, "No such company") from exc
    return templates.TemplateResponse(
        request,
        "remove.html",
        {"page": "", "contact": _contact(), "target": "", "info": None, "done": res},
    )


@router.get("/about")
async def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {"page": ""})


@router.get("/privacy")
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {"page": ""})
