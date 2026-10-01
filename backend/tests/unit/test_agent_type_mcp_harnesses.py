"""A teammate's type's own stdio MCP server, on every harness.

A type declares its servers as a Claude Code subagent does (`mcpServers`), and
the platform hands a session of a teammate of that type the inline stdio ones
as definitions (`agent_mcp` in its execution target, or pi's launch). The
server runs on the room's machine, as the checkout's `.mcp.json` servers do,
and the project's own hooks hold around its calls.

What is under test is the session side and a real executor: that each harness
lists the tool as `mcp__<server>__<tool>`, that a call runs on the machine,
and which hooks ran around it. The machine's checkout never names the server.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.pinned_claude import claude_binary

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/remote_execution"

# The type's server: it answers with where it runs and what it was asked.
SERVER = """import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request['method']
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'lint', 'version': '1'}}
    elif method == 'tools/list':
        schema = {'type': 'object', 'properties': {'note': {'type': 'string'}}}
        result = {'tools': [{'name': 'where', 'description': 'Where it runs.',
                             'inputSchema': schema}]}
    elif method == 'tools/call':
        note = request['params']['arguments'].get('note', '')
        text = 'TYPE_SERVER ' + os.environ.get('LINT_MARK', '') + ' ' + note
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
            "matcher": "mcp__lint__.*",
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
            "matcher": "mcp__lint__.*",
            "hooks": [
                {"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/.claude/log.sh'}
            ],
        }
    ],
}


def _definition(folder: Path) -> dict:
    """The server as the type file defines it: outside the checkout."""
    script = folder / "type-servers" / "lint.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(SERVER)
    return {
        "command": sys.executable,
        "args": [str(script)],
        "env": {"LINT_MARK": "from-the-type"},
    }


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
    """A room's machine over a project whose settings hold HOOKS, and whose
    `.mcp.json` does not name the type's server."""
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
    (project / ".claude").mkdir(exist_ok=True)
    for name, script in (("log.sh", LOG), ("guard.sh", GUARD)):
        (project / ".claude" / name).write_text(script)
        (project / ".claude" / name).chmod(0o755)
    (project / ".claude/settings.json").write_text(json.dumps({"hooks": HOOKS}))
    assert "lint" not in json.dumps(target["mcp_servers"])
    return executor, target, project


def _log(project: Path) -> list[dict]:
    path = project / "hook-log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_codex_lists_and_calls_the_types_server_on_the_machine(
    acceptance, tmp_path, monkeypatch
):
    from app.domain.agent.harness.codex.tools import RemoteTools

    executor, target, project = _machine(acceptance, tmp_path / "codex", monkeypatch)
    own = {**target, "agent_mcp": {"lint": _definition(tmp_path)}}
    try:
        # A teammate of another type, on the same machine, has no such tool.
        other = RemoteTools(dict(target))
        listed = asyncio.run(other.discover())
        assert "mcp__lint__where" not in [tool["name"] for tool in listed]

        tools = RemoteTools(own)
        names = [tool["name"] for tool in asyncio.run(tools.discover())]
        assert "mcp__lint__where" in names

        def call(call_id, note):
            return asyncio.run(
                tools(
                    "item/tool/call",
                    {
                        "tool": "mcp__lint__where",
                        "callId": call_id,
                        "arguments": {"note": note},
                    },
                )
            )

        allowed = call("call-1", "hello")
        assert allowed["success"] is True, allowed
        assert allowed["contentItems"][0]["text"] == (
            f"TYPE_SERVER from-the-type hello @{project}"
        )
        pre, post = _log(project)
        assert (pre["hook_event_name"], pre["tool_name"]) == (
            "PreToolUse",
            "mcp__lint__where",
        )
        assert pre["tool_input"] == {"note": "hello"}
        assert post["hook_event_name"] == "PostToolUse"

        denied = call("call-2", "FORBIDDEN")
        assert denied["success"] is False
        assert "PROJECT_POLICY: no forbidden notes" in denied["contentItems"][0]["text"]
        assert [e["hook_event_name"] for e in _log(project)[2:]] == ["PreToolUse"]
    finally:
        subprocess.run(executor.command("stop"), capture_output=True, timeout=30)


def test_a_checkout_server_of_the_same_name_keeps_its_name(tmp_path):
    """Committed configuration decides which server a name reaches: the type's
    definition never replaces the machine's `.mcp.json` entry of that name."""
    from app.domain.agent.executor_transport import RemoteClient

    client = RemoteClient(
        {
            "mcp_servers": ["lint"],
            "agent_mcp": {"lint": _definition(tmp_path), "own": _definition(tmp_path)},
        }
    )
    assert client.session_servers() == ["lint", "own"]
    assert client.agent_servers() == ["own"]


def test_a_session_at_the_placeholder_lists_no_stdio_server():
    """As with the checkout's servers, a session that has not taken the machine
    lists none: listing one would take the machine for a turn that may never
    need it. Remote servers are listed from the start."""
    from app.domain.agent.executor_transport import DEFERRED_WORKSPACE, RemoteClient

    placeholder = {
        "kind": "deferred",
        "workspace": DEFERRED_WORKSPACE,
        "mcp_servers": [],
        "agent_mcp": {"lint": {"command": "x"}},
        "remote_mcp": {"path": "/topics/t/mcp", "servers": ["tracker"]},
    }
    assert RemoteClient(placeholder).session_servers() == ["tracker"]
    on_machine = {**placeholder, "workspace": "/home/room/work"}
    assert RemoteClient(on_machine).session_servers() == ["lint", "tracker"]


