"""A container's connection budget is the container's, however it is served.

`db_pool_size` / `db_max_overflow` are what one backend spends on the server
(the comment over them in `app/core/config.py` holds the arithmetic).
`--workers` turns that one process into several, each a process with a pool of
its own, so `Settings.db_connections_per_worker` hands each worker its share and
the container asks the server for the same number of connections either way.
`test_db_pool_fits_the_server.py` adds the two backends of a rollout up against
that number; this file holds the division that keeps it true.

The other half of the same contract: the process count and the division have to
read ONE variable. A CMD hard-coding `--workers 4` while the app divides by its
default of 1 asks a 97-connection server for 4 x 36 — the 2026-09-16 failure —
so the two places that decide it are checked against each other here.
"""

import re
from pathlib import Path

import pytest

from app import serve
from app.core.config import Settings

BACKEND = Path(__file__).resolve().parents[2]
DOCKERFILE = BACKEND / "Dockerfile"
COMPOSE = BACKEND.parent / "deploy/compose/docker-compose.base.yml"

# The share stops being a pool here: two connections each, one in the pool and
# one for the owner lock (`app/core/ownership.py`).
MOST_WORKERS_THE_BUDGET_HOLDS = 18


def test_one_worker_gets_exactly_the_configured_pool():
    settings = Settings(backend_workers=1)
    assert settings.db_connections_per_worker() == (
        settings.db_pool_size,
        settings.db_max_overflow,
    )


@pytest.mark.parametrize("workers", [2, 3, 4, 8, 9, 18])
def test_an_api_pool_worker_spends_the_whole_budget_it_is_given(workers):
    """A pool worker takes no owner lock, so nothing is held back for one: the
    pool asks the server for the budget it was given, divided among its
    workers — the 4 and 2 per worker `serve.pool_env` promises by default."""
    settings = Settings(
        backend_role="api",
        backend_workers=workers,
        db_pool_size=4 * workers,
        db_max_overflow=2 * workers,
    )
    assert settings.db_connections_per_worker() == (4, 2)


@pytest.mark.parametrize("workers", [1, 2, 3, 4, 5, 6, 8, 9, 12, 18])
def test_the_container_never_asks_the_server_for_more_than_one_process_did(workers):
    settings = Settings(backend_workers=workers)
    pool_size, max_overflow = settings.db_connections_per_worker()
    per_worker = pool_size + max_overflow + 1
    one_process_held = settings.db_pool_size + settings.db_max_overflow + 1
    assert workers * per_worker <= one_process_held, (
        f"{workers} workers holding {per_worker} connections each ask the server "
        f"for {workers * per_worker} where one process asked for {one_process_held}"
    )


def test_the_budget_holds_at_the_worker_count_it_stops_holding_above():
    """The parametrised list above is a claim about where the share runs out."""
    settings = Settings(backend_workers=MOST_WORKERS_THE_BUDGET_HOLDS + 1)
    pool_size, max_overflow = settings.db_connections_per_worker()
    beyond = (MOST_WORKERS_THE_BUDGET_HOLDS + 1) * (pool_size + max_overflow + 1)
    assert beyond > settings.db_pool_size + settings.db_max_overflow + 1


@pytest.mark.parametrize("workers", [2, 4, 8])
def test_a_worker_still_has_a_pool_and_burst_headroom(workers):
    """Dividing must not hand a worker a pool it cannot serve requests from."""
    pool_size, _ = Settings(backend_workers=workers).db_connections_per_worker()
    assert pool_size >= 1


def test_the_image_and_the_app_take_the_worker_count_from_one_variable(monkeypatch):
    # The `development` stage's CMD is `uv run uvicorn --reload`, one process by
    # construction; the deploy runs the `production` stage, whose command is
    # built by `app/serve.py`.
    text = DOCKERFILE.read_text().split("AS production")[1]
    command = re.search(r"^CMD .*$", text, re.MULTILINE)
    assert command is not None and "app.serve" in command.group(0)
    monkeypatch.setenv("BACKEND_WORKERS", "3")
    argv = serve.realtime_argv()
    assert argv[argv.index("--workers") + 1] == "3", (
        "the image's --workers does not come from BACKEND_WORKERS, so it can "
        "disagree with the pool division"
    )
    # The api pool is its own set of workers with its own budget, and the app
    # in each of them divides that budget by the pool's own count.
    env = serve.pool_env(4)
    pool = serve.pool_argv(4)
    assert env["BACKEND_WORKERS"] == pool[pool.index("--workers") + 1] == "4"
    assert env["BACKEND_ROLE"] == "api"


def test_the_deploy_can_raise_the_worker_count_without_a_rebuild():
    text = COMPOSE.read_text()
    backend = text.split("\n  backend:")[1].split("\n  frontend:")[0]
    assert re.search(r"^\s+- BACKEND_WORKERS=", backend, re.MULTILINE), (
        "the backend service does not pass BACKEND_WORKERS through"
    )


@pytest.mark.parametrize("owner", ["device-connection", "preview-connection"])
def test_a_standing_owner_is_pinned_to_one_worker(owner):
    """Both owners keep their pool for the life of the box, and both serve one
    process; a shared env file raising the backend's worker count must not
    quietly divide an owner's pool instead."""
    text = COMPOSE.read_text()
    section = text.split(f"\n  {owner}:")[1].split("\n\n  ")[0]
    assert re.search(r"^\s+- BACKEND_WORKERS=1$", section, re.MULTILINE), (
        f"{owner} does not pin BACKEND_WORKERS to 1"
    )
