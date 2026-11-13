import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import alembic_config

TABLES = {
    "companies", "sources", "blocked_domains", "removal_requests", "postings", "jobs",
    "crawl_runs", "company_daily_stats", "robots_cache", "fx_rates",
    "app_state", "saved_searches", "job_state",
}  # fmt: skip
ABSENT = {"llm_extractions", "plans", "users", "auth_tokens", "sessions"}
INDEXES = {
    "sources_due", "postings_open", "jobs_feed", "jobs_tsv", "jobs_title_trg", "jobs_tags",
    "jobs_countries", "jobs_salary", "jobs_company", "crawl_runs_recent", "companies_name_trg",
    "postings_job", "crawl_runs_source",
}  # fmt: skip


async def _seed(conn):
    cid = (
        await conn.execute(
            text("INSERT INTO companies (name, slug) VALUES ('Acme', 'acme') RETURNING id")
        )
    ).scalar_one()
    sid = (
        await conn.execute(
            text(
                "INSERT INTO sources (company_id, adapter, token) "
                "VALUES (:c, 'greenhouse', 'acme') RETURNING id"
            ),
            {"c": cid},
        )
    ).scalar_one()
    return cid, sid


async def test_tables_exist(conn):
    rows = await conn.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    names = {r[0] for r in rows}
    assert TABLES <= names
    assert not (ABSENT & names)


async def test_indexes_exist(conn):
    rows = await conn.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"))
    assert INDEXES <= {r[0] for r in rows}


async def test_job_search_tsv(conn):
    cid, sid = await _seed(conn)
    pid = (
        await conn.execute(
            text(
                "INSERT INTO postings (source_id, external_id, url, payload, payload_hash) "
                "VALUES (:s, '1', 'https://x/1', '{}'::jsonb, '\\x00') RETURNING id"
            ),
            {"s": sid},
        )
    ).scalar_one()
    await conn.execute(
        text(
            "INSERT INTO jobs (company_id, primary_posting_id, dedupe_key, title, title_norm, "
            "tech_tags, first_seen_at, last_seen_at, freshness_at, apply_url, normalizer_version) "
            "VALUES (:c, :p, 'k', 'Backend Engineer', 'backend engineer', ARRAY['python','go'], "
            "now(), now(), now(), 'https://x/apply', 1)"
        ),
        {"c": cid, "p": pid},
    )
    await conn.execute(text("UPDATE postings SET job_id = (SELECT id FROM jobs)"))
    n = (
        await conn.execute(
            text(
                "SELECT count(*) FROM jobs "
                "WHERE search_tsv @@ websearch_to_tsquery('english', 'python')"
            )
        )
    ).scalar_one()
    assert n == 1
    n = (
        await conn.execute(
            text(
                "SELECT count(*) FROM jobs "
                "WHERE search_tsv @@ websearch_to_tsquery('english', 'rust')"
            )
        )
    ).scalar_one()
    assert n == 0


async def test_sources_unique_adapter_token(conn):
    cid, _ = await _seed(conn)
    with pytest.raises(IntegrityError):
        await conn.execute(
            text(
                "INSERT INTO sources (company_id, adapter, token) VALUES (:c, 'greenhouse', 'acme')"
            ),
            {"c": cid},
        )


def test_migration_up_down_up(migrated_db):
    cfg = alembic_config(migrated_db)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    async def count_tables() -> int:
        eng = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            async with eng.connect() as c:
                return (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_name = ANY(:t)"
                        ),
                        {"t": sorted(TABLES)},
                    )
                ).scalar_one()
        finally:
            await eng.dispose()

    assert asyncio.run(count_tables()) == len(TABLES)


def test_migration_0005_up_down_up(migrated_db):
    cfg = alembic_config(migrated_db)
    new = {"app_state", "saved_searches"}

    async def present() -> set[str]:
        eng = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            async with eng.connect() as c:
                rows = await c.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name = ANY(:t)"
                    ),
                    {"t": sorted(new)},
                )
                return {r[0] for r in rows}
        finally:
            await eng.dispose()

    command.downgrade(cfg, "0004_match_checks")
    assert asyncio.run(present()) == set()
    command.upgrade(cfg, "head")
    assert asyncio.run(present()) == new


def test_migration_0006_up_down_up(migrated_db):
    cfg = alembic_config(migrated_db)

    async def present() -> bool:
        eng = create_async_engine(migrated_db, poolclass=NullPool)
        try:
            async with eng.connect() as c:
                return bool(
                    (
                        await c.execute(text("SELECT to_regclass('public.job_state') IS NOT NULL"))
                    ).scalar_one()
                )
        finally:
            await eng.dispose()

    command.downgrade(cfg, "0005_app_state_saved_searches")
    assert asyncio.run(present()) is False
    command.upgrade(cfg, "head")
    assert asyncio.run(present()) is True
