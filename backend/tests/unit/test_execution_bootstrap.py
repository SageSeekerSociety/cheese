"""Start an executor from an empty machine home and reuse its room state."""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution import runtime
from app.domain.agent.harness.claude_code.remote_execution.launch import script


def test_prepared_executor_can_start_offline_forge_transport(tmp_path, monkeypatch):
    from app.domain.agent import forge_cli
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

    project, resource = uuid.uuid4(), uuid.uuid4()
    env = {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"}
    payload = payload_for(project, resource, env)
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    with bootstrap.prepared(payload, tmp_path) as (home, _config, _state, _env):
        monkeypatch.setenv("HOME", str(home))
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        transport = {}
        with forge_cli.github_transport(
            {"api_url": "http://127.0.0.1:1", "project_id": str(project)}, transport
        ):
            assert transport["HTTPS_PROXY"].startswith("http://127.0.0.1:")
            assert transport["NO_PROXY"] == ""


def test_executor_prepares_room_without_model_credentials(tmp_path):
    pin = Path.home() / ".local/share/claude/versions/2.1.282"
    binary = os.environ.get("CHEESE_TEST_CLAUDE") or (
        str(pin) if pin.exists() else None
    )
    if not binary:
        pytest.fail(
            "CHEESE_TEST_CLAUDE must point to the pinned Claude build before "
            "running the pure layer"
        )
    owner = tmp_path / "owner"
    destination = owner / ".cheese/claude/versions/2.1.282"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(binary)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = owner / ".cheese/home" / str(project) / str(resource)
    work = home / "room"
    state = home / ".cheese/executor"
    configuration = {
        "revision": "fixture-revision",
        "variables": {"EXECUTOR_SETTING": "project-value"},
        "setup_script": (
            "printf original > notes.txt\n"
            'printf "$EXECUTOR_SETTING" > setup-result\n'
            'mkdir -p "$HOME/.local/bin"\n'
            "printf '#!/bin/sh\\nprintf INSTALLED_TOOL_OK\\n' "
            '> "$HOME/.local/bin/room-tool"\n'
            'chmod +x "$HOME/.local/bin/room-tool"'
        ),
        "startup_script": "cd backend\nprintf started >> startup-result",
    }
    env = {
        "CHEESE_API": "http://127.0.0.1:1",
        "CHEESE_TOKEN": "first-token",
        "CHEESE_PROJECT": str(project),
        "CHEESE_TOPIC": str(resource),
        "CHEESE_ENVIRONMENT": json.dumps(configuration),
        "ANTHROPIC_API_KEY": "must-not-be-copied",
    }

    def launch():
        return subprocess.run(
            [sys.executable, "-"],
            input=script(project, resource, env),
            env={**os.environ, "HOME": str(owner)},
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

    try:
        info = json.loads(launch().stdout)
        assert info["workspace"] == str(work)
        deadline = time.monotonic() + 15
        while True:
            try:
                running = runtime.request(state, "ping")
                break
            except (ConnectionError, FileNotFoundError):
                if time.monotonic() >= deadline:
                    pytest.fail((home / ".cheese/executor-bootstrap.log").read_text())
                time.sleep(0.05)
        assert not (work / ".git").exists()
        assert (work / "notes.txt").read_text() == "original"
        assert (work / "setup-result").read_text() == "project-value"
        assert not (work / "startup-result").exists()
        stored = json.loads((state / "config.json").read_text())
        assert "ANTHROPIC_API_KEY" not in stored["env"]
        read = runtime.request(
            state,
            "invoke",
            {
                "id": "read-notes",
                "tool": "Read",
                "args": {"file_path": str(work / "notes.txt")},
            },
        )
        assert "error" not in read, read
        assert read["value"]["file"]["content"] == "original"
        installed = runtime.request(
            state,
            "invoke",
            {
                "id": "installed-program",
                "tool": "Bash",
                "args": {"command": "room-tool"},
            },
        )
        assert "INSTALLED_TOOL_OK" in json.dumps(installed), installed
        (work / "draft.txt").write_text("retain this draft")
        # Older running rooms have a setup receipt but no task configuration.
        task_config = home / ".cheese-environment/config.json"
        task_config.unlink()
        env["CHEESE_TOKEN"] = "rotated-token"
        reused = json.loads(launch().stdout)
        assert reused["pid"] == running["pid"]
        assert (work / "draft.txt").read_text() == "retain this draft"
        assert not (work / "startup-result").exists()
        assert json.loads(task_config.read_text()) == configuration
        assert (
            json.loads((state / "config.json").read_text())["env"]["CHEESE_TOKEN"]
            == "rotated-token"
        )
        # The existing environment reset must stop the execution daemon too.
        reset = subprocess.run(
            [sys.executable, str(home / ".cheese/cheese-environment.py"), "reset"],
            env={**os.environ, "HOME": str(home)},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        assert json.loads(reset.stdout)["state"] == "pending"
        with pytest.raises((ConnectionError, FileNotFoundError)):
            runtime.request(state, "ping")
        configuration["revision"] = "second-revision"
        env["CHEESE_ENVIRONMENT"] = json.dumps(configuration)
        launch()
        deadline = time.monotonic() + 15
        while True:
            try:
                restarted = runtime.request(state, "ping")
                break
            except (ConnectionError, FileNotFoundError):
                assert time.monotonic() < deadline
                time.sleep(0.05)
        assert restarted["pid"] != running["pid"]
        assert (work / "draft.txt").read_text() == "retain this draft"
        assert not (work / "startup-result").exists()
        # A failed setup leaves the ownership marker without a running daemon.
        subprocess.run(
            [sys.executable, runtime.__file__, "stop", "--state", str(state)],
            check=True,
            timeout=15,
        )
        configuration.update(revision="broken-revision", setup_script="exit 23")
        env["CHEESE_ENVIRONMENT"] = json.dumps(configuration)
        launch()
        status_file = home / ".cheese-environment/status.json"
        deadline = time.monotonic() + 15
        while json.loads(status_file.read_text()).get("state") != "failed":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        repaired = subprocess.run(
            [sys.executable, str(home / ".cheese/cheese-environment.py"), "reset"],
            env={**os.environ, "HOME": str(home)},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        assert json.loads(repaired.stdout)["state"] == "pending"
        configuration.update(revision="fixed-revision", setup_script="true")
        env["CHEESE_ENVIRONMENT"] = json.dumps(configuration)
        launch()
        deadline = time.monotonic() + 15
        while True:
            try:
                runtime.request(state, "ping")
                break
            except (ConnectionError, FileNotFoundError):
                assert time.monotonic() < deadline
                time.sleep(0.05)
        assert (work / "draft.txt").read_text() == "retain this draft"
    finally:
        subprocess.run(
            [sys.executable, runtime.__file__, "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def _executor_payload():
    from app.domain.agent.harness.claude_code.remote_execution.launch import (
        payload_for,
    )

    return payload_for(
        uuid.uuid4(),
        uuid.uuid4(),
        {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"},
    )


def test_a_new_executor_machine_is_given_the_platform_skills(tmp_path, monkeypatch):
    """This machine is where the skill's own command runs.

    The agent is handed skill text that names
    `$CLAUDE_CONFIG_DIR/skills/documents/scripts/office.py`, and the shell it
    runs that in is on the executor. Nothing on that side installed the file —
    the container and device-hosted launches each do it beside the claude they
    start, and this path starts no claude. A room that answers this by finding a
    copy in another room's cache is not a mechanism: the other room has to still
    be there.
    """
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.skills import native_skill_files

    payload = _executor_payload()
    assert payload["skills"], "the payload carries no skills at all"
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    with bootstrap.prepared(payload, tmp_path) as (home, _config, _state, _env):
        for name, content in native_skill_files().items():
            assert (home / ".claude" / name).read_text(encoding="utf-8") == content
        # The script the skill tells the agent to run, and one reference it is
        # told to read before running it.
        assert (home / ".claude/skills/documents/scripts/office.py").is_file()
        assert (home / ".claude/skills/documents/references/word.md").is_file()


def test_preparing_again_restores_them_and_an_older_payload_still_prepares(
    tmp_path, monkeypatch
):
    """This runs on every prepare, including the ones that change nothing."""
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    payload = _executor_payload()
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    with bootstrap.prepared(payload, tmp_path) as (home, _config, _state, _env):
        script = home / ".claude/skills/documents/scripts/office.py"
    script.write_text("# trampled\n", encoding="utf-8")
    with bootstrap.prepared(payload, tmp_path) as (home, _config, _state, _env):
        restored = home / ".claude/skills/documents/scripts/office.py"
        assert restored.resolve() == script.resolve()
        assert restored.read_text(encoding="utf-8") != "# trampled\n"
    # A payload from a backend that predates this key prepares as it always did.
    with bootstrap.prepared(
        {name: value for name, value in payload.items() if name != "skills"}, tmp_path
    ):
        pass
