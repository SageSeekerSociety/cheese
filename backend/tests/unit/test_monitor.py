"""`Monitor` for the harnesses without one: a command on the room's machine whose
every stdout line the session is told, as Claude Code 2.1.282 tells it.

Driven against a real executor standing for the room's machine
(`tests/support/room_machine.py`); what is asserted is what the session hears
and what happens to the command on the machine.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest

from app.domain.agent.executor_transport import RemoteClient
from app.domain.agent.harness.driven import monitor
from app.domain.agent.harness.driven.monitor import Monitors
from tests.support.room_machine import room_machine

pytestmark = pytest.mark.anyio


async def until(check, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.05)


class Session:
    """Everything the monitors said, in order."""

    def __init__(self) -> None:
        self.told: list[str] = []

    async def tell(self, text: str) -> None:
        self.told.append(text)

    def events(self) -> list[str]:
        return [
            text.split("<event>", 1)[1].split("</event>", 1)[0]
            for text in self.told
            if "<event>" in text
        ]

    def said(self, words: str) -> bool:
        return any(words in text for text in self.told)


def monitors(target: dict, session: Session) -> Monitors:
    client = RemoteClient(dict(target))
    return Monitors(client.control, session.tell, cwd=lambda: target["workspace"])


@pytest.mark.parametrize("chunk_size", [16_384, 2_000_000])
def test_a_long_output_line_keeps_its_beginning_and_the_next_event(chunk_size):
    """Truncation preserves a line's beginning regardless of read boundaries."""
    text = "FAILED: " + "x" * 1_100_000
    data = (text + "\nnext event\n").encode()
    lines = monitor.Lines()
    received = []
    for offset in range(0, len(data), chunk_size):
        received.extend(lines.feed(data[offset : offset + chunk_size]))
    received.extend(lines.rest())

    assert received == [text[: monitor.LINE_MAX] + "...(truncated)", "next event"]


async def test_each_line_is_told_as_it_is_printed_and_the_exit_ends_the_watch(
    tmp_path,
):
    with room_machine(tmp_path / "machine") as target:
        session = Session()
        watching = monitors(target, session)
        try:
            began = time.monotonic()
            answer = await watching.start(
                {
                    "command": "echo first; sleep 1; echo second; "
                    "echo not-an-event >&2; sleep 1; exit 3",
                    "description": "two lines",
                },
                "call-1",
            )
            # The call returns at once; the command goes on.
            assert time.monotonic() - began < 1
            assert "Monitor started (task monitor-" in answer
            # The first line is told while the command is still running.
            await until(lambda: session.events() == ["first"], timeout=5)
            assert watching.count() == 1
            await until(lambda: session.said("script failed (exit 3)"))
            assert session.events() == ["first", "second"]
            assert not session.said("not-an-event")
            assert watching.count() == 0
        finally:
            await watching.close()


