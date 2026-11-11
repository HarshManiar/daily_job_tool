import asyncio

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import alembic_config

CHECKS = {
    "jobs_years_min_chk", "jobs_years_max_chk", "jobs_location_rank_chk",
    "jobs_industry_rank_chk", "jobs_match_tier_chk",
}  # fmt: skip


def test_0004_up_down_up(migrated_db):
    cfg = alembic_config(migrated_db)

    async def state() -> set[str]:
        eng = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            async with eng.connect() as c:
                cons = await c.execute(
                    text("SELECT conname FROM pg_constraint WHERE conname LIKE 'jobs\\_%\\_chk'")
                )
                cols = await c.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='jobs' AND column_name='norm_flags'"
                    )
                )
                return {r[0] for r in cons} | {f"COL:{r[0]}" for r in cols}
        finally:
            await eng.dispose()

    full = CHECKS | {"COL:norm_flags"}
    assert full <= asyncio.run(state())
    command.downgrade(cfg, "0003_match")
    assert not full & asyncio.run(state())
    command.upgrade(cfg, "head")
    assert full <= asyncio.run(state())
