from __future__ import annotations

from fastapi import APIRouter, Request

from daily_job_tool.config import get_settings
from daily_job_tool.web import queries
from daily_job_tool.web.deps import Conn, templates

router = APIRouter()


@router.get("/health")
async def health(request: Request, conn: Conn):
    d = await queries.health_data(conn)
    return templates.TemplateResponse(request, "health.html", {"page": "health", "d": d})


@router.get("/bot")
async def bot(request: Request):
    contact = get_settings().bot_contact
    return templates.TemplateResponse(
        request, "bot.html", {"page": "", "contact": contact, "ua": get_settings().user_agent}
    )
