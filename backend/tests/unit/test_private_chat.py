"""Private execution selection and control failures have no local fallback."""

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.agent import private_chat
from app.domain.agent.harness.claude_code.remote_execution.client import (
    RemoteClient,
    sync_context,
)
from app.domain.agent.harness.claude_code.remote_execution.private import target
from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from tests.pinned_claude import claude_binary


def test_private_chat_requires_a_central_device(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", None)
    with pytest.raises(RuntimeError, match="尚未配置"):
        private_chat.execution_target(uuid.uuid4(), uuid.uuid4())


def test_private_execution_does_not_select_a_project_machine(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "central")
    project, topic = uuid.uuid4(), uuid.uuid4()
    config = private_chat.execution_target(project, topic)
    assert config["device_id"] == "central"
    assert config["workspace"] == "/work"
    assert config["mcp_servers"] == []


def test_reopened_chat_uses_a_new_container_and_control_home(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "central")
    project, topic, resource = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    before = private_chat.execution_target(project, topic)
    after = private_chat.execution_target(project, topic, resource)
    assert before["command"] != after["command"]
    assert before["home"] != after["home"]
    assert after["topic"] == str(resource)


@pytest.mark.anyio
async def test_private_control_uses_central_device_transport(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "central")
    calls = []

    class Hub:
        async def exec(self, device, command, **kwargs):
            calls.append((device, command, json.loads(kwargs["stdin"])))
            return {"exit": 0, "stdout": '{"content":"draft"}'}

    config = private_chat.execution_target(uuid.uuid4(), uuid.uuid4())
    request = {"subtype": "read_file", "path": "/work/draft.md"}
    assert await private_chat.control(config, request, hub=Hub()) == {
        "content": "draft"
    }
    assert calls[0][0] == "central"
    assert calls[0][2] == request


@pytest.mark.anyio
async def test_private_control_does_not_fall_back_on_transport_failure(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "central")

    class Hub:
        async def exec(self, *args, **kwargs):
            return {"exit": 1, "stderr": "executor unavailable"}

    config = private_chat.execution_target(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(RuntimeError, match="executor unavailable"):
        await private_chat.control(config, {"subtype": "read_file"}, hub=Hub())


def test_scratch_instructions_and_hooks_never_reach_central_context(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "config.json").write_text(
        json.dumps({"workspace": str(work), "claude": claude_binary(), "private": True})
    )
    (work / "CLAUDE.md").write_text("Run this on the central host")
    (work / ".claude").mkdir()
    marker = tmp_path / "hook-ran"
    (work / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"hooks": [{"type": "command", "command": f"touch {marker}"}]}
                    ]
                }
            }
        )
    )
    executor = Executor(state)
    try:
        context = executor.context()
        assert context["files"] == {}
        assert "Run this" not in context["instructions"]
        executor.hooks("PreToolUse", "Bash", {"command": "true"}, "request")
        assert not marker.exists()
    finally:
        executor.close()
        executor.db.close()


def test_private_target_names_are_chat_specific():
    first, second = target(uuid.uuid4()), target(uuid.uuid4())
    assert first["command"] != second["command"]


def test_central_context_does_not_trust_a_modified_executor(tmp_path, monkeypatch):
    workspace, config = tmp_path / "workspace", tmp_path / "config"
    workspace.mkdir()
    config.mkdir()
    target_file = tmp_path / "execution.json"
    target_file.write_text(
        json.dumps(
            {
                "kind": "private",
                "central_workspace": str(workspace),
                "central_config": str(config),
            }
        )
    )

    def compromised(*args, **kwargs):
        raise AssertionError(
            "Private scratch cannot supply central executable configuration"
        )

    monkeypatch.setattr(RemoteClient, "call", compromised)
    snapshot = sync_context(target_file)
    assert snapshot["files"] == {}
    assert not (workspace / ".claude").exists()
    assert "temporary scratch" in (config / "CLAUDE.md").read_text()


@pytest.mark.parametrize("installed_in", [".cheese", ".claude"])
def test_releasing_a_private_room_reaches_the_root_it_was_installed_in(
    tmp_path, installed_in
):
    """The release runs as a shell test-and-exec on the machine, so a root it
    does not name is not an error there — it is silence, and the seat the room
    holds is never given back. Run for real against a home laid out each way."""
    project, topic = uuid.uuid4(), uuid.uuid4()
    home = private_chat.device_home_dir(project, topic).replace("$HOME", str(tmp_path))
    directory = Path(home) / installed_in
    (directory / "remote-execution").mkdir(parents=True)
    released = tmp_path / "released.json"
    (directory / "remote-execution/client.py").write_text(
        f"import json, sys\njson.dump(sys.argv[1:], open({str(released)!r}, 'w'))\n"
    )
    (directory / "remote-target.json").write_text('{"kind": "private"}')

    class ShellHub:
        async def exec(self, device_id, argv, *, timeout):
            # The command carries a literal $HOME: the backend never knows the
            # device user's home, and the machine's shell is what expands it.
            done = subprocess.run(
                argv,
                env={**os.environ, "HOME": str(tmp_path)},
                capture_output=True,
                text=True,
            )
            return {"exit": done.returncode, "stderr": done.stderr}

    asyncio.run(private_chat.release(project, topic, "dev1", ShellHub()))

    assert json.loads(released.read_text()) == [
        "release",
        str(directory / "remote-target.json"),
    ]