async def test_lines_printed_together_are_one_notification(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        session = Session()
        watching = monitors(target, session)
        try:
            await watching.start(
                {
                    "command": "printf 'a\\nb\\nc\\n'; sleep 1; echo d",
                    "description": "x",
                },
                "call-1",
            )
            await until(lambda: session.said("stream ended (exit 0)"))
            assert session.events() == ["a\nb\nc", "d"]
        finally:
            await watching.close()


async def test_a_command_that_prints_nothing_says_so_when_it_ends(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        session = Session()
        watching = monitors(target, session)
        try:
            await watching.start({"command": "true", "description": "quiet"}, "c")
            await until(lambda: session.said("ended without producing output (exit 0)"))
        finally:
            await watching.close()


async def test_the_deadline_kills_the_command_and_says_how_many_events_it_delivered(
    tmp_path,
):
    with room_machine(tmp_path / "machine") as target:
        session = Session()
        watching = monitors(target, session)
        marker = Path(target["workspace"]) / "alive"
        try:
            await watching.start(
                {
                    "command": "while true; do date +%s%N > alive; sleep 0.2; done",
                    "description": "forever",
                    "timeout_ms": 1500,
                },
                "c",
            )
            await until(lambda: session.said("Monitor expired after 2s"), timeout=10)
            assert session.said("with no events delivered")
            await until(lambda: watching.count() == 0, timeout=10)
            stamp = marker.read_text()
            await asyncio.sleep(1)
            assert marker.read_text() == stamp, "the command outlived its deadline"
        finally:
            await watching.close()


async def test_task_stop_ends_the_command_and_nothing_more_is_said(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        session = Session()
        watching = monitors(target, session)
        marker = Path(target["workspace"]) / "alive"
        try:
            answer = await watching.start(
                {
                    "command": "echo up; while true; do date +%s%N > alive; "
                    "sleep 0.2; done",
                    "description": "a server",
                },
                "c",
            )
            task = answer.split("task ", 1)[1].split(",", 1)[0]
            await until(lambda: session.events() == ["up"], timeout=5)
            assert "stopped" in await watching.stop(task)
            await until(lambda: watching.count() == 0, timeout=10)
            stamp = marker.read_text()
            await asyncio.sleep(1)
            assert marker.read_text() == stamp
            assert len(session.told) == 1
            assert "No running monitor" in await watching.stop(task)
        finally:
            await watching.close()


async def test_expiry_kills_a_command_that_ignores_termination(tmp_path):
    """A monitor's deadline also stops a script that ignores SIGTERM."""
    with room_machine(tmp_path / "machine") as target:
        session = Session()
        watching = monitors(target, session)
        client = RemoteClient(dict(target))
        try:
            answer = await watching.start(
                {
                    "command": "trap '' TERM; echo ready; while true; do sleep 1; done",
                    "description": "ignores termination",
                    "timeout_ms": 1000,
                },
                "c",
            )
            task = answer.split("task ", 1)[1].split(",", 1)[0]
            await until(lambda: session.events() == ["ready"], timeout=2)
            await until(lambda: session.said("expired"), timeout=2)
            await asyncio.gather(*watching.chores)

            def killed():
                result = client.control(
                    {"subtype": "shell", "operation": "read", "command_id": task}
                )
                return result.get("exit") == -9

            await until(killed, timeout=1)
        finally:
            await watching.close()


async def test_the_projects_bash_rules_hold_for_its_command(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        settings = Path(target["workspace"]) / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"permissions": {"deny": ["Bash(touch *)"]}}))
        session = Session()
        watching = monitors(target, session)
        try:
            with pytest.raises(ValueError, match="permissions.deny"):
                await watching.start(
                    {"command": "touch made", "description": "denied"}, "c"
                )
            await asyncio.sleep(0.5)
            assert not (Path(target["workspace"]) / "made").exists()
            assert watching.count() == 0
        finally:
            await watching.close()


async def test_a_flood_is_held_back_and_then_stopped(tmp_path, monkeypatch):
    # Claude Code's bucket refills a notification every 2 s and stops a flood
    # after 30 s; the rule is the same at a scale a test can wait for.
    monkeypatch.setattr(monitor, "BURST", 2)
    monkeypatch.setattr(monitor, "REFILL_S", 60.0)
    monkeypatch.setattr(monitor, "FLOOD_S", 1.0)
    with room_machine(tmp_path / "machine") as target:
        session = Session()
        watching = monitors(target, session)
        marker = Path(target["workspace"]) / "alive"
        try:
            await watching.start(
                {
                    "command": "while true; do echo tick; date +%s%N > alive; "
                    "sleep 0.3; done",
                    "description": "noisy",
                },
                "c",
            )
            await until(lambda: session.said("too much output"), timeout=10)
            assert session.events()[:2] == ["tick", "tick"]
            await until(lambda: watching.count() == 0, timeout=10)
            stamp = marker.read_text()
            await asyncio.sleep(1)
            assert marker.read_text() == stamp
        finally:
            await watching.close()


async def test_a_deadline_beyond_the_longest_is_capped(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        session = Session()
        watching = monitors(target, session)
        try:
            answer = await watching.start(
                {"command": "sleep 30", "description": "x", "timeout_ms": 10**9},
                "c",
            )
            assert "expires in 30m" in answer
            answer = await watching.start(
                {"command": "sleep 30", "description": "y"}, "d"
            )
            assert "expires in 5m" in answer
        finally:
            await watching.close()
