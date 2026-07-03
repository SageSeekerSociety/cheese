"""Unit tests for the host-side check-command runner behind the accept gate."""

from pathlib import Path

from app.domain.workspace.service import run_check_command


def test_green_command(tmp_path: Path):
    log = tmp_path / "logs" / "gate.log"
    result = run_check_command(tmp_path, "echo hello-gate", log_path=log)
    assert result["exit_code"] == 0
    assert "hello-gate" in result["tail"]
    # Full output + a timestamped header land in the log file.
    text = log.read_text(encoding="utf-8")
    assert "hello-gate" in text
    assert "echo hello-gate" in text


def test_red_command_captures_stderr(tmp_path: Path):
    result = run_check_command(tmp_path, "echo broken >&2; exit 5")
    assert result["exit_code"] == 5
    assert "broken" in result["tail"]


def test_runs_in_given_workdir(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
    result = run_check_command(tmp_path, "cat marker.txt")
    assert result["exit_code"] == 0
    assert "here" in result["tail"]


def test_timeout_reports_124(tmp_path: Path):
    result = run_check_command(tmp_path, "sleep 5", timeout=1)
    assert result["exit_code"] == 124
    assert "超时" in result["tail"]


def test_tail_is_bounded(tmp_path: Path):
    result = run_check_command(tmp_path, "yes x | head -20000")
    assert result["exit_code"] == 0
    assert len(result["tail"]) <= 4000
