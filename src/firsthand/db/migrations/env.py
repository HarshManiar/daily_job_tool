"""Alembic env (async). URL precedence: -x dburl=..., FIRSTHAND_ALEMBIC_URL env,
sqlalchemy.url set on the Config, then Settings.database_url."""

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from firsthand.config import get_settings

config = context.config
target_metadata = None  # migrations are hand-written SQL


def _url() -> str:
    return (
        context.get_x_argument(as_dictionary=True).get("dburl")
        or os.environ.get("FIRSTHAND_ALEMBIC_URL")
        or config.get_main_option("sqlalchemy.url")
        or get_settings().database_url
    )


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
