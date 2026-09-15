import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from athenaeum.config import get_settings
from athenaeum.storage.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _dry_run() -> bool:
    """True for `alembic -x dry_run=true upgrade head`.

    A dry run executes every pending migration for real, so the row counts
    migrations log are genuine, then rolls the whole transaction back. That
    only works because Postgres runs schema changes (DDL) transactionally.
    """
    value = context.get_x_argument(as_dictionary=True).get("dry_run", "")
    return value.lower() in {"1", "true", "yes"}


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    if _dry_run():
        # Opening the transaction here makes Alembic run inside it instead of
        # committing its own, so the rollback also discards the version bump.
        transaction = connection.begin()
        try:
            context.run_migrations()
        finally:
            transaction.rollback()
        logging.getLogger("alembic.runtime.migration").warning(
            "DRY RUN: every change above was rolled back; nothing was written."
        )
        return
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
