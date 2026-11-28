"""The platform's log says, of each Claude Code launch, whether it went on with
a conversation or began one.

Only a launch that begins a conversation could be served by a Claude Code
started ahead of it and claimed when the message comes (`--await-claim`): a
claimed process cannot take up a transcript. Whether to build that turns on how
many launches a person waits on are of that kind, and only the runner knows: it
decides on the machine, from the transcripts there, and a conversation the
platform offered and the machine no longer holds begins afresh without anyone
else hearing of it (`test_resume_across_screens`).

These drive the real runner with a stand-in `claude`, and read it the way the
platform does: the greeting's ping, then the reading that logs the launch once
Claude Code is ready.
"""

import asyncio
import logging
import shlex
import sys
import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.harness import SessionRef as SeatRef
from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.driven import lifecycle, reporting
from app.domain.agent.session_host.claude_code import ClaudeCodeDriver
from app.domain.agent.session_host.contract import (
    Access,
    Owner,
    SessionRef,
    SessionSpec,
)

pytestmark = pytest.mark.anyio

SESSION_ID = "9f1c0d3e-2b4a-4c6e-8d10-7a5b3c9e1f20"

# Reports `init` and holds the session until stdin closes.
STAND_IN = """\
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "x"}), flush=True)
sys.stdin.read()
"""


def _transcript(config):
    slug = config / "projects/-home-agent-work"
    slug.mkdir(parents=True, exist_ok=True)
    (slug / f"{SESSION_ID}.jsonl").write_text('{"type":"user"}\n')


async def _launch_logged(tmp_path, caplog, *, resume, held) -> tuple[dict, str]:
    """Start the runner on what the platform offered (``resume``), with the
    machine holding that conversation's transcript or not (``held``). Returns
    the greeting's ping and the line the platform logs once the session is
    ready."""
    config = tmp_path / "home/.claude"
    config.mkdir(parents=True)
    if held:
        _transcript(config)
    stand_in = tmp_path / "claude.py"
    stand_in.write_text(STAND_IN)
    runner = Runner(tmp_path / "state")
    await runner.start(
        command=f"{shlex.quote(sys.executable)} {shlex.quote(str(stand_in))}",
        env={"PATH": "/usr/bin:/bin", "CLAUDE_CONFIG_DIR": str(config)},
        resume=resume,
        agent_handle="agent",
    )
    try:
        async with asyncio.timeout(10):
            while not runner.proven:
                await asyncio.sleep(0.02)
        ping = await runner.dispatch("ping", {})
        await runner.dispatch("register", {"epoch": 1})

        async def nothing(*args, **kwargs) -> None:
            return None

        reading = reporting.Reading(
            reporting.Whose(
                SeatRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code"),
                "cheese-a",
                uuid.uuid4(),
                "cheese-a",
                "claude-code",
                "Claude Code",
            ),
            runner.dispatch,
            epoch=lambda: 1,
            assemble=lambda record: [],
            show=nothing,
            hand=nothing,
            records=None,
            controls=nothing,
        )
        with caplog.at_level(logging.INFO):
            await reading.drain()
    finally:
        await runner.close()
    [line] = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("session_launch_timing")
    ]
    return ping, line


@pytest.mark.parametrize(
    ("resume", "held", "start"),
    [
        # The machine holds the conversation offered: it goes on.
        (SESSION_ID, True, "resume"),
        # Offered, but this machine has no transcript of it: a new one.
        (SESSION_ID, False, "fresh"),
        # Nothing offered: a seat's first conversation.
        (None, False, "fresh"),
    ],
)
async def test_the_ready_launch_is_logged_as_resumed_or_fresh(
    tmp_path, caplog, resume, held, start
):
    ping, line = await _launch_logged(tmp_path, caplog, resume=resume, held=held)

    assert ping["launch_start"] == start
    assert "stage=ready" in line
    # The launch the greeting named, so the ready line joins its greeting and
    # the turn that started it (`count-fresh-cold-launches.py`).
    assert f" launch={ping['launch']} " in line
    assert f"start={start} " in line
    # The timing it is logged with still names the phase a spare would save.
    assert "'claude_init'" in line


class _Host:
    """One seat's screen whose runner answers the greeting with ``told``."""

    def __init__(self, told: dict):
        self.told = told

    async def _ensure_screen(self, **_):
        return SimpleNamespace(sid="screen")

    async def _retire_screen(self, screen, **_):
        raise AssertionError("nothing here is retired")

    async def call(self, host, ref, method, params, **_):
        assert method == "ping"
        return {
            "alive": True,
            "session_id": SESSION_ID,
            "working": False,
            "lifecycle": lifecycle.PROTOCOL,
            **self.told,
        }


@pytest.mark.parametrize(
    ("told", "start"),
    [
        ({"launch_start": "fresh"}, "fresh"),
        ({"launch_start": "resume"}, "resume"),
        # A runner from before it said so.
        ({}, "unknown"),
        ({"launch_start": ["fresh"]}, "unknown"),
    ],
)
async def test_the_greeted_launch_is_logged_with_what_its_runner_said(
    caplog, told, start
):
    host = _Host(
        {"launch": "L1", "launch_timing": {"started": 0.0, "greeted": 812.5}, **told}
    )
    with caplog.at_level(logging.INFO):
        await ClaudeCodeDriver(screens=host).launch(  # type: ignore[arg-type]
            host,  # type: ignore[arg-type]
            "host",
            SessionRef("claude-code", "state"),
            SessionSpec(system_prompt="", model="m"),
            Access(
                "credential",
                owner=Owner(
                    uuid.uuid4(), uuid.uuid4(), "resource", "cheese", "cheese", 7
                ),
            ),
            None,
        )
    [line] = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("session_launch_timing")
    ]
    assert "stage=greeted" in line
    assert f"start={start} " in line
    # The runner's name for this launch, which its ready line and the turn
    # that started it carry too.
    assert " launch=L1 " in line
