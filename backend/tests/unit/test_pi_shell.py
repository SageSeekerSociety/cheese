"""pi's shell on the room's machine starts where every harness's does: in the
user's shell, from the snapshot the executor takes of their profile. What a
Codex room's command finds there, a pi room's finds too."""

import asyncio
import base64
import importlib.util
import uuid
from pathlib import Path

from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.runner import Runner
from tests.pinned_claude import claude_binary
from tests.support.room_machine import RUNTIME, room_machine
from tests.unit.test_pi_runner import call, shim

COMMAND = "greet; type greet >/dev/null && echo bash=${BASH_VERSION:+yes}"


def _runtime():
    spec = importlib.util.spec_from_file_location("pi_shell_runtime", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _home(tmp_path: Path) -> Path:
    """A user whose profile sets up a command, as nvm or conda would."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("greet() { echo function-from-profile; }\n")
    return home


async def _pi_runs(runner: Runner, command: str) -> str:
    started = await call(
        runner.state, "shell", {"operation": "start", "command": command}
    )
    output, offset = b"", 0
    for _ in range(60):
        read = await call(
            runner.state,
            "shell",
            {"operation": "read", "id": started["id"], "offset": offset, "wait": 1},
        )
        output += base64.b64decode(read["data"])
        offset = read["offset"]
        if "exit" in read:
            return output.decode()
    raise AssertionError(output)


def test_pi_runs_a_command_in_the_users_shell_as_codex_does(tmp_path):
    async def session():
        runner = Runner(tmp_path / "state")
        with room_machine(
            tmp_path / "machine", home=_home(tmp_path), claude=claude_binary()
        ) as target:
            await runner.start(
                SessionStart("system prompt", None, agent_handle="teammate"),
                binary=shim(tmp_path),
                cwd=str(tmp_path),
                env={"PATH": "/usr/bin:/bin"},
                args=[],
                target=target,
            )
            try:
                pi = await _pi_runs(runner, COMMAND)
            finally:
                await runner.close()
            # Codex's commands are the executor's own Bash.
            codex = await asyncio.to_thread(
                _runtime().request,
                Path(target["state"]),
                "invoke",
                {"id": str(uuid.uuid4()), "tool": "Bash", "args": {"command": COMMAND}},
            )
            return pi, codex["value"]["stdout"]

    pi, codex = asyncio.run(session())
    assert pi.split() == ["function-from-profile", "bash=yes"]
    assert pi.split() == codex.split()
