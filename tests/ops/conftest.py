import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

TRUNCATE = "TRUNCATE companies, crawl_runs RESTART IDENTITY CASCADE"


@pytest_asyncio.fixture
async def db_clean(migrated_db: str):
    eng = create_async_engine(migrated_db, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.execute(text(TRUNCATE))
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text(TRUNCATE))
    await eng.dispose()
