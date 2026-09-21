"""The three parked bypass turns must stay unreachable.

线下接入 / 定期巡检 / 一页纸总结 each had the platform do something 芝士 already
does when asked, so each grew its own turn, its own session and its own failure
mode — activity ingestion's fresh session overwrote the topic's session pointer,
which is why 27% of live sessions had no history at all. The need behind them is
real and the redesign has not happened; until it does, nothing may invoke them.

This guards the *absence*: re-mounting a route makes it fail with the reason,
which a deleted test file could not do.
"""

import pytest

from app.main import app

PARKED = [
    ("POST", "/projects/{project_id}/activities"),
    ("POST", "/projects/{project_id}/heartbeat"),
    ("POST", "/projects/{project_id}/summary"),
]


def _mounted() -> set[tuple[str, str]]:
    """What the app actually serves, read off its own OpenAPI schema.

    NOT `app.routes`: FastAPI keeps an included router as one `_IncludedRouter`
    entry whose own `path` is `None`, so walking that list finds none of the
    routes this file is here to watch for — the guard passed no matter what was
    mounted."""
    live: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            live.add((method.upper(), path))
    return live


@pytest.mark.parametrize(("method", "path"), PARKED)
def test_a_parked_bypass_turn_has_no_route(method: str, path: str) -> None:
    assert (method, path) not in _mounted(), (
        f"{method} {path} is mounted again. These three were parked on "
        "2026-08-12 — read docs/agent-principles.md §12 before restoring one, "
        "and bring a design that does not give the turn its own session."
    )
