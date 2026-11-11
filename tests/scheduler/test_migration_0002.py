from alembic import command
from sqlalchemy import text

from tests.conftest import alembic_config


def test_0002_up_down_up(migrated_db):
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    cfg = alembic_config(migrated_db)

    async def cols() -> set[str]:
        eng = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            async with eng.connect() as c:
                rows = await c.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='companies'"
                    )
                )
                idx = await c.execute(
                    text("SELECT 1 FROM pg_indexes WHERE indexname='companies_industry'")
                )
                return {r[0] for r in rows} | ({"IDX"} if idx.first() else set())
        finally:
            await eng.dispose()

    new = {"industry", "is_staffing", "exclude_reason", "IDX"}
    assert new <= asyncio.run(cols())
    command.downgrade(cfg, "0001_initial")
    assert not new & asyncio.run(cols())
    command.upgrade(cfg, "head")
    assert new <= asyncio.run(cols())