def test_pi_runs_the_types_server_beside_the_checkouts(tmp_path, monkeypatch):
    """pi's runner is its MCP client: the room's machine starts the type's
    server as it starts the checkout's, and runs the project's hooks around
    the call."""
    from app.domain.agent.harness.pi.machine import Machine
    from app.domain.agent.harness.pi.runner import Runner
    from tests.support.room_machine import room_machine

    work = tmp_path / "room"
    (work / ".claude").mkdir(parents=True)
    for name, script in (("log.sh", LOG), ("guard.sh", GUARD)):
        (work / ".claude" / name).write_text(script)
        (work / ".claude" / name).chmod(0o755)
    (work / ".claude/settings.json").write_text(json.dumps({"hooks": HOOKS}))
    runner = Runner(tmp_path / "pi")

    async def session():
        with room_machine(tmp_path / "machine", checkout=work) as target:
            runner.machine = Machine(
                {**target, "agent_mcp": {"lint": _definition(tmp_path)}}
            )
            try:
                await runner.open_servers()
                home = runner.write_extension(
                    {"index.ts": "export default function () {}\n"}
                )
                answers: list[object] = []
                for index, note in enumerate(("hello", "FORBIDDEN")):
                    try:
                        answers.append(
                            await runner.dispatch(
                                "mcp",
                                {
                                    "id": f"call-{index}",
                                    "tool": "mcp__lint__where",
                                    "arguments": {"note": note},
                                },
                            )
                        )
                    except Exception as error:  # noqa: BLE001 — the answer under test
                        answers.append(error)
                return (home / "platform.json").read_text(), answers
            finally:
                await runner.close()

    manifest, (allowed, denied) = asyncio.run(session())
    assert [tool["name"] for tool in json.loads(manifest)["mcp"]] == [
        "mcp__lint__where"
    ]
    assert allowed == {
        "content": [
            {"type": "text", "text": f"TYPE_SERVER from-the-type hello @{work}"}
        ],
        "isError": False,
    }
    assert isinstance(denied, RuntimeError)
    assert "PROJECT_POLICY: no forbidden notes" in str(denied)
    assert [(e["hook_event_name"], e["tool_input"]["note"]) for e in _log(work)] == [
        ("PreToolUse", "hello"),
        ("PostToolUse", "hello"),
        ("PreToolUse", "FORBIDDEN"),
    ]


@pytest.mark.skipif(
    sys.platform != "linux", reason="the session's namespace is Linux's"
)
def test_claude_code_lists_and_calls_the_types_server_on_the_machine(
    acceptance, tmp_path, monkeypatch
):
    """The session bridges the type's server like a checkout's; the build fires
    the project's hooks around its call, and the machine runs it."""
    sys.path.insert(0, str(SCRIPTS))
    import headless_contract as contract

    from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS

    folder = tmp_path / "room"
    release = acceptance.execution_release
    monkeypatch.setattr(release, "mount_state", lambda _path: release.MOUNT_LIVE)
    executor, target, project = _machine(acceptance, folder, monkeypatch)
    target = {**target, "agent_mcp": {"lint": _definition(tmp_path)}}
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
        claude_binary(), tmp_path, "room", LAUNCH_ARGS, launch=launch
    )
    session.executor = executor
    session.remote_workspace = project
    try:

        def run(note):
            mark = session.user(contract.do("mcp__lint__where", note=note))
            _, result = session.wait(contract.is_("result"), 90, mark)
            assert result is not None, (session.root / "stderr.log").read_text()[-2000:]
            (outcome,) = contract.results_since(session, mark)
            return outcome

        allowed = run("hello")
        assert allowed.get("is_error") is not True, allowed
        assert f"TYPE_SERVER from-the-type hello @{project}" in contract.text_of(
            allowed
        )
        events = [(e["hook_event_name"], e["tool_name"]) for e in _log(project)]
        assert events == [
            ("PreToolUse", "mcp__lint__where"),
            ("PostToolUse", "mcp__lint__where"),
        ], events

        denied = run("FORBIDDEN")
        assert denied.get("is_error") is True, denied
        assert "PROJECT_POLICY: no forbidden notes" in contract.text_of(denied)
    finally:
        contract.stop_remote(session)
        sys.path.remove(str(SCRIPTS))


def test_two_pi_sessions_on_one_machine_each_get_their_own_answer(tmp_path):
    """pi numbers its tool calls per session, so two sessions on one room's
    machine can both make a `call-0`. The machine answers each with its own
    call, never with what it answered the other one."""
    from app.domain.agent.harness.pi.machine import Machine
    from app.domain.agent.harness.pi.runner import Runner
    from tests.support.room_machine import room_machine

    work = tmp_path / "room"
    work.mkdir()

    async def ask(name: str, target: dict, note: str):
        runner = Runner(tmp_path / name)
        runner.journal.remember("session_id", f"session-{name}")
        runner.machine = Machine(
            {**target, "agent_mcp": {"lint": _definition(tmp_path)}}
        )
        try:
            await runner.open_servers()
            return await runner.dispatch(
                "mcp",
                {
                    "id": "call-0",
                    "tool": "mcp__lint__where",
                    "arguments": {"note": note},
                },
            )
        finally:
            await runner.close()

    async def both():
        with room_machine(tmp_path / "machine", checkout=work) as target:
            return await ask("one", target, "first"), await ask("two", target, "second")

    first, second = asyncio.run(both())
    assert "first" in first["content"][0]["text"]
    assert "second" in second["content"][0]["text"]
