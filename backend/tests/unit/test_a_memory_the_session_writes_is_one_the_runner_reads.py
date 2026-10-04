"""A memory the agent writes lands where the runner reconciles it.

The session runs on the session host and its file tools run on the executor,
the machine that holds the project. The memory tree is neither the project's
nor the machine's: it is the session's own, in the session's home on the
session host, where the runner lays the platform's copy down and collects
what changed (`runner.sync_memory`). So a Write or Edit of a memory has to
stay on the session host, however the agent spells the path: as the prompt
names it (`~/.cheese/memory/...`), or with the home its shell reports, which
is the executor's.

The session is prepared exactly as the executor client prepares it, and the
pinned build runs with the plugin that preparation writes, against the
deterministic model fixture.
"""

import importlib.util
import json
import os
import subprocess
import threading
from pathlib import Path

from app.domain.agent.harness.claude_code.remote_execution import client, release
from app.domain.agent.harness.claude_code.runner import Runner
from tests.pinned_claude import claude_binary

ROOT = Path(__file__).resolve().parents[3]

_PRIVATE = """---
name: prefers-short-answers
description: 回答要短
type: feedback
---

他要简短的回答，不要末尾总结。
"""

_PROJECT = """---
name: answer-first
description: 先给结论
type: feedback
---

有结论就先说结论。
"""


