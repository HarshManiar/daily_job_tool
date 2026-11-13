from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from daily_job_tool.config import get_settings, get_user_agent
from daily_job_tool.ops.status import collect_status, human_age
from daily_job_tool.web import queries
from daily_job_tool.web.deps import Conn, templates

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health(request: Request, conn: Conn):
    d = await queries.health_data(conn)
    st = None
    try:
        st = await collect_status(
            request.app.state.engine,
            request.app.state.clock(),
            heartbeat_path=request.app.state.heartbeat_path,
        )
    except Exception:  # health must still render when the ops snapshot fails
        log.exception("collect_status failed")
    ctx: dict = {"page": "health", "d": d, "st": st, "human_age": human_age}
    if st is not None:
        b = st["last_backup_at"]
        ctx["backup_age"] = human_age((st["now"] - b).total_seconds()) if b else "never"
        w = st["worker"] or {}
        ctx["worker_line"] = st["worker_state"] + (
            f" (pid {w.get('pid')}, {w.get('in_flight')} in flight)"
            if w and st["worker_state"] != "not running"
            else ""
        )
    return templates.TemplateResponse(request, "health.html", ctx)


@router.get("/bot")
async def bot(request: Request):
    contact = get_settings().bot_contact.strip()
    ua = get_user_agent() if contact else get_settings().user_agent
    return templates.TemplateResponse(
        request, "bot.html", {"page": "", "contact": contact, "ua": ua}
    )
