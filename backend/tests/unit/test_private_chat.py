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


def test_the_scratch_area_sits_on_the_machine_it_is_handed(monkeypatch):
    """草稿区不自己挑机器 (结论 19)：交进来哪台就是哪台，部署默认那台不参与。"""
    monkeypatch.setattr(settings, "agent_session_device_id", "deployment-default")
    project, resource = uuid.uuid4(), uuid.uuid4()
    config = private_chat.scratch_target(project, resource, device_id="this-session")
    assert config["device_id"] == "this-session"
    assert config["workspace"] == "/work"
    assert config["mcp_servers"] == []


def test_reopened_chat_uses_a_new_container_and_control_home():
    project, topic, resource = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    before = private_chat.scratch_target(project, topic, device_id="central")
    after = private_chat.scratch_target(project, resource, device_id="central")
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

    config = private_chat.scratch_target(
        uuid.uuid4(), uuid.uuid4(), device_id="central"
    )
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

    config = private_chat.scratch_target(
        uuid.uuid4(), uuid.uuid4(), device_id="central"
    )
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
@pytest.mark.parametrize("seat_name", ["", "4b9f7d802648"])
def test_releasing_a_private_room_reaches_the_root_it_was_installed_in(
    tmp_path, installed_in, seat_name
):
    """The release runs as a shell test-and-exec on the machine, so a root it
    does not name is not an error there — it is silence, and the seat the room
    holds is never given back. Run for real against a home laid out each way."""
    project, topic = uuid.uuid4(), uuid.uuid4()
    home = private_chat.device_home_dir(project, topic).replace("$HOME", str(tmp_path))
    directory = Path(home) / installed_in
    session = directory / "seats" / seat_name if seat_name else directory
    (session / "remote-execution").mkdir(parents=True)
    released = tmp_path / "released.json"
    (session / "remote-execution/client.py").write_text(
        f"import json, sys\njson.dump(sys.argv[1:], open({str(released)!r}, 'w'))\n"
    )
    (session / "remote-target.json").write_text('{"kind": "private"}')

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
        str(session / "remote-target.json"),
    ]


@pytest.mark.parametrize("installed_in", [".cheese", ".claude"])
def test_controlling_a_private_room_reaches_the_seat_that_prepared_it(
    tmp_path, installed_in
):
    """The config the client derives its per-turn files from is a SEAT's now
    (`place.seat_dir`), and this command is a shell test-and-exec on the machine
    — a root it does not name is not an error there, it is silence, and the
    room's control request is answered as 「no private executor is installed」."""
    project, resource = uuid.uuid4(), uuid.uuid4()
    config = private_chat.scratch_target(project, resource, device_id="dev1")
    home = config["home"].replace("$HOME", str(tmp_path))
    directory = Path(home) / installed_in
    session = directory / "seats/4b9f7d802648/remote-session"
    session.mkdir(parents=True)
    (session / "execution.json").write_text("{}")
    (session.parent / "remote-execution").mkdir()
    (session.parent / "remote-execution/client.py").write_text(
        "import json, sys\njson.dump(sys.argv[1:], sys.stdout)\n"
    )
    request = {"subtype": "read_file", "path": "/work/draft.md"}

    class ShellHub:
        async def exec(self, device_id, argv, *, stdin=None, timeout):
            done = subprocess.run(
                argv,
                input=stdin,
                env={**os.environ, "HOME": str(tmp_path)},
                capture_output=True,
                text=True,
            )
            return {
                "exit": done.returncode,
                "stdout": done.stdout,
                "stderr": done.stderr,
            }

    assert asyncio.run(private_chat.control(config, request, hub=ShellHub())) == [
        "control",
        str(session / "execution.json"),
    ]


# What docker prints when the executor container cannot be created. Kept so
# the session's startup record says which of these it was.
_DOCKER_REFUSALS = {
    "image": (
        "Unable to find image 'cheese-private-executor:2.1.282' locally\n"
        "docker: Error response from daemon: pull access denied for "
        "cheese-private-executor, repository does not exist.",
        "Claude Code 启动失败：环境里缺少执行容器的镜像",
    ),
    "name": (
        "docker: Error response from daemon: Conflict. The container name "
        '"/cheese-private-0f0f" is already in use by container "abc123".',
        "Claude Code 启动失败：上一个执行容器还没有清理掉",
    ),
    "daemon": (
        "docker: Cannot connect to the Docker daemon at "
        "unix:///var/run/docker.sock. Is the docker daemon running?",
        "Claude Code 启动失败：环境里的 Docker 没有运行或无法访问",
    ),
    "other": (
        "docker: Error response from daemon: OCI runtime create failed.",
        "Claude Code 启动失败：执行容器没能创建",
    ),
}


@pytest.mark.parametrize("case", sorted(_DOCKER_REFUSALS))
def test_a_container_docker_would_not_create_says_why(case, tmp_path, monkeypatch):
    from app.domain.agent.harness.claude_code.remote_execution.private import ensure
    from app.domain.agent.platform_failures import classify_session_start

    said, sentence = _DOCKER_REFUSALS[case]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "said").write_text(said)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = container ]; then echo "Error: No such container: x" >&2; '
        "exit 1; fi\n"
        f'cat "{tmp_path / "said"}" >&2\nexit 125\n'
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(RuntimeError) as refused:
        ensure(target(uuid.uuid4()), tmp_path / "state", {})

    assert said.splitlines()[-1] in str(refused.value)
    assert "status 125" in str(refused.value)
    assert classify_session_start(str(refused.value)).content == sentence
