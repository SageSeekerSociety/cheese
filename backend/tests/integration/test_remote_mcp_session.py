"""A room's session calls a connected remote MCP server, and never holds its token.

The session side is what each harness runs on its machine: for Claude Code the
real bridge it starts from its MCP config (`remote_execution/client.py
bridge`), run as its own process on stdio, with the execution target the
platform hands a session; for pi its runner answering the extension's `mcp`
request, started with the launch `PiChannel` hands the machine. Both reach the
backend over HTTP, as they do on the machine; the backend answers from the
in-process app, which calls the fake upstream on a real port.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.executor_transport import RemoteClient
from app.domain.agent.harness.pi.machine import Machine
from app.domain.agent.harness.pi.runner import Runner as PiRunner
from app.domain.remote_mcp import service
from tests.integration import test_remote_mcp as base
from tests.support.room_machine import room_machine

# The same upstream and project as the settings tests.
upstream = base.upstream
_local_upstream = base._local_upstream
_connect, _project, _topic = base._connect, base._project, base._topic

CLIENT = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution/client.py"
)


@pytest.fixture
def backend_over_http(client):
    """The backend on a real port, as the session host reaches it."""
    requests: list[str] = []

    class Relay(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — the stdlib's name
            requests.append(self.path)
            body = self.rfile.read(int(self.headers["Content-Length"]))
            answer = client.post(
                self.path,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Cheese-Token": self.headers["X-Cheese-Token"],
                },
            )
            self.send_response(answer.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(answer.content)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", requests
    server.shutdown()


def _target(client, pid, tid, tmp_path) -> Path:
    """The execution target a session in this room starts with, as the
    platform composes it for a session that has not taken a machine yet."""

    async def compose():
        async with client.test_factory() as session:
            # The project default's session: no type of its own.
            return await service.session_target(session, pid, tid, None)

    remote = asyncio.run(compose())
    target = {
        "kind": "deferred",
        "lease_path": f"/topics/{tid}/sessions/unused/work-lease",
        "workspace": "/workspace/room",
        "mcp_servers": [],
        **({"remote_mcp": remote} if remote else {}),
    }
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target))
    return path


def _bridge(target: Path, name: str, api: str, token: str, messages: list[dict]):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CHEESE_", "HTTP"))}
    env.update(CHEESE_API=api, CHEESE_TOKEN=token, NO_PROXY="127.0.0.1")
    done = subprocess.run(
        [sys.executable, str(CLIENT), "bridge", str(target), name],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    replies = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    return {reply["id"]: reply for reply in replies}, done


def test_a_session_calls_the_server_without_ever_holding_its_token(
    client, upstream, backend_over_http, tmp_path
):
    api, requests = backend_over_http
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    assert (
        json.loads(_target(client, pid, tid, tmp_path).read_text()).get("remote_mcp")
        is None
    ), "an unconnected server is not handed to the session"

    _connect(client, pid)
    target = _target(client, pid, tid, tmp_path)
    assert json.loads(target.read_text())["remote_mcp"]["servers"] == ["tracker"]

    room = mint_scoped_token(project_id=str(pid), topic_id=tid)
    replies, done = _bridge(
        target,
        "tracker",
        api,
        room,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "whoami", "arguments": {"note": "from a room"}},
            },
        ],
    )
    assert replies[2]["result"]["tools"][0]["name"] == "whoami", done.stderr
    text = replies[3]["result"]["content"][0]["text"]
    assert text == "reached with oauth; note=from a room"

    token = upstream.issued[-1]
    assert upstream.seen_credentials[-1] == f"Bearer {token}"
    # Nothing the session holds or receives carries it.
    assert token not in target.read_text()
    assert token not in done.stdout and token not in done.stderr
    assert token not in room
    # And no machine was taken for it: every request went to the proxy route.
    assert requests and all(path == f"/topics/{tid}/mcp/tracker" for path in requests)


# A stdio server a checkout declares: it answers with where it runs, and what
# it was asked, and keeps a list of the calls it received.
STDIO_SERVER = """import json, os, sys
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
        with open('calls.txt', 'a') as calls:
            calls.write(note + '\\n')
        result = {'content': [{'type': 'text', 'text': os.getcwd() + ' ' + note}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}),
          flush=True)
"""

# What the runner writes beside the manifest; its content is not the subject.
EXTENSION = {"index.ts": "export default function () {}\n"}


def _pi_room(client, upstream, api: str, tmp_path, monkeypatch):
    """A room whose project has a connected remote server, a checkout with a
    stdio server, and the runner's environment on the session host."""
    pid = _project(client, upstream)
    tid = _topic(client, pid)
    _connect(client, pid)
    remote = json.loads(_target(client, pid, tid, tmp_path).read_text())["remote_mcp"]
    work = tmp_path / "room"
    work.mkdir()
    (work / "notes.py").write_text(STDIO_SERVER)
    room = mint_scoped_token(project_id=str(pid), topic_id=tid)
    for name, value in (
        ("CHEESE_API", api),
        ("CHEESE_TOKEN", room),
        ("NO_PROXY", "127.0.0.1"),
    ):
        monkeypatch.setenv(name, value)
    return tid, remote, work


