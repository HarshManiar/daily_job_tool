"""Shared web plumbing: templates and the per-request read-only connection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import nh3
from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncConnection

from daily_job_tool.web import format as fmt
from daily_job_tool.web.visits import since_text

HERE = Path(__file__).parent

templates = Jinja2Templates(directory=str(HERE / "templates"))  # autoescape on for .html
_env = templates.env
_env.filters.update(
    pay_range=fmt.pay_range,
    posted_text=fmt.posted_text,
    relative_date=fmt.relative_date,
    years_chip=fmt.years_chip,
    reason_text=fmt.reason_text,
    safe_url=fmt.safe_http_url,
    warn_chips=fmt.warn_chips,
    quiet_chips=fmt.quiet_chips,
    exclusion_reasons=fmt.exclusion_reasons,
    location_text=fmt.location_text,
    tier_label=fmt.tier_label,
    since_text=since_text,
)
_env.globals.update(
    LOCATION_RANK_TEXT=fmt.LOCATION_RANK_TEXT,
    INDUSTRY_RANK_TEXT=fmt.INDUSTRY_RANK_TEXT,
    TIER_TEXT=fmt.TIER_TEXT,
)


def sanitize_description(html: str | None) -> str:
    """Defensive second nh3 pass at render time (stored HTML is already sanitized)."""
    return nh3.clean(html or "", link_rel="noopener noreferrer")


_env.filters["sanitize"] = sanitize_description


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """Read-only connection: the web app never writes."""
    async with request.app.state.engine.connect() as conn:
        conn = await conn.execution_options(postgresql_readonly=True)
        yield conn


async def get_rw_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """Read-write transaction on the SEPARATE engine; only app_state / saved_searches routes
    depend on this (commits on success)."""
    async with request.app.state.rw_engine.begin() as conn:
        yield conn


def now_utc(request: Request):
    return request.app.state.clock()


Conn = Annotated[AsyncConnection, Depends(get_conn)]
RWConn = Annotated[AsyncConnection, Depends(get_rw_conn)]
