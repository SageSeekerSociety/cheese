"""The gate command crosses only a constrained Docker argv boundary."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.domain.workspace.service import run_check_command


class FakeProcess:
    def __init__(self, argv, *, stdout, returncode=0, timeout=False, **_kwargs):
        self.argv = argv
        self.stdout = stdout
        self.returncode = returncode
        self.timeout = timeout
        self.killed = False
        stdout.write("container-output\n")

    def wait(self, timeout=None):
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return self.returncode

    def kill(self):
        self.killed = True


def test_command_is_data_in_restricted_docker_argv(tmp_path: Path, monkeypatch):
    seen: dict = {}

    def popen(argv, **kwargs):
        seen["argv"] = argv
        return FakeProcess(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = run_check_command(tmp_path, "echo hello; touch /host-owned")

    assert result["exit_code"] == 0
    assert "container-output" in result["tail"]
    argv = seen["argv"]
    assert argv[:2] == ["docker", "run"]
    assert ["--network", "none"] == argv[
        argv.index("--network") : argv.index("--network") + 2
    ]
    assert ["--cap-drop", "ALL"] == argv[
        argv.index("--cap-drop") : argv.index("--cap-drop") + 2
    ]
    assert "no-new-privileges" in argv
    assert "/var/run/docker.sock" not in " ".join(argv)
    assert argv[-1] == "echo hello; touch /host-owned"
    assert argv[-3:-1] == ['exec sh -lc "$1"', "cheesex-gate"]


def test_worktree_is_mounted_where_the_agent_built_its_venv(
    tmp_path: Path, monkeypatch
):
    # Console scripts (pyright, pytest, alembic) bake their venv's absolute path
    # into their shebang. The agent builds .venv under /work, so mounting the
    # same worktree anywhere else leaves a venv whose tools can't execute — and
    # with no network in the gate container, nothing can repair that. That is
    # how the gate came to run lint only, on every card.
    def popen(argv, **kwargs):
        seen["argv"] = argv
        return FakeProcess(argv, **kwargs)

    seen: dict = {}
    monkeypatch.setattr(subprocess, "Popen", popen)
    run_check_command(tmp_path, "true")

    argv = seen["argv"]
    mount = argv[argv.index("--mount") + 1]
    assert mount == f"type=bind,source={tmp_path.resolve()},target=/work"
    assert argv[argv.index("--workdir") + 1] == "/work"


def test_check_command_is_told_it_is_a_gate(tmp_path: Path, monkeypatch):
    # CHECK_STRICT=1 is how the check command learns it must not report a check
    # it couldn't run as passed (check.sh answers with exit 2 → gate_blocked).
    def popen(argv, **kwargs):
        seen["argv"] = argv
        return FakeProcess(argv, **kwargs)

    seen: dict = {}
    monkeypatch.setattr(subprocess, "Popen", popen)
    run_check_command(tmp_path, "true")

    argv = seen["argv"]
    env_values = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert "CHECK_STRICT=1" in env_values


def test_full_output_is_logged_and_tail_is_bounded(tmp_path: Path, monkeypatch):
    log = tmp_path / "logs" / "gate.log"

    def popen(argv, **kwargs):
        kwargs["stdout"].write("x" * 5000)
        return FakeProcess(argv, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = run_check_command(tmp_path, "test-command", log_path=log)

    assert len(result["tail"]) <= 4000
    text = log.read_text(encoding="utf-8")
    assert "test-command" in text
    assert "x" * 4000 in text


def test_timeout_removes_only_exact_container(tmp_path: Path, monkeypatch):
    seen: dict = {}

    def popen(argv, **kwargs):
        process = FakeProcess(argv, timeout=True, **kwargs)
        seen["process"] = process
        seen["argv"] = argv
        return process

    remove = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", remove)

    result = run_check_command(tmp_path, "sleep forever", timeout=1)

    name = seen["argv"][seen["argv"].index("--name") + 1]
    assert result["exit_code"] == 124
    assert "超时" in result["tail"]
    assert seen["process"].killed is True
    assert remove.call_args.args[0] == ["docker", "rm", "-f", name]


def test_missing_workspace_fails_before_docker(monkeypatch, tmp_path: Path):
    popen = Mock()
    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(FileNotFoundError):
        run_check_command(tmp_path / "missing", "echo no")
    popen.assert_not_called()
