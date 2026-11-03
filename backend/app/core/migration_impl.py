"""Alembic's Postgres implementation, with ``CONCURRENTLY`` index statements
that retry themselves when they lose a lock race with live traffic.

Importing this module replaces alembic's ``postgresql`` implementation
(``__dialect__`` registers it), so only ``alembic/env.py`` imports it; the
running backend never does. Without a ``lock_race_retries`` option in the
migration context it behaves exactly like the class it extends.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from alembic.ddl.postgresql import PostgresqlImpl
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateIndex, DropIndex
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.util import immutabledict

from app.core.db import LockRaceRetries

logger = logging.getLogger(__name__)

_CONCURRENT = re.compile(
    r"\s*(?:CREATE\s+(?:UNIQUE\s+)?INDEX|DROP\s+INDEX)\s+CONCURRENTLY\b", re.I
)
_BUILD = re.compile(
    r"\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r'(?P<name>"(?:[^"]|"")+"|[\w$]+)',
    re.I,
)


class RetryingPostgresqlImpl(PostgresqlImpl):
    """Runs a ``CREATE INDEX CONCURRENTLY`` or ``DROP INDEX CONCURRENTLY`` that
    lost a lock race again, drawing on the upgrade's :class:`LockRaceRetries`.

    These run in an ``autocommit_block``, outside any transaction, so the
    migration around them cannot be rolled back and rerun
    (``run_migrations_retrying_lock_contention``); the statement is the unit
    that can be. A concurrent build that fails leaves its index behind INVALID
    (``pg_index.indisvalid`` false), and ``IF NOT EXISTS`` would then accept
    that husk as done, so before every try of a build an INVALID index of the
    same name is dropped. A valid one is never touched. A drop that fails
    partway also leaves the index INVALID, and running it again finishes it.
    """

    __dialect__ = "postgresql"

    def _exec(
        self,
        construct: Any,
        execution_options: Mapping[str, Any] | None = None,
        multiparams: Sequence[Mapping[str, Any]] | None = None,
        params: Mapping[str, Any] = immutabledict(),
    ) -> CursorResult | None:
        retries: LockRaceRetries | None = self.context_opts.get("lock_race_retries")
        sql = _sql_of(construct, self.dialect)
        if self.as_sql or retries is None or sql is None or not _CONCURRENT.match(sql):
            return super()._exec(construct, execution_options, multiparams, params)
        build = _BUILD.match(sql)
        while True:
            try:
                if build:
                    self._drop_if_invalid(build["name"])
                return super()._exec(construct, execution_options, multiparams, params)
            except DBAPIError as exc:
                wait = retries.wait_before_retry(exc)
                if wait is None:
                    raise
                logger.warning(
                    "migration lost a lock to live traffic (%s) in `%s`; "
                    "retrying it in %.0fs (attempt %d of %d)",
                    exc.orig,
                    " ".join(sql.split())[:120],
                    wait,
                    retries.tried,
                    retries.attempts,
                )
                retries.sleep(wait)

    def _drop_if_invalid(self, name: str) -> None:
        invalid = super()._exec(
            text(
                "SELECT NOT indisvalid FROM pg_index"
                " WHERE indexrelid = to_regclass(:name)"
            ),
            params={"name": name},
        )
        if invalid is not None and invalid.scalar():
            logger.warning("dropping INVALID index %s left by a failed build", name)
            super()._exec(text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))


def _sql_of(construct: Any, dialect: Any) -> str | None:
    """The SQL of a statement that may be a concurrent index statement:
    ``op.execute`` text, or ``op.create_index`` / ``op.drop_index``."""
    if isinstance(construct, str):
        return construct
    if isinstance(construct, TextClause):
        return construct.text
    if isinstance(construct, CreateIndex | DropIndex):
        return str(construct.compile(dialect=dialect))
    return None
