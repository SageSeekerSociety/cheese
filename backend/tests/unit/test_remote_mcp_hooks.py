"""A project's own tool hooks hold around a remote MCP server's calls.

A repository that guards `mcp__<server>__.*` with a PreToolUse or PostToolUse
hook in `.claude/settings.json` gets that in plain Claude Code whatever the
server's transport. In a room a remote server's calls are made by the
platform, never by the room's machine, yet the hooks are the project's and run
on that machine, as every other hook of the project does.

The platform's side is a stand-in HTTP server answering the room's
`/topics/{id}/mcp/{name}` route; what is under test is the session side and
the machine: which hooks ran, with what, and whether the call went out.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.pinned_claude import claude_binary

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/remote_execution"

TOOL = {
    "name": "whoami",
    "description": "Echo a note.",
    "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}},
}

# Written the way a repository writes them for plain Claude Code.
LOG = """#!/bin/sh
cat >> "$CLAUDE_PROJECT_DIR/hook-log.jsonl"
echo >> "$CLAUDE_PROJECT_DIR/hook-log.jsonl"
"""
GUARD = """#!/bin/sh
input=$(cat)
case "$input" in
  *FORBIDDEN*)
    printf '%s' '{"hookSpecificOutput": {"hookEventName": "PreToolUse",
      "permissionDecision": "deny",
      "permissionDecisionReason": "PROJECT_POLICY: no forbidden notes"}}'
    ;;
esac
"""
HOOKS = {
    "PreToolUse": [
        {
            "matcher": "mcp__tracker__.*",
            "hooks": [
                {"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/.claude/log.sh'},
                {
                    "type": "command",
                    "command": '"$CLAUDE_PROJECT_DIR"/.claude/guard.sh',
                },
            ],
        }
    ],
    "PostToolUse": [
        {
            "matcher": "mcp__tracker__.*",
            "hooks": [
                {"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/.claude/log.sh'}
            ],
        }
    ],
}


@pytest.fixture
def platform():
    """The platform's remote MCP route, recording each call that reached it."""
    calls: list[dict] = []

    class Route(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — the stdlib's name
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, **body})
            if body["method"] == "tools/call":
                note = body["params"]["arguments"].get("note")
                result = {
                    "content": [{"type": "text", "text": f"REMOTE_RESULT {note}"}]
                }
            elif body["method"] == "tools/list":
                result = {"tools": [TOOL]}
            else:
                result = {}
            data = json.dumps({"code": 200, "data": {"result": result}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Route)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls
    server.shutdown()


@pytest.fixture
def acceptance():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import acceptance

        yield acceptance
    finally:
        sys.path.remove(str(SCRIPTS))
        for name in ("headless_contract", "model_fixture", "acceptance", "release"):
            sys.modules.pop(name, None)


def _machine(acceptance, folder, monkeypatch):
    """A room's machine over a project whose settings hold HOOKS."""
    home = folder / "executor-home"
    (home / ".claude").mkdir(parents=True)
    with monkeypatch.context() as scoped:
        scoped.setenv("HOME", str(home))
        scoped.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
        executor, target = acceptance.setup(
            folder,
            argparse.Namespace(ssh=None, claude=claude_binary()),
            "http://127.0.0.1:9",
        )
    project = Path(
        json.loads((folder / "executor-input.json").read_text())["workspace"]
    )
    for name, script in (("log.sh", LOG), ("guard.sh", GUARD)):
        (project / ".claude").mkdir(exist_ok=True)
        (project / ".claude" / name).write_text(script)
        (project / ".claude" / name).chmod(0o755)
    (project / ".claude/settings.json").write_text(json.dumps({"hooks": HOOKS}))
    return executor, target, project


def _log(project: Path) -> list[dict]:
    path = project / "hook-log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_codex_calls_a_remote_server_inside_the_projects_hooks(
    acceptance, platform, tmp_path, monkeypatch
):
    from app.domain.agent.harness.codex.tools import RemoteTools

    api, calls = platform
    executor, target, project = _machine(acceptance, tmp_path / "codex", monkeypatch)
    monkeypatch.setenv("CHEESE_API", api)
    monkeypatch.setenv("CHEESE_TOKEN", "room-token")
    target = {**target, "remote_mcp": {"path": "/topics/t/mcp", "servers": ["tracker"]}}
    try:
        tools = RemoteTools(target)
        names = [tool["name"] for tool in asyncio.run(tools.discover())]
        assert "mcp__tracker__whoami" in names

        def call(call_id, note):
            return asyncio.run(
                tools(
                    "item/tool/call",
                    {
                        "tool": "mcp__tracker__whoami",
                        "callId": call_id,
                        "arguments": {"note": note},
                    },
                )
            )

        allowed = call("call-1", "hello")
        assert allowed["success"] is True, allowed
        assert allowed["contentItems"][0]["text"] == "REMOTE_RESULT hello"
        pre, post = _log(project)
        assert (pre["hook_event_name"], pre["tool_name"]) == (
            "PreToolUse",
            "mcp__tracker__whoami",
        )
        assert pre["tool_input"] == {"note": "hello"}
        assert post["hook_event_name"] == "PostToolUse"
        assert post["tool_response"]["content"][0]["text"] == "REMOTE_RESULT hello"

        reached = len(calls)
        denied = call("call-2", "FORBIDDEN")
        assert denied["success"] is False
        assert "PROJECT_POLICY: no forbidden notes" in denied["contentItems"][0]["text"]
        assert len(calls) == reached, "a denied call never reaches the server"
        assert [e["hook_event_name"] for e in _log(project)[2:]] == ["PreToolUse"]
    finally:
        subprocess.run(executor.command("stop"), capture_output=True, timeout=30)


