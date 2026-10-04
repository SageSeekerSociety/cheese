"""A session left idle is let go even though a backend keeps reading it, and
the next message, to a runner started again on the same state directory,
reaches a model that still has the conversation.

Each case runs the real, pinned harness against a scripted model: pi through
its runner and the platform extension, Codex through its runner with its tools
on a real executor. Claude Code's case is in ``test_claude_runner.py``.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.domain.agent.harness.codex.runner import Runner as CodexRunner
from app.domain.agent.harness.codex.tools import RemoteTools
from app.domain.agent.harness.driven.runner import SessionStart, reply_owed_path
from app.domain.agent.harness.pi.launch import arguments, extension, provider
from app.domain.agent.harness.pi.runner import Runner as PiRunner
from app.domain.agent.harness.prompt import PLATFORM_NOTICE
from tests.pinned_claude import claude_binary, codex_binary, pi_binary
from tests.support import executor_release
from tests.support.completions_fixture import Completions
from tests.support.responses_fixture import Responses
from tests.support.room_machine import NO_MACHINE
from tests.unit.test_a_person_is_answered_before_anything_else import CHEESE, Backend

FIRST = "The password is PINEAPPLE."
SECOND = "What was the password?"
IDLE_S = 0.5


def _restore(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


async def _until(check, timeout: float = 90.0) -> None:
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.2)


async def _let_go_while_read(runner, read: str) -> None:
    """Wait out the idle window with a backend reading all the while."""

    async def backend():
        while True:
            await runner.dispatch(read, {})
            await asyncio.sleep(0.1)

    reading = asyncio.create_task(backend())
    try:
        assert runner.process is not None
        await asyncio.wait_for(runner.process.wait(), 60)
    finally:
        reading.cancel()
        await asyncio.gather(reading, return_exceptions=True)


# --- pi ------------------------------------------------------------------------


@asynccontextmanager
async def _pi_machine(tmp_path: Path):
    backend = Backend()
    model = Completions([])
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "cheese").symlink_to(CHEESE)
    config = tmp_path / "pi-config"
    config.mkdir()
    (config / "models.json").write_text(provider(model.url, "fixture-model"))
    machine = tmp_path / "work"
    machine.mkdir()
    env = {
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "PI_CODING_AGENT_DIR": str(config),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        **backend.room_env(),
    }
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)

    async def start(**options) -> PiRunner:
        runner = PiRunner(tmp_path / "state", **options)
        await runner.start(
            SessionStart(
                system_prompt="FIXTURE", model="fixture-model", agent_handle="cheese"
            ),
            binary=pi_binary(),
            cwd=str(machine),
            env=env,
            args=arguments("fixture-model"),
            target=NO_MACHINE,
            extension=extension(),
            notice=PLATFORM_NOTICE,
        )
        return runner

    try:
        yield start, model
    finally:
        _restore(saved)
        model.close()
        backend.close()


async def _pi_turn(runner: PiRunner, model: Completions, text: str, asked: int) -> None:
    await runner.dispatch(
        "send", {"input_id": str(uuid.uuid4()), "work_id": "work", "text": text}
    )

    async def answered():
        status = await runner.dispatch("ping", {})
        return len(model.requests) >= asked and not status["working"]

    await _until(answered)


@pytest.mark.anyio
async def test_pi_comes_back_with_its_conversation_after_an_idle_exit(tmp_path):
    async with _pi_machine(tmp_path) as (start, model):
        runner = await start(idle_exit_s=IDLE_S)
        try:
            await _pi_turn(runner, model, FIRST, 1)
            await _let_go_while_read(runner, "entries")
        finally:
            await runner.close()

        again = await start()
        try:
            await _pi_turn(again, model, SECOND, 2)
        finally:
            await again.close()
    asked = json.dumps(model.requests[-1]["messages"], ensure_ascii=False)
    assert FIRST in asked
    assert SECOND in asked


# --- Codex ---------------------------------------------------------------------


@asynccontextmanager
async def _codex_machine(tmp_path: Path):
    home = tmp_path / "executor-home"
    helper = executor_release.install(home / ".cheese")
    executor_state = home / ".cheese/executor"
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        [sys.executable, str(helper), "start", "--state", str(executor_state)],
        input=json.dumps(
            {
                "workspace": str(project),
                "claude": claude_binary(),
                "env": {},
                "mcp_servers": {},
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    backend = Backend(executor_state)
    model = Responses([])
    state = tmp_path / "runner"
    codex_home = state / "codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(model.config())
    workspace = state / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    (state / "home").mkdir()
    env = {
        "PATH": os.environ["PATH"],
        **backend.room_env(),
        "HOME": str(state / "home"),
        "CODEX_HOME": str(codex_home),
    }
    target = {
        "kind": "device",
        "url": backend.url + "/execution",
        "workspace": str(project),
    }
    # The runner reaches its executor with its own environment, as on a machine.
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)

    async def start(**options) -> CodexRunner:
        tools = RemoteTools(
            target,
            mirror=state / "project-skills",
            reply_file=reply_owed_path(state),
            shipped=state / "platform-skills",
        )
        runner = CodexRunner(state, tools, skills=tools, **options)
        schemas = await tools.discover()
        tools.main_thread = await runner.start(
            SessionStart(system_prompt="FIXTURE"),
            binary=codex_binary(),
            cwd=str(workspace),
            env=env,
            tools=schemas,
        )
        return runner

    try:
        yield start, model
    finally:
        _restore(saved)
        subprocess.run(
            [sys.executable, str(helper), "stop", "--state", str(executor_state)],
            capture_output=True,
            timeout=30,
        )
        model.close()
        backend.close()


async def _codex_turn(
    runner: CodexRunner, model: Responses, text: str, asked: int
) -> None:
    await runner.dispatch(
        "send", {"input_id": str(uuid.uuid4()), "work_id": "work", "text": text}
    )

    async def answered():
        status = await runner.dispatch("ping", {})
        return len(model.requests) >= asked and not status["turn_id"]

    await _until(answered)


@pytest.mark.anyio
async def test_codex_comes_back_with_its_conversation_after_an_idle_exit(tmp_path):
    async with _codex_machine(tmp_path) as (start, model):
        runner = await start(idle_exit_s=IDLE_S)
        try:
            await _codex_turn(runner, model, FIRST, 1)
            await _let_go_while_read(runner, "events")
        finally:
            await runner.close()

        again = await start()
        try:
            await _codex_turn(again, model, SECOND, 2)
        finally:
            await again.close()
    asked = json.dumps(model.requests[-1]["input"], ensure_ascii=False)
    assert FIRST in asked
    assert SECOND in asked
