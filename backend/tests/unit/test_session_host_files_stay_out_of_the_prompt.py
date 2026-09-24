"""The session host's own instruction files never reach the model.

A central session runs on a machine that has an owner, and its working
directory sits several levels under that owner's home. Claude Code looks for
instruction files in every directory from the working directory up to `/`, so
anything the owner keeps there — a CLAUDE.md, a CLAUDE.local.md, a
`.claude/CLAUDE.md`, a rule — would otherwise be read into a room that belongs
to somebody else. What the room is meant to see arrives through the config
directory instead: the platform's own skills, and the forwarded repository's
instructions imported from there.

The session is started exactly as the executor client starts it, with the
pinned build, and every request it makes is read at a fixture model.
"""

import importlib.util
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from app.domain.agent.harness.claude_code.remote_execution import client, release
from app.domain.agent.skills import native_skill_files
from tests.pinned_claude import claude_binary

ROOT = Path(__file__).resolve().parents[3]

HOST_MARKERS = {
    "ABOVE_THE_HOST_HOME": "CLAUDE.md",
    "HOST_HOME_INSTRUCTIONS": "owner/CLAUDE.md",
    "HOST_HOME_LOCAL_INSTRUCTIONS": "owner/CLAUDE.local.md",
    "HOST_CONFIG_INSTRUCTIONS": "owner/.claude/CLAUDE.md",
    "HOST_RULE_TEXT": "owner/.claude/rules/house-style.md",
    "SESSION_HOME_INSTRUCTIONS": "owner/.cheese/home/CLAUDE.md",
}


def load_model_fixture():
    spec = importlib.util.spec_from_file_location(
        "model_fixture", ROOT / "scripts/remote_execution/model_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Executor:
    """The machine holding the project. Only its answer to `ping` is needed."""

    def __init__(self, target):
        self.target = target

    def call(self, operation, args=None):
        assert operation == "ping", operation
        return {"workspace": "/executor/project"}


def test_host_instruction_files_stay_out_while_the_rooms_own_arrive(
    tmp_path, monkeypatch
):
    owner = tmp_path / "owner"
    for relative, marker in (
        ("CLAUDE.md", "ABOVE_THE_HOST_HOME"),
        ("owner/CLAUDE.md", "HOST_HOME_INSTRUCTIONS"),
        ("owner/CLAUDE.local.md", "HOST_HOME_LOCAL_INSTRUCTIONS"),
        ("owner/.claude/CLAUDE.md", "HOST_CONFIG_INSTRUCTIONS"),
        ("owner/.claude/rules/house-style.md", "HOST_RULE_TEXT"),
        ("owner/.cheese/home/CLAUDE.md", "SESSION_HOME_INSTRUCTIONS"),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{marker}\n")

    # The room's home as the device launcher lays it out on that machine.
    home = owner / ".cheese/home" / str(uuid.uuid4()) / str(uuid.uuid4())
    config = home / ".claude"
    for name, content in native_skill_files().items():
        (config / name).parent.mkdir(parents=True, exist_ok=True)
        (config / name).write_text(content)
    directory = home / ".cheese/remote-session"
    forwarded = directory / "forwarded-project"
    (forwarded / ".claude/skills/repository-check").mkdir(parents=True)
    (forwarded / "CLAUDE.md").write_text("FORWARDED_REPOSITORY_INSTRUCTIONS\n")
    (forwarded / ".claude/skills/repository-check/SKILL.md").write_text(
        "---\nname: repository-check\n"
        "description: FORWARDED_REPOSITORY_SKILL\n---\n\nCheck the repository.\n"
    )
    # The forwarded view is served by the executor; a plain directory with the
    # same files stands in for that mount here.
    monkeypatch.setattr(
        release,
        "mount_state",
        lambda path: (
            release.MOUNT_LIVE if Path(path) == forwarded else release.MOUNT_NONE
        ),
    )
    monkeypatch.setattr(client, "RemoteClient", Executor)
    monkeypatch.setenv("CHEESE_TOKEN", "fixture-scoped-token")

    fixture = load_model_fixture()
    server = fixture.Server(("127.0.0.1", 0), fixture.Handler)
    server.state = {"dir": tmp_path, "actions": [], "requests": []}
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
                    "entries": {
                        "CLAUDE.md": {"kind": "file"},
                        ".claude": {"kind": "directory"},
                        ".claude/skills": {"kind": "directory"},
                        ".claude/skills/repository-check": {"kind": "directory"},
                        ".claude/skills/repository-check/SKILL.md": {"kind": "file"},
                    },
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
                "Say the fixture reply.",
            ],
            base_settings={},
            home_override=str(home),
            config_override=str(config),
        )
        assert Path(launch["cwd"]) == forwarded
        result = subprocess.run(
            launch["command"],
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
            timeout=120,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    assert result.returncode == 0, result.stderr
    assert "ACCEPTANCE_DONE" in result.stdout, result.stdout
    requests = server.state["requests"]
    assert requests
    seen = json.dumps(requests, ensure_ascii=False)
    leaked = {marker: HOST_MARKERS[marker] for marker in HOST_MARKERS if marker in seen}
    assert not leaked, f"session host files reached the model: {leaked}"
    first = json.dumps(requests[0], ensure_ascii=False)
    assert "FORWARDED_REPOSITORY_INSTRUCTIONS" in first
    assert "repository-check" in first
    for skill in {name.split("/")[1] for name in native_skill_files()}:
        assert skill in first, skill