@pytest.mark.skipif(
    sys.platform != "linux", reason="the session's namespace is Linux's"
)
def test_claude_code_fires_the_projects_hooks_around_a_remote_call(
    acceptance, platform, tmp_path, monkeypatch
):
    """The build fires the hooks it registered from the project, whatever the
    server; their commands run on the machine through the shell prefix."""
    sys.path.insert(0, str(SCRIPTS))
    import headless_contract as contract

    from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS

    api, calls = platform
    folder = tmp_path / "room"
    release = acceptance.execution_release
    monkeypatch.setattr(release, "mount_state", lambda _path: release.MOUNT_LIVE)
    executor, target, project = _machine(acceptance, folder, monkeypatch)
    target = {**target, "remote_mcp": {"path": "/topics/t/mcp", "servers": ["tracker"]}}
    home = folder / "home"
    (home / ".claude").mkdir(parents=True)
    launch = acceptance.client.prepare(
        home / "session",
        target,
        claude=claude_binary(),
        home_override=home,
        config_override=home / ".claude",
    )
    session = contract.Session(
        claude_binary(),
        tmp_path,
        "room",
        LAUNCH_ARGS,
        launch=launch,
        env={"CHEESE_API": api, "CHEESE_TOKEN": "room-token"},
    )
    session.executor = executor
    session.remote_workspace = project
    try:

        def run(note):
            mark = session.user(contract.do("mcp__tracker__whoami", note=note))
            _, result = session.wait(contract.is_("result"), 90, mark)
            assert result is not None, (session.root / "stderr.log").read_text()[-2000:]
            (outcome,) = contract.results_since(session, mark)
            return outcome

        allowed = run("hello")
        assert allowed.get("is_error") is not True, allowed
        assert "REMOTE_RESULT hello" in contract.text_of(allowed)
        events = [(e["hook_event_name"], e["tool_name"]) for e in _log(project)]
        assert events == [
            ("PreToolUse", "mcp__tracker__whoami"),
            ("PostToolUse", "mcp__tracker__whoami"),
        ], events
        assert "REMOTE_RESULT hello" in json.dumps(_log(project)[1]["tool_response"])

        reached = len([c for c in calls if c["method"] == "tools/call"])
        denied = run("FORBIDDEN")
        assert denied.get("is_error") is True, denied
        assert "PROJECT_POLICY: no forbidden notes" in contract.text_of(denied)
        assert len([c for c in calls if c["method"] == "tools/call"]) == reached
    finally:
        contract.stop_remote(session)
        sys.path.remove(str(SCRIPTS))
