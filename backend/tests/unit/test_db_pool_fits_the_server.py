"""The pool ceiling is per process; the budget it spends is the server's.

A release runs three pools against one PostgreSQL at the same time — the
outgoing backend, the incoming one the rollout brings up beside it, and the
separately released connection owner. Nothing in the app can see
`max_connections`, so the only place this can be got wrong quietly is here.
"""

from app.core.config import Settings

# A default PostgreSQL offers 100 connections and reserves 3 of them for
# superusers, which is what the deployment runs.
SERVER_CONNECTIONS = 100
SUPERUSER_RESERVE = 3

# Alive at once during an ordinary release.
POOLS_DURING_A_ROLLOUT = 3

# The deploy's `alembic upgrade head` needs one, and an operator holding a psql
# must not be the thing that tips it over.
RESERVED_FOR_OPS = 10


def test_three_pools_fit_in_one_default_postgresql():
    settings = Settings()
    per_process = settings.db_pool_size + settings.db_max_overflow
    demanded = per_process * POOLS_DURING_A_ROLLOUT + RESERVED_FOR_OPS
    available = SERVER_CONNECTIONS - SUPERUSER_RESERVE
    assert demanded <= available, (
        f"one process may hold {per_process} connections, so a release asks for "
        f"{demanded} of the {available} a default server has. On 2026-09-16 that "
        f"arithmetic refused 83 connections in one minute, 19 seconds after a "
        f"rollout's new backend came up."
    )


def test_a_pool_still_has_room_to_burst():
    """Guarding the ceiling must not shrink it to where ordinary load waits."""
    settings = Settings()
    assert settings.db_max_overflow > 0, "no burst headroom at all"
    assert settings.db_pool_size >= 10, "steady-state pool too small for a page load"
