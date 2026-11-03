"""A Codex room lists and calls the checkout's `.mcp.json` stdio servers.

A repository whose `.mcp.json` names a stdio server gets its tools in a Claude
Code room and in a pi room. A Codex room is started before it holds its
machine's lease, with a target that names no machine server; the lease is what
names them. So a Codex session started on its leased machine takes the lease
before it opens, as Claude Code's session does, and its own MCP client then
reaches the server at the session's gateway; the server runs on the machine
with the project's hooks around its calls.

The machine is a real executor over a checkout whose `.mcp.json` and
`.claude/settings.json` are the repository's own. The platform's side is a
stand-in for the work-lease route and the executor route it names.
"""

import asyncio
import json
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent.executor_transport import DEFERRED_WORKSPACE
from app.domain.agent.harness.claude_code.remote_execution import runtime
from app.domain.agent.harness.claude_code.remote_execution.bootstrap import (
    process_servers,
)
from app.domain.agent.harness.codex.tools import RemoteTools
from tests.pinned_claude import claude_binary
from tests.support import executor_release
from tests.support.mcp_gateway_client import call_tool, text_of, tool_names

# The repository's server: it answers with where it runs and what it was asked,
# and leaves a mark for every call it receives.
SERVER = """import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request['method']
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'notes', 'version': '1'}}
    elif method == 'tools/list':
        schema = {'type': 'object', 'properties': {'note': {'type': 'string'}}}
        result = {'tools': [{'name': 'where', 'description': 'Where it runs.',
                             'inputSchema': schema}]}
    elif method == 'tools/call':
        note = request['params']['arguments'].get('note', '')
        with open('server-calls.txt', 'a') as calls:
            calls.write(note + '\\n')
        text = 'CHECKOUT_SERVER ' + os.environ.get('NOTES_MARK', '') + ' ' + note
        result = {'content': [{'type': 'text', 'text': text + ' @' + os.getcwd()}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}),
          flush=True)
"""

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
            "matcher": "mcp__notes__.*",
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
            "matcher": "mcp__notes__.*",
            "hooks": [
                {"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/.claude/log.sh'}
            ],
        }
    ],
}


@pytest.fixture
def machine(tmp_path):
    """A room's machine whose checkout declares `notes` in its `.mcp.json`; the
    executor is started with that file's servers, as the machine's bootstrap
    starts it."""
    home = tmp_path / "machine home"
    helper = executor_release.install(home / ".cheese")
    state = home / ".cheese/executor"
    work = tmp_path / "room"
    (work / "tools").mkdir(parents=True)
    (work / "tools/notes.py").write_text(SERVER)
    (work / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "notes": {
                        "command": sys.executable,
                        "args": ["tools/notes.py"],
                        "env": {"NOTES_MARK": "from-the-checkout"},
                    }
                }
            }
        )
    )
    (work / ".claude").mkdir()
    for name, script in (("log.sh", LOG), ("guard.sh", GUARD)):
        (work / ".claude" / name).write_text(script)
        (work / ".claude" / name).chmod(0o755)
    (work / ".claude/settings.json").write_text(json.dumps({"hooks": HOOKS}))
    subprocess.run(
        [sys.executable, str(helper), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(work),
                "claude": claude_binary(),
                "env": {},
                "mcp_servers": json.loads((work / ".mcp.json").read_text())[
                    "mcpServers"
                ],
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    try:
        yield work, state
    finally:
        subprocess.run(
            [sys.executable, str(helper), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


@pytest.fixture
def lease_answers():
    """What the work-lease route answers to each ask, in order; once these run
    out, the machine."""
    return []


@pytest.fixture
def platform(machine, monkeypatch, lease_answers):
    """The work-lease route, answering with the machine as the platform
    records it once its executor started (`session_work`): the stdio servers
    the machine's bootstrap reports from the checkout's `.mcp.json`. And the
    executor route to it, which admits only the lease's credential."""
    work, state = machine
    declared = json.loads((work / ".mcp.json").read_text())["mcpServers"]
    seen: list[str] = []
    generation = str(uuid.uuid4())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # the stdlib's name
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/lease" and lease_answers:
                seen.append("lease")
                result = {"data": lease_answers.pop(0)}
            elif self.path == "/lease":
                seen.append("lease")
                result = {
                    "data": {
                        "target": {
                            "kind": "device",
                            "workspace": str(work),
                            "url": base + "/execute",
                            "generation": generation,
                            "mcp_servers": process_servers({"mcp_servers": declared}),
                        },
                        "token": "execution-only",
                    }
                }
            else:
                assert self.headers["Authorization"] == "Bearer execution-only"
                seen.append(body["method"])
                result = runtime.request(state, body["method"], body["params"])
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("CHEESE_API", base)
    monkeypatch.setenv("CHEESE_TOKEN", "session-token")
    try:
        yield seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _session(workspace: str) -> dict:
    """The target a room's Codex session is started with (`central_provider`):
    it names no machine server."""
    return {
        "kind": "deferred",
        "resource_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "lease_path": "/lease",
        "setup_env": {},
        "workspace": workspace,
        "mcp_servers": [],
    }


def _log(work: Path) -> list[dict]:
    path = work / "hook-log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_codex_room_lists_and_calls_the_checkouts_server(machine, platform):
    work, _ = machine
    tools = RemoteTools(_session(str(work)))
    asyncio.run(tools.discover())
    assert platform[0] == "lease", platform
    assert "notes" in tools.mcp_servers
    gateway = tools.gateway()
    gateway.start()
    try:
        assert tool_names(gateway, "notes") == ["where"]

        allowed = call_tool(gateway, "notes", "where", {"note": "hello"})
        assert not allowed.get("isError"), allowed
        assert text_of(allowed) == f"CHECKOUT_SERVER from-the-checkout hello @{work}"
        pre, post = _log(work)
        assert (pre["hook_event_name"], pre["tool_name"]) == (
            "PreToolUse",
            "mcp__notes__where",
        )
        assert pre["tool_input"] == {"note": "hello"}
        assert (post["hook_event_name"], post["tool_name"]) == (
            "PostToolUse",
            "mcp__notes__where",
        )

        denied = call_tool(gateway, "notes", "where", {"note": "FORBIDDEN"})
        assert denied["isError"] is True
        assert "PROJECT_POLICY: no forbidden notes" in text_of(denied)
    finally:
        gateway.close()
    assert [e["hook_event_name"] for e in _log(work)[2:]] == ["PreToolUse"]
    assert (work / "server-calls.txt").read_text() == "hello\n", (
        "a denied call never reaches the server"
    )


def test_a_codex_session_at_the_placeholder_lists_no_checkout_server(machine, platform):
    """Before it is relaunched onto its machine, a session lists none of the
    machine's stdio servers, as on every harness."""
    tools = RemoteTools(_session(DEFERRED_WORKSPACE))
    asyncio.run(tools.discover())
    assert "notes" not in tools.mcp_servers


def test_a_codex_session_whose_machine_is_being_prepared_starts_at_the_placeholder(
    machine, platform, lease_answers
):
    """A Codex session relaunched onto a machine the platform cannot hand out
    at that moment (its sandbox being prepared again) still starts, as Claude
    Code's does: at the placeholder, listing none of the machine's stdio
    servers, its own tools taking the machine once it is there."""
    work, _ = machine
    lease_answers.append({"unavailable": "环境仍在准备", "preparing": True})
    tools = RemoteTools(_session(str(work)))
    names = [tool["name"] for tool in asyncio.run(tools.discover())]
    assert "Bash" in names
    assert "notes" not in tools.mcp_servers