def _model_fixture():
    spec = importlib.util.spec_from_file_location(
        "model_fixture", ROOT / "scripts/remote_execution/model_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Executor:
    """The machine holding the project. Preparation only asks it `ping`; any
    file tool that reaches it goes through the transport process, which has no
    machine to reach here, so a memory written there is a memory lost."""

    def __init__(self, target):
        self.target = target

    def call(self, operation, args=None):
        assert operation == "ping", operation
        return {"workspace": "/executor/project"}


def _session_writes(tmp_path, monkeypatch, actions) -> Path:
    """Run one session whose model makes ``actions``; return its home."""
    home = tmp_path / "owner/.cheese/home/project/room"
    config = home / ".claude"
    config.mkdir(parents=True)
    directory = home / ".cheese/remote-session"
    forwarded = directory / "forwarded-project"
    forwarded.mkdir(parents=True)
    monkeypatch.setattr(
        release,
        "mount_state",
        lambda path: (
            release.MOUNT_LIVE if Path(path) == forwarded else release.MOUNT_NONE
        ),
    )
    monkeypatch.setattr(client, "RemoteClient", Executor)
    monkeypatch.setenv("CHEESE_TOKEN", "fixture-scoped-token")

    fixture = _model_fixture()
    server = fixture.Server(("127.0.0.1", 0), fixture.Handler)
    server.state = {"dir": tmp_path, "actions": actions, "requests": []}
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        launch = client.prepare(
            directory,
            {
                "kind": "device",
                "device_id": "executor",
                "workspace": "/executor/project",
                "mcp_servers": [],
                "context_tree": {
                    "generation": "fixture",
                    "entries": {},
                    "unsupported_imports": [],
                    "unsupported_paths": [],
                },
            },
            claude=claude_binary(),
            extra_args=[
                "--dangerously-skip-permissions",
                "--print",
                "--model",
                "claude-sonnet-4-6",
                "--",
                "Remember this.",
            ],
            base_settings={},
            home_override=str(home),
            config_override=str(config),
        )
        # `[python, client.py, "enter", target, *build]`: the namespace `enter`
        # builds places the project view, which no memory path goes through,
        # and needs Linux. The build runs with every other part of the launch.
        helper, enter = launch["command"][:4], launch["command"][4:]
        assert helper[2] == "enter", helper
        result = subprocess.run(
            enter,
            cwd=launch["cwd"],
            env={
                "PATH": os.environ["PATH"],
                **launch["env"],
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    assert result.returncode == 0, result.stderr
    assert "ACCEPTANCE_DONE" in result.stdout, result.stdout
    return home


def _reconciled(home: Path, monkeypatch, scopes: dict) -> dict:
    """What the runner's next reconciliation collects from that home."""
    monkeypatch.setenv("HOME", str(home))
    runner = object.__new__(Runner)
    runner.launch = "test"
    return runner.sync_memory({"scopes": scopes})["files"]


def test_a_memory_written_as_the_prompt_names_it_is_collected(tmp_path, monkeypatch):
    home = _session_writes(
        tmp_path,
        monkeypatch,
        [
            {
                "name": "Write",
                "input": {
                    "file_path": "~/.cheese/memory/private/alice/prefers-short.md",
                    "content": _PRIVATE,
                },
            },
            {
                "name": "Edit",
                "input": {
                    "file_path": "~/.cheese/memory/private/alice/prefers-short.md",
                    "old_string": "不要末尾总结",
                    "new_string": "不要在末尾总结",
                },
            },
        ],
    )
    files = _reconciled(home, monkeypatch, {"project": {}, "private/alice": {}})
    assert files == {
        "private/alice/prefers-short.md": _PRIVATE.replace(
            "不要末尾总结", "不要在末尾总结"
        )
    }


def test_a_memory_written_under_the_shells_home_is_collected(tmp_path, monkeypatch):
    """The agent asked its shell where `$HOME` is and got the executor's."""
    home = _session_writes(
        tmp_path,
        monkeypatch,
        [
            {
                "name": "Write",
                "input": {
                    "file_path": "/home/cheese/.cheese/memory/project/answer-first.md",
                    "content": _PROJECT,
                },
            },
        ],
    )
    files = _reconciled(home, monkeypatch, {"project": {}})
    assert files == {"project/answer-first.md": _PROJECT}


def test_what_the_platform_lays_down_is_what_the_agent_reads(tmp_path, monkeypatch):
    """The other direction: a Read of a memory is of the runner's copy."""
    captured = {}

    def read_back(body):
        captured["result"] = json.dumps(
            body["messages"][-1]["content"], ensure_ascii=False
        )
        return None

    home = tmp_path / "owner/.cheese/home/project/room"
    monkeypatch.setenv("HOME", str(home))
    runner = object.__new__(Runner)
    runner.launch = "test"
    runner.sync_memory({"scopes": {"project": {"answer-first.md": _PROJECT}}})
    _session_writes(
        tmp_path,
        monkeypatch,
        [
            {
                "name": "Read",
                "input": {"file_path": "~/.cheese/memory/project/answer-first.md"},
            },
            read_back,
        ],
    )
    assert "有结论就先说结论" in captured["result"]


def test_a_memory_written_empty_is_deleted_and_an_empty_index_is_kept(
    tmp_path, monkeypatch
):
    """The agent's shell is on another machine, so `rm` cannot reach the tree:
    writing a memory empty is how it deletes one. The index is different — an
    empty index is an empty index, not a deleted one."""
    index = "- [先给结论](answer-first.md) — 有结论就先说结论\n"
    scopes = {"project": {"answer-first.md": _PROJECT, "MEMORY.md": index}}
    home = tmp_path / "owner/.cheese/home/project/room"
    assert _reconciled(home, monkeypatch, scopes) == {
        "project/answer-first.md": _PROJECT,
        "project/MEMORY.md": index,
    }
    _session_writes(
        tmp_path,
        monkeypatch,
        [
            {
                "name": "Write",
                "input": {
                    "file_path": "~/.cheese/memory/project/answer-first.md",
                    "content": "",
                },
            },
            {
                "name": "Write",
                "input": {
                    "file_path": "~/.cheese/memory/project/MEMORY.md",
                    "content": "",
                },
            },
        ],
    )
    assert _reconciled(home, monkeypatch, scopes) == {"project/MEMORY.md": ""}
    assert not (home / ".cheese/memory/project/answer-first.md").exists()
    # The platform, answered with that tree, deletes the memory; the next
    # reconciliation has nothing to lay back down.
    assert _reconciled(home, monkeypatch, {"project": {"MEMORY.md": ""}}) == {
        "project/MEMORY.md": ""
    }
    assert not (home / ".cheese/memory/project/answer-first.md").exists()
