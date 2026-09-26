"""Which backend process is running the platform's standing work right now.

A rollout overlaps two backend processes on the same database: the new one
starts and serves before the old one has stopped. Most of what a process does is
safe to double — answering a request, starting the turn that request asked for.
The rest is not, because it is judged from the process's own memory: which
sessions it is listening to, which turns it is running, which periodic sweep it
last ran. Two processes doing that at once each see the other's live work as
abandoned, and close it, re-send it, or land its output a second time.

So that work belongs to exactly one process at a time, and a Postgres
session-level advisory lock says which. It is held on a connection of its own
for as long as the process owns the work: a process that dies drops its
connection and with it the lock, so the next one takes over without anybody
having to notice the death first. Nothing else is needed — no table, no lease,
no heartbeat to tune.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# Any fixed bigint, as long as nothing else in this database takes it. Spelled as
# the bytes of "cheese" so a reader of `pg_locks` can tell whose it is.
OWNER_LOCK = 0x636865657365


class Ownership:
    """The one lock, on the one connection that holds it."""

    def __init__(self, url: str, *, retry_s: float = 1.0) -> None:
        self._url = url
        self._retry_s = retry_s
        self._engine: AsyncEngine | None = None
        self._connection: AsyncConnection | None = None
        self.held = False

    async def _connect(self) -> AsyncConnection:
        if self._connection is None:
            # Its own engine, outside the application's pool: a connection that
            # is never returned would otherwise be one slot fewer for requests,
            # and the pool's recycling would one day hand the lock back to
            # Postgres. Autocommit, so the connection never sits idle inside a
            # transaction for the life of the process.
            self._engine = create_async_engine(self._url, poolclass=NullPool)
            connection = await self._engine.connect()
            self._connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
        return self._connection

    async def _drop_connection(self) -> None:
        connection, engine = self._connection, self._engine
        self._connection = self._engine = None
        if connection is not None:
            try:
                await connection.close()
            except Exception:  # noqa: BLE001 — it is already gone
                pass
        if engine is not None:
            await engine.dispose()

    async def try_acquire(self) -> bool:
        try:
            connection = await self._connect()
            self.held = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": OWNER_LOCK}
                )
            )
        except Exception:  # noqa: BLE001 — a database blip is a later retry
            logger.exception("could not ask for the owner lock")
            await self._drop_connection()
            self.held = False
        return self.held

    async def acquire(self) -> None:
        """Wait until this process is the owner. Returns at once when no other
        process holds the lock, which is every start that is not a rollout."""
        waited = False
        while not await self.try_acquire():
            if not waited:
                waited = True
                logger.info("another backend owns the running work; waiting for it")
            await asyncio.sleep(self._retry_s)
        if waited:
            logger.info("took over the running work from the previous backend")

    async def still_held(self) -> bool:
        """Is the lock still ours? Only a lost connection can take it away."""
        if not self.held or self._connection is None:
            return False
        try:
            await self._connection.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 — answered below
            logger.exception("the owner lock's connection is gone")
            await self._drop_connection()
            self.held = False
            return False

    async def release(self) -> None:
        if self._connection is not None and self.held:
            try:
                await self._connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": OWNER_LOCK}
                )
            except Exception:  # noqa: BLE001 — closing the connection frees it too
                logger.exception("could not unlock the owner lock; closing instead")
        self.held = False
        await self._drop_connection()


async def keep_holding(ownership: Ownership, *, every_s: float = 10.0) -> None:
    """Watch the lock's connection, and stop being the owner if it goes.

    A database restart drops the connection, and the lock with it. If nobody
    else was waiting the lock is simply taken again. If somebody was, they own
    the work now, and this process carrying on would be the double ownership the
    lock exists to prevent — so it shuts down the ordinary way instead, and its
    container's restart policy brings it back as a process that waits its turn.
    """
    while True:
        await asyncio.sleep(every_s)
        if await ownership.still_held():
            continue
        if await ownership.try_acquire():
            logger.warning("the owner lock was dropped with its connection; retaken")
            continue
        logger.error("another backend took over the running work; shutting down")
        os.kill(os.getpid(), signal.SIGTERM)
        return
