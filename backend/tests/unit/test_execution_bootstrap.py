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


def test_executor_prepares_room_without_model_credentials(tmp_path):
    pin = Path.home() / ".local/share/claude/versions/2.1.265"
    binary = os.environ.get("CHEESE_TEST_CLAUDE") or (
        str(pin) if pin.exists() else None
    )
    if not binary:
        pytest.skip("Native executor acceptance supplies CHEESE_TEST_CLAUDE in CI")
    owner = tmp_path / "owner"
    destination = owner / ".cheese/claude/versions/2.1.265"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(binary)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = owner / ".cheese/home" / str(project) / str(resource)
    work = home / "room"
    state = home / ".claude/executor"
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
                    pytest.fail((home / ".claude/executor-bootstrap.log").read_text())
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
            [sys.executable, str(home / ".claude/cheese-environment.py"), "reset"],
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
            [sys.executable, str(home / ".claude/cheese-environment.py"), "reset"],
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
