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
    live: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        for method in getattr(route, "methods", None) or ():
            if path and method not in {"HEAD", "OPTIONS"}:
                live.add((method, path))
    return live


@pytest.mark.parametrize(("method", "path"), PARKED)
def test_a_parked_bypass_turn_has_no_route(method: str, path: str) -> None:
    assert (method, path) not in _mounted(), (
        f"{method} {path} is mounted again. These three were parked on "
        "2026-08-12 — read docs/agent-principles.md §12 before restoring one, "
        "and bring a design that does not give the turn its own session."
    )


@pytest.mark.anyio
async def test_a_scheduler_round_no_longer_wakes_the_agent() -> None:
    """The runner wiring stays; the round it drives must wake nobody.

    Kept separate from the route guard because the timer, not the route, is what
    actually fired in production — the endpoint was barely used and the clock ran
    against every project.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.domain.scheduler.service import SchedulerService

    chat = SimpleNamespace(session_factory=None, run_heartbeat=AsyncMock())
    result = await SchedulerService(chat_service=chat).tick()

    chat.run_heartbeat.assert_not_awaited()
    assert result["projects_inspected"] == 0
