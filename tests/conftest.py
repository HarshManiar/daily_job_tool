import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from firsthand.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/firsthand/db/migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _ping(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_db_url() -> str:
    return get_settings().test_database_url


@pytest.fixture(scope="session")
def migrated_db(test_db_url: str):
    """Run `alembic upgrade head` on the test DB for the session; `downgrade base` at the end."""
    try:
        asyncio.run(_ping(test_db_url))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database unreachable ({test_db_url}): {exc!r}")
    from sqlalchemy.engine import make_url

    assert (make_url(test_db_url).database or "").endswith("_test"), (
        "refusing to wipe a non-test DB"
    )
    cfg = alembic_config(test_db_url)
    command.downgrade(cfg, "base")  # start clean even if a previous run crashed
    command.upgrade(cfg, "head")
    yield test_db_url
    command.downgrade(cfg, "base")


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
