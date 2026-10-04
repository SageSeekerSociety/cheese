"""The pool ceiling is per process; the budget it spends is the server's.

A release runs four pools against one PostgreSQL at the same time — the
outgoing backend, the incoming one the rollout brings up beside it, and the two
separately released connection owners (device-connection and preview-connection),
which the compose file gives smaller pools of their own. The owners are started
once and left standing, so their pools are charged to the server for the whole
life of the box, not just for the seconds a rollout overlaps. Nothing in the app
can see `max_connections`, so the only place this can be got wrong quietly is
here.
"""

import re
from pathlib import Path

from app.core.config import Settings

# A default PostgreSQL offers 100 connections and reserves 3 of them for
# superusers, which is what the deployment runs.
SERVER_CONNECTIONS = 100
SUPERUSER_RESERVE = 3

# Backends alive at once during an ordinary release.
BACKENDS_DURING_A_ROLLOUT = 2

# The deploy's `alembic upgrade head` needs one, and an operator holding a psql
# must not be the thing that tips it over.
RESERVED_FOR_OPS = 10

COMPOSE = Path(__file__).resolve().parents[3] / "deploy/compose/docker-compose.base.yml"


def _owner_pools() -> int:
    """What the compose file hands EVERY owner, read as a deploy would.

    The owners are the services declared before `backend:` (device-connection
    and preview-connection). Each keeps its pool for the life of the process, so
    the server is charged for all of them at once — summing rather than taking
    the last one is the whole point of this test.
    """
    text = COMPOSE.read_text()
    owners = text.split("  device-connection:")[1].split("\n  backend:")[0]
    values = re.findall(r"- (?:DB_POOL_SIZE|DB_MAX_OVERFLOW)=(\d+)", owners)
    return sum(int(value) for value in values)


def test_all_pools_fit_in_one_default_postgresql():
    settings = Settings()
    # Plus the one connection outside the pool that holds the owner lock
    # (`app.core.ownership`), for as long as the process lives.
    per_backend = settings.db_pool_size + settings.db_max_overflow + 1
    demanded = (
        per_backend * BACKENDS_DURING_A_ROLLOUT + _owner_pools() + RESERVED_FOR_OPS
    )
    available = SERVER_CONNECTIONS - SUPERUSER_RESERVE
    assert demanded <= available, (
        f"a backend may hold {per_backend} connections and the standing owners "
        f"{_owner_pools()}, so a release asks for {demanded} of the {available} a "
        f"default server has. On 2026-09-16 that arithmetic refused 83 "
        f"connections in one minute, 19 seconds after a rollout's new backend "
        f"came up."
    )


def test_a_pool_still_has_room_to_burst():
    """Guarding the ceiling must not shrink it to where ordinary load waits."""
    settings = Settings()
    assert settings.db_max_overflow > 0, "no burst headroom at all"
    assert settings.db_pool_size >= 10, "steady-state pool too small for a page load"
