"""Refresh button: POST /refresh starts one detached crawl; GET /refresh/status is the fragment."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from daily_job_tool.ops.heartbeat import classify, read_heartbeat
from daily_job_tool.ops.status import human_age
from daily_job_tool.web import refresh as rf
from daily_job_tool.web.deps import Conn, templates

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store"}


async def _last_finished(request: Request, conn) -> datetime | None:
    async def q():
        return (await conn.execute(text("SELECT max(finished_at) FROM crawl_runs"))).scalar()

    return await request.app.state.refresh_cache.get_or_set("last_finished", q)


async def _fragment(request: Request, conn, *, poll: bool, notice: str = "", fresh: bool = False):
    app = request.app
    now: datetime = app.state.clock()
    if fresh:
        app.state.refresh_cache.clear()
    ctx: dict[str, Any] = {"notice": notice, "state": "idle", "poll": False}
    hb = read_heartbeat(app.state.heartbeat_path)
    ctx["worker"] = classify(hb, now) == "alive"
    ctx["worker_text"] = rf.worker_summary(hb) if ctx["worker"] else ""
    info = rf.running_info(app.state.refresh_pid_path, now)
    if info is not None:
        ctx.update(state="running", poll=True, **await rf.progress(conn, info["started"]))
    else:
        rec = rf.read_pidfile(app.state.refresh_pid_path)
        if poll and rec is not None and now - rec["started"] <= rf.MAX_AGE:
            ctx.update(state="done", **await rf.progress(conn, rec["started"]))
    last = await _last_finished(request, conn)
    ctx["last"] = human_age((now - last).total_seconds()) if last else "never"
    return templates.TemplateResponse(request, "_refresh.html", ctx, headers=NO_STORE)


@router.get("/refresh/status")
async def refresh_status(request: Request, conn: Conn, poll: int = 0):
    return await _fragment(request, conn, poll=bool(poll))


@router.post("/refresh")
async def refresh(request: Request, conn: Conn):
    app = request.app
    async with app.state.refresh_lock:
        out = await asyncio.to_thread(
            rf.start_refresh,
            spawner=app.state.spawner,
            pid_path=app.state.refresh_pid_path,
            heartbeat_path=app.state.heartbeat_path,
            log_path=app.state.refresh_log_path,
            now=app.state.clock(),
        )
    if request.headers.get("hx-request") != "true":
        return RedirectResponse("/", status_code=303)
    notice = {
        "worker": "The worker is running; it refreshes automatically.",
        "running": "A refresh is already running.",
        "dryrun": "Dry run: no crawler was started.",
        "started": "",
    }[out.state]
    return await _fragment(request, conn, poll=False, notice=notice, fresh=True)
