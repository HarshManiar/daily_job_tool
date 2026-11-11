import asyncio

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import alembic_config

NEW = {
    "match_tier", "match_status", "match_reasons", "visa_status", "visa_evidence",
    "years_min", "years_max", "industry_rank", "location_rank", "fit_score",
}  # fmt: skip


def test_0003_up_down_up(migrated_db):
    cfg = alembic_config(migrated_db)

    async def state() -> set[str]:
        eng = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            async with eng.connect() as c:
                cols = await c.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_name='jobs'"
                    )
                )
                idx = await c.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE indexname IN ('jobs_my_feed','jobs_excluded')"
                    )
                )
                return {r[0] for r in cols} | {f"IDX:{r[0]}" for r in idx}
        finally:
            await eng.dispose()

    full = NEW | {"IDX:jobs_my_feed", "IDX:jobs_excluded"}
    assert full <= asyncio.run(state())
    command.downgrade(cfg, "0002_company_meta")
    assert not full & asyncio.run(state())
    command.upgrade(cfg, "head")
    assert full <= asyncio.run(state())
