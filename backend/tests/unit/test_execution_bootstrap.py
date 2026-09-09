"""Start an executor from an empty machine home and reuse its project state."""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution import runtime
from app.domain.agent.harness.claude_code.remote_execution.launch import script


def test_executor_prepares_project_without_model_credentials(tmp_path):
    pin = Path.home() / ".local/share/claude/versions/2.1.265"
    binary = os.environ.get("CHEESE_TEST_CLAUDE") or (
        str(pin) if pin.exists() else shutil.which("claude")
    )
    assert binary, "The pinned Claude Code build is required"
    original = tmp_path / "original"
    subprocess.run(["git", "init", "-q", str(original)], check=True)
    (original / "project.txt").write_text("original")
    subprocess.run(["git", "add", "."], cwd=original, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=original,
        check=True,
    )
    owner = tmp_path / "owner"
    destination = owner / ".cheese/claude/versions/2.1.265"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(binary)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = owner / ".cheese/home" / str(project) / str(resource)
    work = owner / ".cheese/work" / str(project) / str(resource)
    state = home / ".claude/executor"
    configuration = {
        "revision": "fixture-revision",
        "variables": {"EXECUTOR_SETTING": "project-value"},
        "setup_script": 'printf "$EXECUTOR_SETTING" > setup-result',
        "startup_script": "printf started >> startup-result",
    }
    env = {
        "CHEESE_API": "http://127.0.0.1:1",
        "CHEESE_TOKEN": "first-token",
        "CHEESE_PROJECT": str(project),
        "CHEESE_TOPIC": str(resource),
        "CHEESE_GIT_REMOTE": original.as_uri(),
        "CHEESE_GIT_BRANCH": "room",
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
        assert (work / "project.txt").read_text() == "original"
        assert (work / "setup-result").read_text() == "project-value"
        assert (work / "startup-result").read_text() == "started"
        stored = json.loads((state / "config.json").read_text())
        assert "ANTHROPIC_API_KEY" not in stored["env"]
        read = runtime.request(
            state,
            "invoke",
            {
                "id": "read-project",
                "tool": "Read",
                "args": {"file_path": str(work / "project.txt")},
            },
        )
        assert "error" not in read, read
        assert read["value"]["file"]["content"] == "original"
        (work / "draft.txt").write_text("retain this draft")
        env["CHEESE_TOKEN"] = "rotated-token"
        reused = json.loads(launch().stdout)
        assert reused["pid"] == running["pid"]
        assert (work / "draft.txt").read_text() == "retain this draft"
        assert (work / "startup-result").read_text() == "started"
        assert (
            json.loads((state / "config.json").read_text())["env"]["CHEESE_TOKEN"]
            == "rotated-token"
        )
    finally:
        subprocess.run(
            [sys.executable, runtime.__file__, "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )
