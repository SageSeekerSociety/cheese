"""The pool ceiling is per process; the budget it spends is the server's.

A release runs three pools against one PostgreSQL at the same time — the
outgoing backend, the incoming one the rollout brings up beside it, and the
separately released connection owner, which the compose file gives a smaller
pool of its own. Nothing in the app can see `max_connections`, so the only
place this can be got wrong quietly is here.
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


def _owner_pool() -> int:
    """What the compose file hands the connection owner, read as a deploy would."""
    text = COMPOSE.read_text()
    service = text.split("  device-connection:")[1].split("\n  backend:")[0]
    values = dict(re.findall(r"- (DB_POOL_SIZE|DB_MAX_OVERFLOW)=(\d+)", service))
    return int(values["DB_POOL_SIZE"]) + int(values["DB_MAX_OVERFLOW"])


def test_three_pools_fit_in_one_default_postgresql():
    settings = Settings()
    per_backend = settings.db_pool_size + settings.db_max_overflow
    demanded = (
        per_backend * BACKENDS_DURING_A_ROLLOUT + _owner_pool() + RESERVED_FOR_OPS
    )
    available = SERVER_CONNECTIONS - SUPERUSER_RESERVE
    assert demanded <= available, (
        f"a backend may hold {per_backend} connections and the owner "
        f"{_owner_pool()}, so a release asks for {demanded} of the {available} a "
        f"default server has. On 2026-09-16 that arithmetic refused 83 "
        f"connections in one minute, 19 seconds after a rollout's new backend "
        f"came up."
    )


def test_a_pool_still_has_room_to_burst():
    """Guarding the ceiling must not shrink it to where ordinary load waits."""
    settings = Settings()
    assert settings.db_max_overflow > 0, "no burst headroom at all"
    assert settings.db_pool_size >= 10, "steady-state pool too small for a page load"
