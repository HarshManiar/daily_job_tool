from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy.ext.asyncio import AsyncConnection

from daily_job_tool.web import queries, saved, visits
from daily_job_tool.web.deps import Conn, templates
from daily_job_tool.web.queries import FeedFilters

router = APIRouter()
NO_CACHE = {"Vary": "HX-Request, HX-Target", "Cache-Control": "no-store"}

PLACE_LABEL = {
    "any": "United States",
    "nj": "New Jersey",
    "nyc": "NYC area",
    "remote": "US remote",
    "other": "Other US",
}


async def _render(
    request: Request, conn: AsyncConnection, f: FeedFilters, *, excluded: bool, page: str
):
    # The tier filter was removed from the UI (the API still accepts it): never apply it here.
    f = f.model_copy(update={"tier": "any"})
    htmx = request.headers.get("hx-request") == "true"
    more_only = htmx and f.offset > 0
    rows, has_more, total, now = await queries.fetch_feed(
        conn, f, excluded=excluded, with_total=not more_only
    )
    base_path = "/filtered" if excluded else "/"
    app = request.app
    since, _ = visits.effective_since(await visits.read_state(conn), app.state.clock())
    ctx = {
        "page": page,
        "f": f,
        "rows": rows,
        "has_more": has_more,
        "total": total,
        "now": now,
        "excluded": excluded,
        "base_path": base_path,
        "next_qs": f.query_string(offset=f.offset + f.limit),
        "shown_to": f.offset + len(rows),
        "since": None if excluded else since,
        "here": base_path + ("?" + f.query_string() if f.query_string() else ""),
        "place_label": PLACE_LABEL[f.place],
        "selected_id": None,
        "j": None,
    }
    if not more_only:
        if excluded:
            ctx["reasons"] = await queries.reason_counts(conn, f)
        base = f.model_copy(update={"days": "all", "posted24": False, "posted48": False})
        base = base.model_copy(update={"offset": 0})
        ctx["posted_counts"] = await app.state.count_cache.get_or_set(
            (excluded, base.query_string()),
            lambda: queries.posted_counts(conn, f, excluded=excluded),
        )
        ctx["functions"] = await app.state.option_cache.get_or_set(
            "functions", lambda: queries.function_options(conn)
        )
        ctx["seniorities"] = queries.SENIORITY
        ctx["new_count"] = 0
        if not excluded and since is not None:
            ctx["new_count"] = (await queries.match_and_new_counts(conn, f, since))[1]
        ctx["saved"] = await saved.list_saved(conn, since)
        ctx["picked"] = await queries.companies_by_ids(conn, f.companies)
        ctx["save_qs"] = f.query_string(offset=0)
        if rows:  # the first job opens in the detail pane (hidden by CSS on narrow screens)
            ctx["j"] = await queries.fetch_job(conn, rows[0]["id"])
            ctx["selected_id"] = rows[0]["id"]
    if more_only:
        name = "_more.html"
    elif htmx and request.headers.get("hx-target") == "browse":
        name = "_browse.html"
    else:
        name = "filtered.html" if excluded else "feed.html"
    return templates.TemplateResponse(request, name, ctx, headers=NO_CACHE)


@router.get("/")
async def feed(
    request: Request,
    f: Annotated[FeedFilters, Query()],
    conn: Conn,
):
    return await _render(request, conn, f, excluded=False, page="feed")


@router.get("/filtered")
async def filtered(
    request: Request,
    f: Annotated[FeedFilters, Query()],
    conn: Conn,
):
    return await _render(request, conn, f, excluded=True, page="filtered")
