"""Shared building blocks for migrations under ``alembic/versions``.

Importable from a migration as ``from migration_helpers import ...``:
``alembic.ini`` puts this directory on ``sys.path`` (``prepend_sys_path``).
It is NOT ``app.*`` — migrations never import application code (see
``.claude/rules/migrations.md``), because a migration may run thousands of
commits after it was written, against whatever ``app`` looks like then.

What lives here must therefore stay stable: every migration that calls it, old
and new, runs this code on a fresh database. Change behaviour only by adding a
new parameter whose default keeps the old SQL; ``tests/unit/test_migration_helpers.py``
pins the exact text each existing caller emits.
"""

from alembic import op

#: Postgres major version CI runs every test and migration against. Kept in one
#: place so ``alembic/env.py`` can warn when a deploy targets something else,
#: and ``tests/unit/test_postgres_version_pins.py`` holds every CI image pin to it.
CI_POSTGRES_MAJOR = 17


def lock_retries_sql(tables: str, *, nowait: bool = False) -> str:
    """The ``DO`` block :func:`with_lock_retries` runs; split out so a test can
    compare it against the copies migrations used to carry."""
    if nowait:
        return f"""
        DO $$
        DECLARE attempts integer := 0;
        BEGIN
            LOOP
                BEGIN
                    LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE NOWAIT;
                    EXIT;
                EXCEPTION WHEN lock_not_available THEN
                    attempts := attempts + 1;
                    IF attempts >= 1200 THEN
                        RAISE;
                    END IF;
                    PERFORM pg_sleep(0.05);
                END;
            END LOOP;
        END
        $$
    """
    return f"""
        DO $$
        DECLARE
            attempts integer := 0;
            outer_timeout text := current_setting('lock_timeout');
        BEGIN
            PERFORM set_config('lock_timeout', '3s', true);
            LOOP
                BEGIN
                    LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE;
                    EXIT;
                EXCEPTION WHEN lock_not_available OR deadlock_detected THEN
                    attempts := attempts + 1;
                    IF attempts >= 100 THEN
                        RAISE;
                    END IF;
                    PERFORM pg_sleep(0.2);
                END;
            END LOOP;
            PERFORM set_config('lock_timeout', outer_timeout, true);
        END
        $$
    """


def with_lock_retries(tables: str, *, nowait: bool = False) -> None:
    """Take ACCESS EXCLUSIVE on every table in ``tables`` (comma-separated), or
    on none of them, retrying until it gets all at once.

    The backend this deploy replaces is still serving while migrations run, so
    a DDL statement that queues behind a live transaction would otherwise hold
    the global ``lock_timeout`` (10s) and then fail the whole deploy. Call this
    first in ``upgrade()``, naming every table the migration alters AND every
    table a new or dropped foreign key points at (adding one locks its target).

    Default: wait in line up to 3s per attempt, 100 attempts, 0.2s apart, and on
    a timeout or deadlock let go of everything and try again. Waiting (unlike
    ``NOWAIT``) queues ahead of later requests, so a steady stream of short
    transactions drains in front of it instead of never leaving every table
    free at the same instant (``b6fcc6362b79``).

    ``nowait=True``: never wait while holding nothing — 1200 attempts, 0.05s
    apart. The older form (``4383bf20b465``, ``41a261d02e9e``, ``f7985445d2bf``);
    prefer the default.

    The lock lasts until the migration's transaction ends — one migration, since
    ``env.py`` runs each in its own transaction.
    """
    op.execute(lock_retries_sql(tables, nowait=nowait))
