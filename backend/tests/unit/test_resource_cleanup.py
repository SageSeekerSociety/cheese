"""Protect original bytes and keep late subprocesses away from reused work."""

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

import pytest

from app.domain.agent import resource_cleanup as cleanup


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("fixture did not reach the expected process state")


def test_stop_lock_survives_parent_death_and_late_retry_is_read_only(tmp_path):
    project, resource, operation = (str(uuid.uuid4()) for _ in range(3))
    home, work = cleanup.resource_paths(tmp_path, project, resource)
    (home / ".claude").mkdir(parents=True)
    work.mkdir(parents=True)
    socket = tmp_path / "tmux.sock"
    socket.touch()
    checksum = (
        subprocess.run(
            ["cksum"], input=str(work).encode(), capture_output=True, check=True
        )
        .stdout.decode()
        .split()[0]
    )
    (home / ".claude/environment-session.json").write_text(
        json.dumps([str(socket), "cheese_" + checksum])
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    tmux = binary / "tmux"
    tmux.write_text(
        f"#!{sys.executable}\n"
        + """import os, sys, time
from pathlib import Path
root = Path(os.environ["HOME"])
if "has-session" in sys.argv:
    sys.exit(1 if (root / "delivered").exists() else 0)
(root / "started").write_text(str(os.getpid()))
while not (root / "release").exists():
    time.sleep(0.01)
with (root / "delivered").open("a") as output:
    output.write("stop\\n")
"""
    )
    tmux.chmod(0o755)
    lsof = binary / "lsof"
    lsof.write_text("#!/bin/sh\nexit 1\n")
    lsof.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
    }
    command = [
        sys.executable,
        cleanup.__file__,
        "prepare",
        project,
        resource,
        operation,
    ]
    receipt = tmp_path / ".cheese/cleanup" / operation / (resource + ".ready")
    with (tmp_path / "process.log").open("wb") as log:
        first = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        second = None
        try:
            wait_for(lambda: (tmp_path / "started").exists())
            first.kill()
            first.wait(timeout=5)
            second = subprocess.Popen(command, env=env, stdout=log, stderr=log)
            time.sleep(0.15)
            assert second.poll() is None and not receipt.exists()
            (tmp_path / "release").touch()
            assert second.wait(timeout=5) == 0
            assert receipt.read_text() == "ready\n"
            assert (tmp_path / "delivered").read_text() == "stop\n"
            # An old prepare arriving after a new session started must do nothing.
            subprocess.run(
                command, env=env, stdout=log, stderr=log, check=True, timeout=5
            )
            assert (tmp_path / "delivered").read_text() == "stop\n"
        finally:
            (tmp_path / "release").touch()
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


def test_deletion_refuses_tail_written_after_confirmation(tmp_path):
    home = tmp_path / "home"
    original = home / ".claude/projects/p/session.jsonl"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original\n")
    receipts = [
        {
            "source": str(original.relative_to(home)),
            "size": 9,
            "sha256": hashlib.sha256(b"original\n").hexdigest(),
        }
    ]
    cleanup.check_transcripts(home, receipts)
    original.write_bytes(b"original\nlate result\n")
    with pytest.raises(RuntimeError, match="changed"):
        cleanup.check_transcripts(home, receipts)
    assert original.read_bytes().endswith(b"late result\n")


def test_unpublished_source_blocks_cleanup(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "source.py").write_text("work in progress")
    with pytest.raises(RuntimeError, match="working-tree changes"):
        cleanup.check_published(tmp_path)
    assert (tmp_path / "source.py").exists()


def test_private_cleanup_rejects_another_generation(tmp_path):
    resource = str(uuid.uuid4())
    marker = tmp_path / ".claude/remote-target.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"kind": "private", "topic": resource}))
    assert cleanup.session_target(tmp_path, resource)["topic"] == resource
    with pytest.raises(RuntimeError, match="another resource generation"):
        cleanup.session_target(tmp_path, str(uuid.uuid4()))
