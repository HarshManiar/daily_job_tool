from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from daily_job_tool.config import get_settings

_engine: AsyncEngine | None = None


def make_engine(url: str | None = None, **kwargs) -> AsyncEngine:
    """Create a new AsyncEngine (defaults to Settings.database_url)."""
    return create_async_engine(url or get_settings().database_url, **kwargs)


def get_engine() -> AsyncEngine:
    """Process-wide shared engine for the default database."""
    global _engine
    if _engine is None:
        _engine = make_engine(pool_pre_ping=True)
    return _engine


@asynccontextmanager
async def get_conn(engine: AsyncEngine | None = None) -> AsyncIterator[AsyncConnection]:
    """Connection inside a transaction: commits on success, rolls back on error."""
    async with (engine or get_engine()).begin() as conn:
        yield conn
