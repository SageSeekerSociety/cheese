"""A failed step's error is stored scrubbed, like the step's output.

The error text of a failed command is shown in the room and read back by
transcript tooling; a credential on the command line comes back in it.
"""

import uuid
from contextlib import asynccontextmanager

import pytest

from app.domain.agent.turn.store import events


class _Session:
    async def commit(self):
        return None


@asynccontextmanager
async def _sessions():
    yield _Session()


@pytest.mark.anyio
async def test_a_failed_steps_error_is_stored_without_the_token(monkeypatch):
    stored = {}

    async def fake_fail_step(session, block_id, error):
        stored["error"] = error
        return None

    monkeypatch.setattr(events, "fail_step", fake_fail_step)
    await events._mark_step_failed(
        _sessions,
        uuid.uuid4(),
        "push failed: https://x:ghp_abcdefgh12345@github.com/o/r",
    )
    assert "ghp_" not in stored["error"]
    assert "push failed" in stored["error"]
