"""The observer cannot change the command result or outlive it."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolate_storage_observer(monkeypatch):
    monkeypatch.delenv("CHEESE_CI_POSTGRES_PID", raising=False)


def test_storage_sample_counts_whole_filesystem_and_available_memory(
    tmp_path, monkeypatch
):
    from scripts.observe_ci_resources import storage_snapshot

    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16384 kB\nMemFree: 512 kB\nMemAvailable: 4096 kB\n")
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda _: SimpleNamespace(
            f_blocks=1000, f_bfree=700, f_bavail=600, f_frsize=4096
        ),
    )
    assert storage_snapshot(tmp_path, meminfo) == {
        "filesystem_total_bytes": 4096000,
        "filesystem_used_bytes": 1228800,
        "filesystem_available_bytes": 2457600,
        "mem_available_bytes": 4194304,
    }


def test_sigterm_stops_command_and_monitors(tmp_path):
    output = tmp_path / "resources"
    pidfile = tmp_path / "command.pid"
    binary = tmp_path / "vmstat"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(300)\n")
    binary.chmod(0o755)
    command = tmp_path / "command.py"
    command.write_text(
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(300)\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.observe_ci_resources",
            "--output",
            str(output),
            "--",
            sys.executable,
            str(command),
        ],
        cwd=BACKEND,
        env=os.environ | {"PATH": str(tmp_path)},
    )
    try:
        deadline = time.monotonic() + 10
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pidfile.exists()
        process.terminate()
        assert process.wait(timeout=10) == 143
        events = [
            json.loads(line)
            for line in (output / "lifecycle.jsonl").read_text().splitlines()
        ]
        pids = [int(pidfile.read_text())] + [
            event["pid"] for event in events if event["event"] == "monitor_started"
        ]
        assert len(pids) == 3
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert events[-1]["event"] == "finished"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize("exit_code", [0, 23])
def test_command_result_and_monitor_cleanup(tmp_path, exit_code):
    output = tmp_path / "resources"
    # A real long-running process proves cleanup, even on a host without vmstat.
    binary = tmp_path / "vmstat"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(300)\n")
    binary.chmod(0o755)
    command = tmp_path / "command.py"
    command.write_text(
        "import time\nfrom pathlib import Path\n"
        f"log = Path({str(output / 'postgres.log')!r})\n"
        "deadline = time.monotonic() + 5\n"
        "while time.monotonic() < deadline:\n"
        "    if log.exists() and log.stat().st_size:\n"
        "        break\n"
        "    time.sleep(0.01)\n"
        f"raise SystemExit({exit_code})\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.observe_ci_resources",
            "--output",
            str(output),
            "--",
            sys.executable,
            str(command),
        ],
        cwd=BACKEND,
        env=os.environ
        | {
            "PATH": str(tmp_path),
            "DATABASE_URL": "postgresql://127.0.0.1:1/no_database",
        },
        timeout=15,
    )
    assert result.returncode == exit_code
    events = [
        json.loads(line)
        for line in (output / "lifecycle.jsonl").read_text().splitlines()
    ]
    started = [event for event in events if event["event"] == "monitor_started"]
    assert {event["monitor"] for event in started} == {"vmstat", "postgres"}
    assert len([event for event in events if event["event"] == "monitor_stopped"]) == 2
    for event in started:
        with pytest.raises(ProcessLookupError):
            os.kill(event["pid"], 0)
    assert events[-1]["event"] == "finished"
    assert "ConnectionRefusedError" in (output / "postgres.log").read_text()


def test_missing_vmstat_does_not_replace_a_command_failure(tmp_path):
    output = tmp_path / "resources"
    command = tmp_path / "command.py"
    command.write_text("raise SystemExit(17)\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.observe_ci_resources",
            "--output",
            str(output),
            "--",
            sys.executable,
            str(command),
        ],
        cwd=BACKEND,
        env=os.environ | {"PATH": str(tmp_path)},
        timeout=15,
    )
    assert result.returncode == 17
    events = [
        json.loads(line)
        for line in (output / "lifecycle.jsonl").read_text().splitlines()
    ]
    assert any(
        event["event"] == "unavailable" and event["monitor"] == "vmstat"
        for event in events
    )
