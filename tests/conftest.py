import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from daily_job_tool.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/daily_job_tool/db/migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _ping(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    finally:
        await engine.dispose()


async def _admin(url: URL, sql: str) -> None:
    """Run one statement on the `postgres` maintenance DB in autocommit mode."""
    engine = create_async_engine(
        url.set(database="postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as c:
            await c.execute(text(sql))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_db():
    """Per-process database `daily_job_tool_<pid>_test`, migrated to head and dropped at the end.

    Isolation lets several pytest processes (or agents) run concurrently. Set
    DAILY_JOB_TOOL_TEST_SHARED_DB=1 to use TEST_DATABASE_URL (must end in `_test`) as-is instead.
    """
    base = make_url(get_settings().test_database_url)
    shared = os.environ.get("DAILY_JOB_TOOL_TEST_SHARED_DB") == "1"
    own = not shared
    url = base if shared else base.set(database=f"daily_job_tool_{os.getpid()}_test")
    assert (url.database or "").endswith("_test"), "refusing to wipe a non-test DB"
    try:
        asyncio.run(_ping(base.set(database="postgres") if own else base))
        if own:
            asyncio.run(_admin(base, f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            asyncio.run(_admin(base, f'CREATE DATABASE "{url.database}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database unreachable ({base}): {exc!r}")
    rendered = url.render_as_string(hide_password=False)
    cfg = alembic_config(rendered)
    try:
        if shared:
            command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        yield rendered
    finally:
        if own:
            asyncio.run(_admin(base, f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        else:
            command.downgrade(cfg, "base")


@pytest.fixture(scope="session")
def test_db_url(migrated_db: str) -> str:
    return migrated_db


@pytest_asyncio.fixture
async def engine(migrated_db: str):
    eng: AsyncEngine = create_async_engine(migrated_db, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def conn(engine: AsyncEngine):
    """Connection inside a transaction that is rolled back after the test."""
    async with engine.connect() as connection:
        trans = await connection.begin()
        try:
            yield connection
        finally:
            await trans.rollback()


__all__ = ["AsyncConnection"]
