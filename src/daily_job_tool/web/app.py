"""Daily Job Tool web app: local, single-user. Read-only except app_state / saved_searches."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from daily_job_tool.db.session import make_engine
from daily_job_tool.web.cache import TTLCache
from daily_job_tool.web.deps import HERE, templates

log = logging.getLogger("daily_job_tool.web")

ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})

CSP = (
    "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def _hostname(host_header: str) -> str:
    h = host_header.strip().lower()
    if h.startswith("["):  # IPv6 literal: [::1]:8000
        return h[: h.find("]") + 1] if "]" in h else h
    return h.split(":")[0]


class LocalHostOnly:
    """DNS-rebinding defense: only localhost / 127.0.0.1 / [::1] Host headers (like
    TrustedHostMiddleware, but understands IPv6 literals with a port)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            if _hostname(host) not in ALLOWED_HOSTS:
                if scope["type"] == "http":
                    await PlainTextResponse("Invalid host header", status_code=400)(
                        scope, receive, send
                    )
                return
        await self.app(scope, receive, send)


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _origin_ok(url: str, host_header: str) -> bool:
    """True when `url` (an Origin or Referer value) is a loopback host on the app's port."""
    try:
        u = urlsplit(url)
        host, port = (u.hostname or "").lower(), u.port
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or host.strip("[]") not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return False
    h = host_header.strip().lower()
    hp = h.rsplit(":", 1)[1] if (":" in h and not h.endswith("]")) else ""
    app_port = int(hp) if hp.isdigit() else (443 if u.scheme == "https" else 80)
    return (port or (443 if u.scheme == "https" else 80)) == app_port


class SameOriginForWrites:
    """CSRF defense (the app has no cookies/sessions): every state-changing request must carry
    an Origin (or, failing that, Referer) naming a loopback host on the app's own port."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            h = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            src = h.get("origin") or h.get("referer")
            if not src or not _origin_ok(src, h.get("host", "")):
                await PlainTextResponse("Cross-origin request rejected", status_code=403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                for k, v in SECURITY_HEADERS.items():
                    if k.lower().encode() not in present:
                        headers.append((k.lower().encode(), v.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, wrapped)


def create_app(
    engine: AsyncEngine | None = None,
    rw_engine: AsyncEngine | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """`engine` is the read-only query engine; `rw_engine` is a SEPARATE small engine used only
    by the app_state / saved_searches routes. `clock` is injectable for tests."""
    owns_engine = engine is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if owns_engine:
            app.state.engine = make_engine(pool_pre_ping=True)
            app.state.rw_engine = make_engine(pool_pre_ping=True, pool_size=2, max_overflow=1)
        try:
            yield
        finally:
            if owns_engine:
                await app.state.engine.dispose()
                await app.state.rw_engine.dispose()

    app = FastAPI(
        title="Daily Job Tool", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    if engine is not None:
        app.state.engine = engine
        app.state.rw_engine = rw_engine or engine
    app.state.clock = clock or (lambda: datetime.now(UTC))
    app.state.count_cache = TTLCache(30)  # 24h/48h box counts
    app.state.option_cache = TTLCache(300)  # function dropdown values

    from daily_job_tool.web.routes import api, companies, feed, health, jobs, state

    for mod in (feed, jobs, companies, health, api, state):
        app.include_router(mod.router)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return templates.TemplateResponse(request, "404.html", {"page": ""}, status_code=404)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return templates.TemplateResponse(request, "500.html", {"page": ""}, status_code=500)

    app.add_middleware(SameOriginForWrites)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(LocalHostOnly)  # outermost: rejects before anything else runs
    return app


app = create_app()
