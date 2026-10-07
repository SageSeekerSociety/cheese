import asyncio
import logging
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Make the application package importable and pull in settings + Base.
# Import every model so its tables register on Base.metadata. Without this,
# autogenerate would see an empty metadata and drop all tables.
from migration_helpers import CI_POSTGRES_MAJOR  # noqa: E402

import app.models  # noqa: E402, F401
from app.core.config import settings  # noqa: E402
from app.core.db import Base, apply_migration_timeouts  # noqa: E402

log = logging.getLogger("alembic.env")

# Inject the runtime database URL instead of hardcoding it in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.database_url)

# Target metadata for 'autogenerate' support.
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transaction_per_migration=True,
    )

    with context.begin_transaction():
        # Emit the same guard into the generated SQL script. Offline never
        # touches a live DB, so this is documentation of intent more than
        # protection; the online path is the one that matters for #356.
        context.execute(f"SET lock_timeout = '{settings.migration_lock_timeout}'")
        context.execute(
            f"SET statement_timeout = '{settings.migration_statement_timeout}'"
        )
        context.run_migrations()


def report_server_version(connection: Connection) -> None:
    """Say which Postgres this upgrade runs against, and warn when it is not
    the major version CI tested every migration on (``CI_POSTGRES_MAJOR``).

    A warning, not a failure: the deployed databases' versions live in each
    box's own ``.env``, not in this repository, so a hard stop could abort a
    deploy that has always worked. The line in the deploy log is how a
    mismatch gets noticed.
    """
    shown = connection.exec_driver_sql("SHOW server_version_num").scalar_one()
    major = int(shown) // 10000
    log.info("Postgres server %s (CI tests against %s)", shown, CI_POSTGRES_MAJOR)
    if major != CI_POSTGRES_MAJOR:
        log.warning(
            "Postgres major version %s differs from the %s CI runs every migration on; "
            "a migration that passed CI may behave differently here.",
            major,
            CI_POSTGRES_MAJOR,
        )


def do_run_migrations(connection: Connection) -> None:
    apply_migration_timeouts(connection)
    report_server_version(connection)
    # The timeouts are session-level, so they outlive this commit. Committing
    # leaves no transaction open: alembic would otherwise adopt the one
    # SQLAlchemy autobegan above as an "external" transaction and run the whole
    # upgrade inside it, which is what transaction_per_migration turns off.
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Each migration commits on its own (.claude/rules/migrations.md): its
        # locks and the row locks of its backfill end with it, instead of being
        # held until the last migration of the deploy finishes.
        transaction_per_migration=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
