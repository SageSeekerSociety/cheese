"""A Codex or pi session's `Monitor` is told each line its command prints —
while it works, and between turns, when a line wakes it — as Claude Code's own
Monitor tells a Claude Code session.

Each case runs the real, pinned harness through its runner, with its tools on a
real executor, against a scripted model (the fixtures of
`test_a_person_is_answered_before_anything_else`), and checks what the model
was sent and what ran on the machine.
"""

import asyncio
import time

import pytest

from app.domain.agent.harness.prompt import PLATFORM_NOTICE
from tests.unit.test_a_person_is_answered_before_anything_else import (
    codex,
    monitor,
    pi,
    say,
)

pytestmark = pytest.mark.anyio

every_harness = pytest.mark.parametrize(
    "harness", [codex, pi], ids=lambda harness: harness.__name__
)


async def told_eventually(session, words: str, timeout: float = 60.0) -> int:
    """The first model request that carries `words`, waiting for it."""
    deadline = time.monotonic() + timeout
    while True:
        for index in range(len(session.requests)):
            if words in session.told(index):
                return index
        assert time.monotonic() < deadline, f"the model was never told {words!r}"
        await asyncio.sleep(0.2)


@every_harness
async def test_a_line_printed_after_the_turn_wakes_the_session(tmp_path, harness):
    steps = [
        monitor(
            # What it prints is spelled differently in the command, which the
            # model is sent too.
            "echo FIRST-$((40+2)); while [ ! -e RELEASE ]; do sleep 0.1; done; "
            "echo LAST-$((40+2))"
        ),
        say("watching it"),
    ]
    async with harness(tmp_path, steps) as session:
        await session.send(f"{PLATFORM_NOTICE}\nwatch the deploy", owes_reply=False)
        await session.finished(len(steps))
        # The call came back at once, with the id to stop it by.
        assert "Monitor started (task monitor-" in session.told(1)
        await told_eventually(session, "FIRST-42")
        await session.finished(len(steps))
        before = len(session.requests)

        # Nothing is running now; the next line starts a turn of its own.
        (session.machine / "RELEASE").touch()
        woken = await told_eventually(session, "LAST-42")
        assert woken >= before
        ended = await told_eventually(session, "stream ended (exit 0)")
        assert ended >= woken


@every_harness
async def test_a_monitor_never_started_by_a_denied_command(tmp_path, harness):
    steps = [monitor("touch MADE"), say("ok")]
    async with harness(tmp_path, steps) as session:
        settings = session.machine / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"permissions": {"deny": ["Bash(touch *)"]}}')
        await session.send(f"{PLATFORM_NOTICE}\nwatch it", owes_reply=False)
        await session.finished(len(steps))
        await asyncio.sleep(1)

        assert "permissions.deny" in session.told(1)
        assert not (session.machine / "MADE").exists()
