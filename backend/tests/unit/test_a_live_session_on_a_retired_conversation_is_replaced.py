"""A Claude Code session still running a conversation the platform retired is
started again once it is idle, and goes on as it is while it works.

The platform retires a conversation it gave up on, or one started under rules
its kind has replaced. A runner idles for minutes before it exits, and a screen
running that conversation would otherwise take the next message into it.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.harness.driven import lifecycle
from app.domain.agent.session_host.claude_code import ClaudeCodeDriver
from app.domain.agent.session_host.contract import (
    Access,
    Owner,
    SessionRef,
    SessionSpec,
)

OLD = "conversation-under-the-old-rules"
NEW = "conversation-started-now"


class _Host:
    """One seat's screen on a session host: the conversation its runner is
    on, whether it is working, and the screens that have been opened."""

    def __init__(self, *, working: bool):
        self.conversation = OLD
        self.working = working
        self.opened = 0
        self.closed = 0

    # --- the device channel ---------------------------------------------

    async def _ensure_screen(self, *, launch, runner_alive, **_):
        if self.closed >= self.opened:
            # A screen opened now starts a runner on what its launch offers.
            self.opened += 1
            self.conversation = launch.resume_session_id or NEW
            self.working = False
        return SimpleNamespace(sid=f"screen-{self.opened}")

    async def _retire_screen(self, screen, **_):
        self.closed += 1

    # --- the wire -------------------------------------------------------

    async def call(self, host, ref, method, params, **_):
        assert method == "ping"
        return {
            "alive": True,
            "session_id": self.conversation,
            "working": self.working,
            "lifecycle": lifecycle.PROTOCOL,
        }


def _access() -> Access:
    return Access(
        "credential",
        owner=Owner(uuid.uuid4(), uuid.uuid4(), "resource", "cheese", "cheese", 7),
    )


async def _launch(host: _Host):
    host.opened = 1  # the screen that is already running OLD
    return await ClaudeCodeDriver(screens=host).launch(  # type: ignore[arg-type]
        host,  # type: ignore[arg-type]
        "host",
        SessionRef("claude-code", "state"),
        SessionSpec(system_prompt="", model="m", retired_token=OLD),
        _access(),
        None,
    )


@pytest.mark.anyio
async def test_an_idle_session_on_a_retired_conversation_starts_a_new_one():
    host = _Host(working=False)
    launched = await _launch(host)
    assert launched.conversation == NEW


@pytest.mark.anyio
async def test_a_working_session_on_a_retired_conversation_is_left_to_finish():
    host = _Host(working=True)
    launched = await _launch(host)
    assert launched.conversation == OLD
