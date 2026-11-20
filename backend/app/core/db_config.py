"""The database connection and its pool: where it is, and how many connections
each process may hold.

`config.py` is at the size cap every module is held to
(`.claude/scripts/check-file-sizes.py`, `.claude/rules/architecture.md`), and
this section stands alone. `Settings` subclasses the class below, so the
fields, their environment names and `settings.db_*` / `settings.backend_*` are
exactly what `config.py` declared before the move: `backend/.env.example` is
held to `Settings.model_fields`, and a field declared anywhere on that class is
a field it reads.
"""

from typing import Literal

from pydantic import BaseModel


class DatabaseSettings(BaseModel):
    # --- Database ---
    # The dev database the repo-root docker-compose.yml publishes on :5432. The
    # test server is a SEPARATE container on :5433 (tests/conftest.py's
    # TEST_PG_BASE), so running the suite never disturbs your dev data.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/cheese"
    db_echo: bool = False
    # How many database connections one backend process may hold. SQLAlchemy's
    # own defaults (5 + 10) size a pool for a thread-per-request server; this is
    # one asyncio process where every in-flight request holds a connection at
    # the same time, so the ceiling is 「同时在飞的请求数」, not 「worker 数」. A
    # single page load fans out dozens of them, and a request that cannot get a
    # connection within `db_pool_timeout_s` raises TimeoutError — a 500 on a
    # perfectly healthy database.
    #
    # The number below the pool must also fit is the SERVER's, which nothing in
    # this process can see. THREE pools share it during an ordinary release: the
    # outgoing backend, the incoming one the rollout brings up beside it, and the
    # separately released connection owner. At 20+30 each that is 150 against a
    # `max_connections` of 100, and on 2026-09-16 it landed exactly as the
    # arithmetic predicts — 19 seconds after a new backend answered /healthz,
    # PostgreSQL refused 83 connections in one minute with `sorry, too many
    # clients already`, and every request in flight failed with it. Users read
    # that as the rooms mysteriously 401-ing and recovering, several times a day,
    # once per deploy.
    #
    # So the ceiling is per-process but the budget is shared: the two backends
    # at (size + overflow) each, plus the connection owner's own pool, have to
    # leave room for the migration the deploy runs and for anyone holding a
    # psql. The owner registers devices and answers bindings; it never fans out
    # the way a page load does, so the compose file hands it DB_POOL_SIZE=5 and
    # DB_MAX_OVERFLOW=5 and the backends take the rest. Each backend also holds
    # one connection outside its pool for the owner lock (`core.ownership`):
    # 2 x (35 + 1) + 10 + 10 = 92 of the 97 a default PostgreSQL offers once its
    # superuser reserve is taken out (tests/unit/test_db_pool_fits_the_server.py
    # holds this arithmetic). A
    # box whose server is configured larger can raise these; a box that adds a
    # fourth pool has to lower them. dev's server was raised to 200 on
    # 2026-09-18 (conf.d/10-connections.conf on cheese-dev-env1-postgresql).
    db_pool_size: int = 20
    db_max_overflow: int = 15
    db_pool_timeout_s: float = 30.0
    # How many prepared statements each pooled connection keeps (asyncpg,
    # LRU). A statement that fell out is prepared again: one more round trip
    # and a fresh plan, 2.5 ms against 0.3 ms for a kept one on the dev box.
    # The hot paths alone issue about 600 distinct statements, and at the
    # driver's default of 100 a third of their executions missed; at 300 one
    # in 45 does. A kept statement costs the server about 40 KB per connection.
    db_statement_cache_size: int = 300
    # How many uvicorn worker processes this container runs. The two numbers
    # above are the CONTAINER's budget and the workers share it; the image
    # passes this same variable to uvicorn's `--workers` (see the split below).
    backend_workers: int = 1
    # Which part of the backend this process plays (`app.core.process_role`).
    # `realtime` is the one process that holds the running work: turns,
    # sessions, room sockets, the owner lock and the jobs behind it. `api` is a
    # pool process beside it that answers the plain reads app-router sends it
    # and nothing else: it never takes the owner lock, and what it publishes
    # reaches the realtime process's broker through Valkey.
    backend_role: Literal["realtime", "api"] = "realtime"
    # Hand out a connection only after checking it is still alive: a pooled
    # asyncpg connection that the database (or anything in between) closed while
    # idle otherwise fails the first statement of whoever checks it out next.
    db_pool_pre_ping: bool = True

    def db_connections_per_worker(self) -> tuple[int, int]:
        """One worker's share of this container's pool budget: (size, overflow).

        A realtime worker also keeps the owner-lock connection outside its pool
        (`core.ownership`), so its share leaves one connection for it and the
        workers together hold what one process held before: the arithmetic in
        `tests/unit/test_db_pool_fits_the_server.py` still describes the
        container. A pool worker never takes that lock
        (`core.process_role`), so its share spends the whole budget — the 4
        and 2 per worker `serve.pool_env` promises by default. At 1 worker it
        is exactly `db_pool_size` and `db_max_overflow` either way.
        """
        # The realtime budget holds up to (db_pool_size + db_max_overflow + 1) // 2
        # workers — 18 at the defaults, a share down to one pooled connection
        # plus the lock. Past that the box has to raise `db_pool_size`.
        workers = max(1, self.backend_workers)
        lock = 0 if self.backend_role == "api" else 1
        budget = max(
            0, (self.db_pool_size + self.db_max_overflow + lock) // workers - lock
        )
        pool_size = max(1, min(self.db_pool_size // workers, budget))
        return pool_size, max(0, budget - pool_size)
