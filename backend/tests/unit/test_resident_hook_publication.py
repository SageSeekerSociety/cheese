"""Managed tool events retain replay and custom hook decisions without a shell."""

import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.domain.agent.harness.claude_code import device_launch, event_spool
from app.domain.agent.harness.claude_code.hooks_substrate import CHEESE_HOOK_SCRIPT
from app.domain.agent.harness.claude_code.remote_execution import client


@pytest.fixture
def hook_environment(tmp_path, monkeypatch):
    executable = tmp_path / "cheese-hook"
    executable.write_text(CHEESE_HOOK_SCRIPT)
    executable.chmod(0o755)
    spool = tmp_path / "spool"
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CHEESE_HOOK_SPOOL", str(spool))
    monkeypatch.setenv("CHEESE_HOOK_SPOOL_ONLY", "1")
    return executable, spool


def config(command="cheese-hook"):
    return {
        "central_hooks": {
            event: [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}
            ]
            for event in ("PreToolUse", "PostToolUse")
        }
    }


def payload(index, event="PreToolUse"):
    return {
        "hook_event_name": event,
        "tool_name": "Bash",
        "tool_use_id": str(index),
        "tool_input": {"command": "printf 中文"},
    }


def test_managed_events_replay_without_launching_a_process(
    hook_environment, monkeypatch
):
    _, spool = hook_environment

    def unexpected(*args, **kwargs):
        pytest.fail("Managed spool-only publication launched a process")

    monkeypatch.setattr(client.subprocess, "run", unexpected)
    for index, event in enumerate(("PreToolUse", "PostToolUse")):
        assert client.publish_event(config(), payload(index, event)) == {}
    rows = event_spool.spool_entries(spool)
    assert [row[2] for row in rows] == [payload(0), payload(1, "PostToolUse")]
    event_spool.write_cursor(spool, rows[0][0].name)
    assert [
        row[2]
        for row in event_spool.spool_entries(spool, event_spool.read_cursor(spool))
    ] == [payload(1, "PostToolUse")]


def test_concurrent_resident_and_shell_writers_keep_every_event(hook_environment):
    executable, spool = hook_environment

    def publish(index):
        value = payload(index)
        if index % 2:
            subprocess.run(
                [str(executable)], input=json.dumps(value), text=True, check=True
            )
        else:
            client.publish_event(config(), value)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(publish, range(40)))
    rows = event_spool.spool_entries(spool)
    assert len(rows) == 40
    assert {row[2]["tool_use_id"] for row in rows} == {str(i) for i in range(40)}
    assert len({row[0].name.split(".")[0] for row in rows}) == 40
    assert len({row[1] for row in rows}) == 40


def test_modified_hook_and_appended_commands_still_execute(hook_environment, tmp_path):
    executable, spool = hook_environment
    decision = {
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": "custom policy",
        }
    }
    executable.write_text("#!/bin/sh\nprintf '%s' '" + json.dumps(decision) + "'\n")
    assert client.publish_event(config(), payload(0)) == {"deny": "custom policy"}
    executable.write_text(CHEESE_HOOK_SCRIPT)
    marker = tmp_path / "custom-ran"
    assert (
        client.publish_event(config(f"cheese-hook; touch '{marker}'"), payload(1)) == {}
    )
    assert marker.exists()
    assert len(event_spool.spool_entries(spool)) == 1
    shutil.copyfile("/usr/bin/true", executable)
    executable.chmod(0o111)
    try:
        assert client.publish_event(config(), payload(2)) == {}
    finally:
        executable.chmod(0o755)


def test_inline_http_delivery_is_preserved(hook_environment, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 5
        worker = threading.Thread(target=server.handle_request)
        worker.start()
        monkeypatch.delenv("CHEESE_HOOK_SPOOL_ONLY")
        monkeypatch.setenv(
            "CHEESE_HOOK_URL", f"http://127.0.0.1:{server.server_port}/hooks"
        )
        try:
            assert client.publish_event(config(), payload(0)) == {}
        finally:
            worker.join(timeout=10)
        assert not worker.is_alive()
    assert received == [payload(0)]


def test_emitted_helpers_publish_without_the_backend_package(
    hook_environment, tmp_path
):
    _, spool = hook_environment
    launcher = device_launch.build_launch_script(remote_execution=True)
    start = launcher.index('mkdir -p "$HOME/.claude/remote-execution"')
    end = launcher.index(
        'EXECUTOR_CLIENT="$HOME/.claude/remote-execution/client.py"', start
    )
    subprocess.run(["sh", "-c", launcher[start:end]], check=True)
    runner = tmp_path / "standalone.py"
    runner.write_text(
        "import json, os, runpy, subprocess, sys\n"
        "from pathlib import Path\n"
        "directory = Path.home() / '.claude/remote-execution'\n"
        "sys.path.insert(0, str(directory))\n"
        "module = runpy.run_path(str(directory / 'client.py'))\n"
        "def unexpected(*args, **kwargs):\n"
        "    raise AssertionError('managed hook spawned a subprocess')\n"
        "subprocess.run = unexpected\n"
        f"module['publish_event']({config()!r}, {payload(0)!r})\n"
    )
    subprocess.run([sys.executable, str(runner)], check=True)
    assert [row[2] for row in event_spool.spool_entries(spool)] == [payload(0)]
