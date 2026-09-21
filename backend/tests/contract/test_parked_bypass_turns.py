"""The three parked bypass turns must stay unreachable.

线下接入 / 定期巡检 / 一页纸总结 each had the platform do something 芝士 already
does when asked, so each grew its own turn, its own session and its own failure
mode — activity ingestion's fresh session overwrote the topic's session pointer,
which is why 27% of live sessions had no history at all. The need behind them is
real and the redesign has not happened; until it does, nothing may invoke them.

This guards the *absence*: re-mounting a route makes it fail with the reason,
which a deleted test file could not do.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.main import app

PARKED = [
    ("POST", "/projects/{project_id}/activities"),
    ("POST", "/projects/{project_id}/heartbeat"),
    ("POST", "/projects/{project_id}/summary"),
]


@pytest.fixture(scope="module")
def probe() -> TestClient:
    """The app as the gateway hands it a request, with no lifespan run: the
    router answers every probe below before a dependency or a handler does, so
    this needs no database. Same client shape as
    tests/contract/test_api_addressing_contract.py."""
    return TestClient(app)


@pytest.mark.parametrize(("method", "path"), PARKED)
def test_a_parked_bypass_turn_has_no_route(
    probe: TestClient, method: str, path: str
) -> None:
    """Ask the server what it serves, not a schema, and not `app.routes`.

    `app.openapi()` is built from `route.include_in_schema` alone
    (fastapi/openapi/utils.py), and that flag is off on 17 routes under
    `app/api/routes/` — so a schema read calls a hidden remount absent. Walking
    `app.routes` is no better: FastAPI keeps an included router as one entry
    whose own `path` is `None`, so none of these three appear there either way.
    A request is the only reading that covers both.

    404 is what only an unmounted path answers: a route that exists but declines
    the caller answers 401 or 403, one that exists under another verb answers
    405, and one that runs without a database raises out of the client."""
    response = probe.request(method, re.sub(r"\{[^}]+\}", "7", path))
    assert response.status_code == 404, (
        f"{method} {path} answered {response.status_code} — it is mounted "
        "again. These three were parked on 2026-08-12 — read "
        "docs/agent-principles.md §12 before restoring one, and bring a design "
        "that does not give the turn its own session."
    )
