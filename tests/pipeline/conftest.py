import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

TRUNCATE = (
    "TRUNCATE companies, crawl_runs, robots_cache, fx_rates, blocked_domains, "
    "removal_requests RESTART IDENTITY CASCADE"
)


@pytest_asyncio.fixture
async def db_clean(migrated_db: str):
    """Fresh tables for tests whose code under test commits its own transactions."""
    eng = create_async_engine(migrated_db, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.execute(text(TRUNCATE))
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text(TRUNCATE))
    await eng.dispose()


@pytest.fixture(autouse=True)
def fake_adapters(monkeypatch):
    from firsthand.adapters import registry
    from tests.pipeline.helpers import FakeAdapter, FakeT3

    registry._load()
    monkeypatch.setitem(registry._REGISTRY, FakeAdapter.name, FakeAdapter)
    monkeypatch.setitem(registry._REGISTRY, FakeT3.name, FakeT3)
