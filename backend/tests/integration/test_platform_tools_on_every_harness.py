"""The room's checklist, message edits and the silence rule are the same on
every harness.

`todo_write`, `chat_send` and `chat_edit` are platform tools, and each harness
reaches the backend through code of its own: Claude Code through the plugin
module that takes the model's tool call (`proxy.js`) and the session's MCP
transport process it hands the call to, Codex through the handler app-server
calls for a dynamic tool, pi through its runner answering the extension's `cli`
request. Each is run here the way production runs it, down to its own HTTP
client. The only stand-in is the network: a relay on localhost that hands each
request to this app.

What is checked is what the room gets — the live frame, the stored checklist,
the edited message, and whether the silence reminder is due — so a harness
whose path lands somewhere else, or not at all, fails here by name.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.api.deps import get_chat_service
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness.claude_code.remote_execution import client as central
from app.domain.agent.harness.claude_code.remote_execution import (
    mcp_process,
    release,
)
from app.domain.agent.harness.codex.tools import RemoteTools
from app.domain.agent.harness.pi.machine import Machine
from app.domain.agent.harness.pi.runner import Runner as PiRunner
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
)
from tests.support.room_machine import NO_MACHINE

CLI = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
HARNESSES = ["claude-code", "codex", "pi"]
PLAN = [
    {"content": "读现有实现", "status": "completed"},
    {"content": "改接口", "status": "in_progress"},
    {"content": "补测试", "status": "pending"},
]


# The build's side of a Claude Code tool call: load the plugin module, hand it
# the call exactly as the model made it (`mcp__native__<tool>` plus the build's
# `tool_use_id`), and carry each `$.mcp.call` it makes to the transport process
# over this script's stdio. Falling through to `next` means the build would
# call the tool itself, without the `id` the transport needs.
_PROXY_CALL = """
import readline from 'node:readline';
import {readFile} from 'node:fs/promises';
const {register} = await import('data:text/javascript;base64,' + process.argv[1]);
const [tool, args] = JSON.parse(process.argv[2]);
const replies = readline.createInterface({input: process.stdin})
  [Symbol.asyncIterator]();
const handlers = {};
register((event, handler) => { handlers[event] = handler; });
const $ = {
  session: {id: async () => 'fixture'},
  env: {get: async () => undefined},
  mcp: {call: async (server, name, params) => {
    process.stdout.write(JSON.stringify({server, name, arguments: params}) + '\\n');
    return JSON.parse((await replies.next()).value);
  }},
  fs: {read: async (path) => readFile(path, 'utf8')},
};
const outcome = await handlers['tool.call']($, {
  tool: 'mcp__native__' + tool, tool_use_id: 'toolu_fixture', ...args,
}, async () => ({deny: 'fell through to the build, which calls it without an id'}));
process.stdout.write(JSON.stringify({outcome}) + '\\n');
"""


def _relay(client) -> ThreadingHTTPServer:
    """localhost → this app, request for request."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def _relay(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() in ("content-type", "x-cheese-token", "x-cheese-turn")
            }
            response = client.request(
                self.command, self.path, content=body, headers=headers
            )
            data = response.content
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _relay
        do_POST = _relay
        do_PUT = _relay
        do_PATCH = _relay

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def room(client):
    project = post_project(client, json={"name": "Harnesses"}, owner="alice").json()[
        "data"
    ]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
    ).json()["data"]
    # 芝士 answers in a 支线 of the channel: its tools act there.
    return project["id"], in_thread(client, topic["id"], "alice")


