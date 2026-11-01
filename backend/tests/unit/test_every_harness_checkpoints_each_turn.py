"""Every harness ends a turn with its work in the project.

Claude Code does it from the Stop hook the platform installs; a driven harness
(pi, Codex) has its runner ask the room's machine for the same checkpoint when a
turn ends. Here the machine is a real executor whose `cheese-sync` is the
platform's own script running the real CLI, and the task's work is a commit that
was never pushed: after the turn, that commit is on the forge's task branch. A
file nobody committed stays on the machine, so a checkpoint does not upload it.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness.claude_code.remote_execution.launch import (
    CHEESE_SYNC_SCRIPT,
)
from app.domain.agent.harness.codex.runner import Runner as CodexRunner
from app.domain.agent.harness.codex.tools import RemoteTools
from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.runner import Runner as PiRunner
from app.domain.agent.nonce import new_nonce
from tests.support.room_machine import room_machine
from tests.support.task_platform import CLI, TaskPlatform, git, load_cli
from tests.unit.test_pi_runner import FAKE, call


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A room's machine whose task has unpushed work; its execution target,
    the platform, and the work's commit."""
    platform = TaskPlatform(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    worktree = load_cli(platform)._task_worktree(platform.task_id)
    (worktree / "report.txt").write_text("committed this turn\n")
    git(worktree, "commit", "-qam", "Report")
    (worktree / "draft.md").write_text("not committed\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "cheese").write_text(f'#!/bin/sh\nexec {sys.executable} {CLI} "$@"\n')
    (bindir / "cheese-sync").write_text(CHEESE_SYNC_SCRIPT)
    # A command runs as the user's login shell. This runner's `/etc/profile`
    # resets PATH, so the profile here keeps it instead — a room's own machine
    # is one whose profile has the platform on PATH, and the fixture's `bin`
    # is what stands in for that.
    (home / ".bash_profile").write_text(f'export PATH="{bindir}:$PATH"\n')
    for program in bindir.iterdir():
        program.chmod(0o755)
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(home),
        "CHEESE_API": platform.url,
        "CHEESE_PROJECT": platform.project,
        "CHEESE_TOPIC": platform.room,
        "CHEESE_TOKEN": "session-token",
    }
    try:
        with room_machine(tmp_path / "machine", env=env) as target:
            yield (
                {**target, "execution_token": "execution-token"},
                platform,
                git(worktree, "rev-parse", "HEAD"),
            )
    finally:
        platform.close()


async def checkpointed(platform, commit, tmp_path) -> None:
    """The commit is on the task branch, and nothing was uploaded for the file
    nobody committed."""
    async with asyncio.timeout(4):
        while platform.branch_head() != commit:
            await asyncio.sleep(0.1)
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
    assert platform.snapshots == []
    reader = tmp_path / "reader.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(platform.forge), str(reader)], check=True
    )
    assert git(reader, "show", f"{commit}:report.txt") == "committed this turn"


@pytest.mark.anyio
async def test_a_pi_turn_ends_with_its_task_pushed(tmp_path, machine):
    target, platform, commit = machine
    text = f"go {new_nonce()}"
    recording = tmp_path / "turn.json"
    recording.write_text(
        json.dumps(
            {
                "stream": [
                    {
                        "entry": {
                            "type": "message",
                            "id": "e-user",
                            "parentId": None,
                            "timestamp": "2026-10-07T00:00:00.000Z",
                            "message": {
                                "role": "user",
                                "content": [{"type": "text", "text": text}],
                            },
                        }
                    },
                    {"event": {"type": "agent_start"}},
                    {"event": {"type": "agent_settled"}},
                ]
            }
        )
    )
    binary = tmp_path / "pi"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} {recording} "$@"\n')
    binary.chmod(0o700)
    runner = PiRunner(tmp_path / "state")
    await runner.start(
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=str(binary),
        cwd=str(tmp_path),
        env={"PATH": os.environ["PATH"]},
        args=["--no-context-files"],
        target=target,
    )
    try:
        assert platform.branch_head() != commit
        await call(
            runner.state,
            "send",
            {"input_id": str(uuid.uuid4()), "text": text, "work_id": "work"},
        )
        await checkpointed(platform, commit, tmp_path)
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_codex_turn_ends_with_its_task_pushed(tmp_path, machine):
    target, platform, commit = machine
    runner = CodexRunner(tmp_path / "state", AsyncMock())
    # As `codex/entry.py` wires the runner to the room's machine.
    runner.checkpointer = RemoteTools(target).client.checkpoint
    runner.session = SimpleNamespace(
        thread_id="root", turn_id=None, observe=lambda event: None
    )
    try:
        assert platform.branch_head() != commit
        await runner.record(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "root",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        await checkpointed(platform, commit, tmp_path)
    finally:
        await runner.close()