def _pi_calls(
    runner: PiRunner, tmp_path: Path, work: Path, remote: dict, calls: list[dict]
):
    """Open the runner's servers on a room machine whose checkout runs one stdio
    server, then answer the extension's `mcp` requests; an answer that failed
    comes back as its exception."""
    stdio = {"command": sys.executable, "args": [str(work / "notes.py")]}

    async def session():
        with room_machine(
            tmp_path / "machine",
            checkout=work,
            env={"NO_PROXY": "127.0.0.1"},
            mcp_servers={"notes": stdio},
        ) as target:
            runner.machine = Machine({**target, "remote_mcp": remote})
            try:
                await runner.open_servers()
                home = runner.write_extension(EXTENSION)
                answers: list[object] = []
                for index, call in enumerate(calls):
                    try:
                        answers.append(
                            await runner.dispatch(
                                "mcp", {"id": f"call-{index}", **call}
                            )
                        )
                    except Exception as error:  # noqa: BLE001 — the answer under test
                        answers.append(error)
                return (home / "platform.json").read_text(), answers
            finally:
                await runner.close()

    return asyncio.run(session())


def test_a_pi_session_calls_the_projects_servers_without_their_token(
    client, upstream, backend_over_http, tmp_path, monkeypatch
):
    """pi has no MCP client; its runner is one. It lists the machine's stdio
    server and the connected remote one as `mcp__<server>__<tool>`, calls each
    where it is served, and holds nothing that carries the upstream token."""
    api, requests = backend_over_http
    tid, remote, work = _pi_room(client, upstream, api, tmp_path, monkeypatch)
    # Only the connected server: `search` still needs a value set.
    assert remote == {"path": f"/topics/{tid}/mcp", "servers": ["tracker"]}

    manifest, (local, tracked) = _pi_calls(
        PiRunner(tmp_path / "pi"),
        tmp_path,
        work,
        remote,
        [
            {"tool": "mcp__notes__where", "arguments": {"note": "hi"}},
            {"tool": "mcp__tracker__whoami", "arguments": {"note": "from pi"}},
        ],
    )
    assert sorted(tool["name"] for tool in json.loads(manifest)["mcp"]) == [
        "mcp__notes__where",
        "mcp__tracker__whoami",
    ]
    # The stdio server ran on the machine, in its checkout.
    assert local == {
        "content": [{"type": "text", "text": f"{work} hi"}],
        "isError": False,
    }
    assert tracked == {
        "content": [{"type": "text", "text": "reached with oauth; note=from pi"}],
        "isError": False,
    }

    token = upstream.issued[-1]
    assert upstream.seen_credentials[-1] == f"Bearer {token}"
    assert token not in manifest
    assert token not in json.dumps([local, tracked])
    assert token not in json.dumps(dict(os.environ))
    # Every remote request went to the platform's route for the server.
    assert requests and all(path == f"/topics/{tid}/mcp/tracker" for path in requests)


# A hook that refuses one note, as a project's guard would.
GUARD = """import json, sys
event = json.load(sys.stdin)
if event['tool_input'].get('note') == 'forbidden':
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': 'notes like that stay out of the tracker'}}))
"""