@pytest.fixture(params=HARNESSES)
def harness(request, client, room, tmp_path, monkeypatch):
    """`call(tool, arguments) -> text` through one harness's own tool path."""
    project, topic = room
    relay = _relay(client)
    env = {
        "NO_PROXY": "127.0.0.1",
        "no_proxy": "127.0.0.1",
        "CHEESE_API": f"http://127.0.0.1:{relay.server_port}",
        "CHEESE_TOKEN": mint_scoped_token(project_id=project, topic_id=topic),
        "CHEESE_TOPIC": topic,
        "CHEESE_PROJECT": project,
    }
    closers = [relay.shutdown, relay.server_close]
    if request.param == "claude-code":
        config = tmp_path / "central.json"
        config.write_text(
            json.dumps(
                {
                    "workspace": str(tmp_path),
                    "central_hooks": {},
                    "kind": "unavailable",
                }
            )
        )
        log = (tmp_path / "central.log").open("w")
        process = mcp_process.MCPProcess(
            [sys.executable, central.__file__, "transport", str(config)],
            str(tmp_path),
            {**os.environ, **env},
            log,
        )
        closers[:0] = [process.close, log.close]
        module = release.hook_module(
            Path(central.__file__).with_name("proxy.js").read_text(),
            {"central_config": str(tmp_path), "session_workspace": str(tmp_path)},
            release.platform_tool_names(CLI.read_text()),
        )

        def call(tool, arguments):
            node = subprocess.Popen(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    _PROXY_CALL,
                    base64.b64encode(module.encode()).decode(),
                    json.dumps([tool, arguments]),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            for line in node.stdout:
                message = json.loads(line)
                if "outcome" in message:
                    break
                assert message["server"] == "native", message
                try:
                    reply = process.call(
                        "tools/call",
                        {"name": message["name"], "arguments": message["arguments"]},
                    )
                except RuntimeError as error:
                    reply = {
                        "isError": True,
                        "content": [{"type": "text", "text": str(error)}],
                    }
                node.stdin.write(json.dumps(reply) + "\n")
                node.stdin.flush()
            node.stdin.close()
            assert node.wait(timeout=30) == 0
            outcome = message["outcome"]
            assert "deny" not in outcome, outcome
            return "".join(block["text"] for block in outcome["result"])

    elif request.param == "codex":
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        tools = RemoteTools({"kind": "unavailable"})
        # The executor lists no native tools here; what app-server is handed is
        # then exactly the platform's table.
        tools.client.call = lambda method, params: {"tools": []}
        listed = asyncio.run(tools.discover())
        assert {"todo_write", "chat_send", "chat_edit"} <= {
            tool["name"] for tool in listed
        }

        def call(tool, arguments):
            result = asyncio.run(
                tools(
                    "item/tool/call",
                    {"tool": tool, "callId": str(uuid.uuid4()), "arguments": arguments},
                )
            )
            assert result["success"], result
            return result["contentItems"][0]["text"]

    else:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        # Platform tools take no machine: the room's has none yet.
        runner = PiRunner(tmp_path / "pi")
        runner.machine = Machine(NO_MACHINE)

        def call(tool, arguments):
            result = asyncio.run(
                runner.dispatch(
                    "cli", {"tool": tool, "arguments": arguments, "cwd": str(tmp_path)}
                )
            )
            assert result["status"] == 0, result
            return result["stdout"]

    yield call
    for close in closers:
        close()


def _next(ws, kind: str) -> dict:
    frame = ws.receive_json()
    while frame["type"] != kind:
        frame = ws.receive_json()
    return frame["block"]


def test_a_checklist_reaches_the_room_the_same_way(client, room, harness):
    _, topic = room
    with room_socket(client, topic, "alice") as ws:
        said = harness("todo_write", {"todos": PLAN})
        message = _next(ws, "assistant_block")
    assert message["content"] == "✓ 读现有实现\n✱ 改接口\n○ 补测试"
    expected = [(todo["content"], todo["status"]) for todo in PLAN]
    stored = client.get(f"/topics/{topic}/progress").json()["data"]["items"]
    assert [(i["subject"], i["status"]) for i in stored] == expected
    assert "3 项" in said and "完成 1 项" in said


def test_an_edit_reaches_the_room_the_same_way(client, room, harness):
    _, topic = room
    harness("chat_send", {"content": "先看 issue"})
    blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
    [sent] = [b for b in blocks if b["content"] == "先看 issue"]
    with room_socket(client, topic, "alice") as ws:
        harness(
            "chat_edit", {"message_id": sent["id"], "content": "先看 issue，再写测试"}
        )
        edited = _next(ws, "block_updated")
    assert edited["id"] == sent["id"]
    assert edited["content"] == "先看 issue，再写测试"
    assert edited["meta"]["edited_at"]


def test_a_message_through_any_harness_silences_the_reminder(
    client, stub_hooks, room, harness, monkeypatch
):
    """`remind_silent_turns` picks the turns that owe the room a word by when it
    last heard one. That clock moves when `chat_send` lands, so it has to land
    the same way from every harness — or one harness is reminded forever, or
    never."""
    from app.domain.agent import chat as chat_module
    from app.domain.agent.room import turn as turn_module

    _, topic = room
    chat = client.app.dependency_overrides[get_chat_service]()
    clock = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock

    monkeypatch.setattr(chat_module, "datetime", Clock)
    monkeypatch.setattr(turn_module, "datetime", Clock)
    threshold = settings.chat_progress_reminder_after_s
    notices: list[str] = []

    async def notice(topic_id, text):
        notices.append(text)
        return True

    monkeypatch.setattr(chat, "notify_running_turn", notice)

    def begin(topic_id, prompt, reply, agent=None):
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.says(topic_id, "Internal output")

    monkeypatch.setattr(stub_hooks, "emit_turn", begin)
    with room_socket(client, topic, "alice") as ws:
        post_message(client, topic, "alice", {"content": "@芝士 检查一下"})
        while True:
            frame = ws.receive_json()
            if frame["type"] == "event_block" and (
                frame["block"]["content"] == "Internal output"
            ):
                break
        clock += timedelta(seconds=threshold)
        assert client.portal.call(chat.remind_silent_turns) == 1
        assert "chat_send" in notices[0] and "todo_write" in notices[0]

        harness("chat_send", {"content": "我先核对一下当前流程。"})
        assert client.portal.call(chat.remind_silent_turns) == 0
        # A checklist is not a word to the room: it does not reset the clock.
        harness("todo_write", {"todos": PLAN})
        clock += timedelta(seconds=threshold)
        assert client.portal.call(chat.remind_silent_turns) == 1
        assert len(notices) == 2

        client.portal.call(stub_hooks.stops, uuid.UUID(topic), "Finished")
        while ws.receive_json()["type"] != "done":
            pass
