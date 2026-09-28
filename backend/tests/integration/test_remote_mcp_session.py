"""A room's session calls a connected remote MCP server, and never holds its token.

The session side is the real bridge Claude Code starts from its MCP config
(`remote_execution/client.py bridge`), run as its own process on stdio, with
the execution target the platform hands a session. It reaches the backend over
HTTP, as it does on the session host; the backend answers from the in-process
app, which calls the fake upstream on a real port.
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
from app.domain.remote_mcp import service
from tests.integration import test_remote_mcp as base

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
            return await service.session_target(session, pid, tid)

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
    from tests.integration.conftest import chat_ws_url

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": text})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


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
    pid = _project(client, upstream)
    tid = _topic(client, pid)

    _turn(client, tid, "@芝士 看一下任务")
    prompt = stub_hooks.last_system_prompt or ""
    assert "tracker、search 需要项目成员在项目设置里连接" in prompt
    _turn(client, tid, "@芝士 再看一下")
    assert sorted(_notices(client, tid)) == [
        "search 需要在项目设置里连接",
        "tracker 需要在项目设置里连接",
    ], "said once per server, not once per turn"

    _connect(client, pid)
    _turn(client, tid, "@芝士 现在呢")
    lines = [
        line
        for line in (stub_hooks.last_system_prompt or "").splitlines()
        if "远程 MCP 服务器" in line
    ]
    assert lines == [
        "- 项目的远程 MCP 服务器 search 需要项目成员在项目设置里连接，"
        "这个会话里用不了它们的工具。"
    ]