def test_a_pi_sessions_mcp_calls_run_the_projects_hooks(
    client, upstream, backend_over_http, tmp_path, monkeypatch
):
    """pi fires no project hooks, so the room's machine runs them around every
    MCP call of its session, stdio or remote: PreToolUse before, and a deny
    means the server is never called; PostToolUse after, with the result."""
    api, _ = backend_over_http
    _, remote, work = _pi_room(client, upstream, api, tmp_path, monkeypatch)
    # One line per event: the event as the hook received it.
    log = (
        'cat >> "$CLAUDE_PROJECT_DIR/hooks.log"; '
        'echo >> "$CLAUDE_PROJECT_DIR/hooks.log"'
    )
    (work / "guard.py").write_text(GUARD)
    guard = f'{sys.executable} "$CLAUDE_PROJECT_DIR/guard.py"'
    (work / ".claude").mkdir()
    (work / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "mcp__.*",
                            "hooks": [
                                {"type": "command", "command": log},
                                {"type": "command", "command": guard},
                            ],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "mcp__.*",
                            "hooks": [{"type": "command", "command": log}],
                        }
                    ],
                }
            }
        )
    )
    reached_upstream = len(upstream.tool_calls)

    _, answers = _pi_calls(
        PiRunner(tmp_path / "pi"),
        tmp_path,
        work,
        remote,
        [
            {"tool": "mcp__notes__where", "arguments": {"note": "hi"}},
            {"tool": "mcp__tracker__whoami", "arguments": {"note": "from pi"}},
            {"tool": "mcp__notes__where", "arguments": {"note": "forbidden"}},
            {"tool": "mcp__tracker__whoami", "arguments": {"note": "forbidden"}},
        ],
    )

    assert [type(answer) for answer in answers[:2]] == [dict, dict]
    for denied in answers[2:]:
        assert isinstance(denied, RuntimeError)
        assert "notes like that stay out of the tracker" in str(denied)
    # A denied call reached neither server.
    assert (work / "calls.txt").read_text() == "hi\n"
    assert [c["arguments"]["note"] for c in upstream.tool_calls[reached_upstream:]] == [
        "from pi"
    ]
    events = [
        json.loads(line)
        for line in (work / "hooks.log").read_text().splitlines()
        if line.strip()
    ]
    assert [
        (e["hook_event_name"], e["tool_name"], e["tool_input"]["note"]) for e in events
    ] == [
        ("PreToolUse", "mcp__notes__where", "hi"),
        ("PostToolUse", "mcp__notes__where", "hi"),
        ("PreToolUse", "mcp__tracker__whoami", "from pi"),
        ("PostToolUse", "mcp__tracker__whoami", "from pi"),
        ("PreToolUse", "mcp__notes__where", "forbidden"),
        ("PreToolUse", "mcp__tracker__whoami", "forbidden"),
    ]
    # Paired by one id, which names the session as well as the call.
    assert events[0]["tool_use_id"] == events[1]["tool_use_id"]
    assert events[0]["tool_use_id"].endswith("-call-0")


def test_a_stdio_server_still_goes_to_the_rooms_machine(tmp_path):
    """The project's own stdio servers are unchanged: the bridge sends them to
    the executor, never to the platform's remote route."""
    config = {
        "kind": "unavailable",
        "workspace": "/workspace/room",
        "mcp_servers": ["local"],
        "remote_mcp": {"path": "/topics/t/mcp", "servers": ["tracker"]},
    }
    client = RemoteClient(config)
    client.platform_request = lambda _args: pytest.fail("not the platform's")  # type: ignore[method-assign]
    from app.domain.agent.executor_transport import MachineOutOfReach

    with pytest.raises(MachineOutOfReach):
        client.call("mcp", {"server": "local", "method": "tools/list"})


def test_the_machine_reports_only_the_servers_it_runs():
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    servers = {
        "local": {"command": "python3", "args": ["server.py"]},
        "tracker": {"type": "http", "url": "https://mcp.example.test/mcp"},
        "old": {"type": "sse", "url": "https://mcp.example.test/sse"},
    }
    assert bootstrap.process_servers({"mcp_servers": servers}) == ["local"]


def _turn(client, room: str, text: str) -> None:
    from tests.integration.conftest import chat_ws_url, post_message

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": text})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def _new_conversation(client, room: str) -> None:
    """The room's conversation is gone (a deploy, a recycled machine)."""
    import uuid

    from app.domain.agent_session.services import AgentSessionService

    async def forget() -> None:
        async with client.test_factory() as session:
            await AgentSessionService(session).forget_room(uuid.UUID(room))
            await session.commit()

    client.portal.call(forget)


def _notices(client, room: str) -> list[str]:
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    return [
        b["content"]
        for b in blocks
        if (b.get("meta") or {}).get("event_type") == "mcp_not_connected"
    ]


def test_an_unconnected_server_is_a_capability_line_and_one_room_notice(
    client, upstream, stub_hooks
):
    from tests.integration.conftest import in_thread

    pid = _project(client, upstream)
    tid = _topic(client, pid)
    # 芝士 answers in a 支线: the notice is said there, to the people talking
    # with it, once per server; the channel's main line hears nothing of it.
    thread = in_thread(client, tid, "alice")

    _turn(client, thread, "@芝士 看一下任务")
    prompt = stub_hooks.told
    assert "tracker、search 需要项目成员在项目设置里连接" in prompt
    _turn(client, thread, "@芝士 再看一下")
    assert sorted(_notices(client, thread)) == [
        "search 需要在项目设置里连接",
        "tracker 需要在项目设置里连接",
    ], "said once per server, not once per turn"
    assert _notices(client, tid) == []

    _connect(client, pid)
    # 没连上的服务器是会话开场时说的事：下一条新开的会话听到的是现在还没连的那些。
    _new_conversation(client, thread)
    _turn(client, thread, "@芝士 现在呢")
    lines = [line for line in stub_hooks.told.splitlines() if "远程 MCP 服务器" in line]
    assert lines == [
        "- 项目的远程 MCP 服务器 search 需要项目成员在项目设置里连接，"
        "这个会话里用不了它们的工具。"
    ]
