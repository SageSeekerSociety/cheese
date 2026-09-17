"""Exercise connector output limits and executor mutation receipts together."""

import asyncio
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent import execution, executor_transport
from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.harness.claude_code.remote_execution import cli_worker, runtime
from app.domain.agent.harness.claude_code.remote_execution import client as central
from app.domain.agent.harness.claude_code.remote_execution.client import (
    _local_chat_send_argv,
)
from app.domain.agent.harness.codex.tools import RemoteTools


def test_device_requests_read_the_current_room_token_file(tmp_path, monkeypatch):
    token = tmp_path / "execution.token"
    token.write_text("first")
    token.chmod(0o600)
    client = executor_transport.RemoteClient(
        {"kind": "device", "url": "http://executor.test", "token_file": str(token)}
    )
    seen = []

    class Response:
        status = 200

        @staticmethod
        def read():
            return b"{}"

    class Connection:
        sock = None

        def request(self, method, path, *, body, headers):
            seen.append(headers["X-Cheese-Token"])

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            pass

    monkeypatch.setattr(client, "connection", lambda: (Connection(), "/execution"))
    client.transport.headers = {}
    monkeypatch.delenv("CHEESE_TOKEN", raising=False)
    client.call("context_fs")
    token.write_text("rotated")
    client.call("context_fs")
    assert seen == ["first", "rotated"]


def test_chat_publication_fast_path_accepts_only_standalone_cli_invocations():
    assert _local_chat_send_argv("cheese chat send 'hello world'") == [
        "cheese",
        "chat",
        "send",
        "hello world",
    ]
    assert _local_chat_send_argv("cheese chat send --file ./update.txt")[-1] == (
        "./update.txt"
    )
    assert _local_chat_send_argv("cheese chat send '$(touch escaped)'") == [
        "cheese",
        "chat",
        "send",
        "$(touch escaped)",
    ]
    assert _local_chat_send_argv("cheese chat send hello; touch escaped") is None
    assert _local_chat_send_argv("cheese chat send $(touch escaped)") is None
    assert _local_chat_send_argv("printf x; cheese chat send hello") is None


def test_chat_publication_fast_path_posts_with_session_credentials(monkeypatch, capsys):
    from app.domain.agent.harness.claude_code.remote_execution import client

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(b'{"data":{"id":"published"}}')

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    seen = {}

    def urlopen(request, timeout):
        seen.update(
            {
                "url": request.full_url,
                "timeout": timeout,
                "headers": dict(request.headers),
                "body": json.loads(request.data),
            }
        )
        return Response()

    monkeypatch.setattr(
        client.os,
        "environ",
        {
            "CHEESE_API": "http://cheese.test/api",
            "CHEESE_TOKEN": "scoped-token",
            "CHEESE_TOPIC": "room",
            "CHEESE_TURN": "turn",
        },
    )
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    status = client._publish_chat_locally(
        ["cheese", "chat", "send", "published", "--reply-to", "parent"]
    )
    assert status == 0
    assert seen["url"] == "http://cheese.test/api/topics/room/messages"
    assert seen["headers"]["X-cheese-token"] == "scoped-token"
    assert seen["headers"]["X-cheese-turn"] == "turn"
    assert seen["body"]["content"] == "published"
    assert seen["body"]["reply_to"] == "parent"
    assert json.loads(capsys.readouterr().out) == {"id": "published"}


@pytest.fixture
def executor(tmp_path):
    home = tmp_path / "session home"
    helper = home / ".cheese/remote-execution/runtime.py"
    helper.parent.mkdir(parents=True)
    shutil.copyfile(runtime.__file__, helper)
    shutil.copyfile(cli_worker.__file__, helper.parent / "cli_worker.py")
    shutil.copyfile(
        Path(__file__).resolve().parents[2] / "sandbox/cheese",
        home / ".cheese/cheese",
    )
    state = home / ".cheese/executor"
    work = tmp_path / "project"
    work.mkdir()
    subprocess.run(
        [sys.executable, str(helper), "start", "--state", str(state)],
        input=json.dumps({"workspace": str(work), "env": {}}),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    yield {"device_id": "project-device", "home": str(home)}, work, state
    subprocess.run(
        [sys.executable, str(helper), "stop", "--state", str(state)],
        capture_output=True,
        check=True,
        timeout=15,
    )


class SocketDevice:
    def __init__(self):
        self.sizes = []
        self.devices = []
        self.hub = DeviceHub()

    async def call_executor(self, device_id, state, method, params, *, trace_id=None):
        self.devices.append(device_id)
        await self.hub.attach_device(device_id, self)
        await self.hub.on_device_message(device_id, {"t": "hello", "executor": True})
        return await self.hub.call_executor(device_id, state, method, params)

    async def send_json(self, message):
        if message["t"] == "welcome":
            return
        assert message["t"] == "execution.call"
        reader, writer = await asyncio.open_unix_connection(
            runtime.socket_path(message["path"])
        )
        writer.write(message["stdin"].encode() + b"\n")
        await writer.drain()
        while chunk := await reader.read(64 * 1024):
            self.sizes.append(len(chunk))
            await self.hub.on_device_message(
                self.devices[-1],
                {
                    "t": "execution.data",
                    "id": message["id"],
                    "data": base64.b64encode(chunk).decode(),
                },
            )
        writer.close()
        await writer.wait_closed()
        await self.hub.on_device_message(
            self.devices[-1],
            {
                "t": "execution.result",
                "id": message["id"],
            },
        )


@pytest.mark.anyio
async def test_large_file_control_crosses_connector_without_truncation(executor):
    target, work, state = executor
    content = "中文 text\n" * 200_000
    (work / "large.txt").write_text(content)
    device = SocketDevice()
    result = await execution.call(
        target, "control", {"subtype": "read_file", "path": "large.txt"}, hub=device
    )
    assert result["contents"] == content
    assert max(device.sizes) < 1 << 20
    assert set(device.devices) == {"project-device"}
    assert not (state / "relay").exists()


@pytest.mark.anyio
async def test_repeated_mutation_id_does_not_repeat_shell_write(executor):
    target, work, _ = executor
    device = SocketDevice()
    request = {
        "id": str(uuid.uuid4()),
        "tool": "Bash",
        "args": {"command": "printf once >> output.txt"},
    }
    first = await execution.call(target, "invoke", request, hub=device)
    second = await execution.call(target, "invoke", request, hub=device)
    assert "error" not in first
    assert first == second
    assert (work / "output.txt").read_text() == "once"


@pytest.fixture
def central_transport(executor, tmp_path, request):
    target, work, state = executor
    clients = []
    drop = []
    publications = []
    platform_calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            assert self.headers["X-Cheese-Token"] == "fixture"
            clients.append(self.client_address)
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status = 200
            if self.path == "/platform-denied":
                status = 403
                result = {"error": {"code": "room_scope_denied"}}
            elif self.path == "/platform-fixture":
                assert self.headers["X-Cheese-Turn"] == "fixture-turn"
                platform_calls.append(payload)
                result = {"data": payload}
            elif self.path == "/topics/fixture/decision":
                assert self.headers["X-Cheese-Turn"] == "fixture-turn"
                platform_calls.append(payload)
                result = {"data": payload}
            elif self.path == "/topics/fixture/messages":
                uuid.UUID(payload["request_id"])
                assert self.headers["X-Cheese-Turn"] == "fixture-turn"
                publications.append(payload)
                result = {"data": {"content": payload["content"]}}
            else:
                result = runtime.request(state, payload["method"], payload["params"])
            if drop:
                drop.pop()
                self.close_connection = True
                return
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = tmp_path / "central.json"
    config.write_text(
        json.dumps(
            {
                "kind": "device",
                "url": f"http://127.0.0.1:{server.server_port}/execution",
                "workspace": str(work),
                "central_hooks": getattr(request, "param", {}),
            }
        )
    )
    log = (tmp_path / "central.log").open("w")
    process = runtime.MCPProcess(
        [sys.executable, central.__file__, "transport", str(config)],
        str(tmp_path),
        {
            **os.environ,
            "NO_PROXY": "127.0.0.1",
            "CHEESE_TOKEN": "fixture",
            "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
            "CHEESE_TOPIC": "fixture",
            "CHEESE_TURN": "fixture-turn",
        },
        log,
    )
    process.publications = publications
    process.platform_calls = platform_calls
    process.cli_source = Path(target["home"]) / ".cheese/cheese"
    try:
        yield process, clients, drop, work
    finally:
        process.close()
        server.shutdown()
        server.server_close()
        log.close()


def test_platform_mcp_posts_literal_json_without_executor_invocation(central_transport):
    process, clients, _, work = central_transport
    tool = next(
        t
        for t in process.call("tools/list", {})["tools"]
        if t["name"] == "platform_request"
    )
    discovery_clients = len(clients)
    assert set(tool["inputSchema"]["required"]) == {"method", "path"}
    body = {"title": "中文\n$(touch escaped); `false`", "values": [1, False, None]}
    for index in range(2):
        result = process.call(
            "tools/call",
            {
                "name": "platform_request",
                "arguments": {
                    "id": str(index),
                    "session_id": "fixture",
                    "method": "POST",
                    "path": "/platform-fixture",
                    "body": body,
                },
            },
        )
        outcome = json.loads(result["content"][0]["text"])
        assert json.loads(outcome["result"]["stdout"]) == {"data": body}
    assert process.platform_calls == [body, body]
    assert len(clients) == discovery_clients + 2
    assert clients[-2] == clients[-1]
    assert not (work / "escaped").exists()


def test_decision_uses_shared_plan_without_executor_invocation(central_transport):
    process, _, _, work = central_transport
    tools = process.call("tools/list", {})["tools"]
    assert any(tool["name"] == "cheese_decision" for tool in tools)
    result = process.call(
        "tools/call",
        {
            "name": "cheese_decision",
            "arguments": {
                "id": "direct-decision",
                "session_id": "fixture",
                "text": "literal $(touch escaped)",
            },
        },
    )
    outcome = json.loads(result["content"][0]["text"])
    assert json.loads(outcome["result"]["stdout"])["data"] == {
        "decision": "literal $(touch escaped)"
    }
    assert not (work / "escaped").exists()


@pytest.mark.parametrize(
    "central_transport",
    [
        {
            "PreToolUse": [
                {
                    "matcher": "mcp__native__cheese_echo",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "printf '%s' '{\"hookSpecificOutput\":"
                                '{"updatedInput":{"value":"changed by policy"}}}'
                                "'"
                            ),
                        }
                    ],
                }
            ]
        }
    ],
    indirect=True,
)
def test_structured_cheese_tool_discovers_and_runs_on_executor_with_policy(
    central_transport,
):
    process, _, _, work = central_transport
    process.cli_source.write_text(
        "import argparse, pathlib\n"
        "def build_parser():\n"
        " p=argparse.ArgumentParser(); s=p.add_subparsers(dest='cmd', required=True)\n"
        " q=s.add_parser('echo', description='Read a value on the executor.')\n"
        " q.add_argument('value'); return p\n"
        "if __name__ == '__main__':\n"
        " a=build_parser().parse_args(); p=pathlib.Path('structured-calls')\n"
        " p.write_text((p.read_text() if p.exists() else '') + 'x'); print(a.value)\n"
    )
    tool = next(
        item
        for item in process.call("tools/list", {})["tools"]
        if item["name"] == "cheese_echo"
    )
    assert tool["inputSchema"]["required"] == ["value"]
    results = []
    for _ in range(2):
        results.append(
            process.call(
                "tools/call",
                {
                    "name": "cheese_echo",
                    "arguments": {
                        "id": "structured-cheese",
                        "session_id": "fixture",
                        "value": "$(touch escaped); original",
                    },
                },
            )
        )
    for result in results:
        outcome = json.loads(result["content"][0]["text"])
        assert outcome["result"]["stdout"] == "changed by policy\n"
    assert (work / "structured-calls").read_text() == "x"
    assert not (work / "escaped").exists()


@pytest.mark.parametrize(
    "path", ["https://other.test/x", "//other.test/x", "relative", "/x#fragment"]
)
def test_platform_mcp_cannot_redirect_credentials(central_transport, path):
    process, clients, _, _ = central_transport
    with pytest.raises(RuntimeError, match="relative API path"):
        process.call(
            "tools/call",
            {
                "name": "platform_request",
                "arguments": {
                    "id": "invalid",
                    "session_id": "fixture",
                    "method": "POST",
                    "path": path,
                },
            },
        )
    assert clients == []


def test_platform_mcp_preserves_backend_permission_failure(central_transport):
    process, clients, _, _ = central_transport
    with pytest.raises(RuntimeError, match="Platform HTTP 403.*room_scope_denied"):
        process.call(
            "tools/call",
            {
                "name": "platform_request",
                "arguments": {
                    "id": "denied",
                    "session_id": "fixture",
                    "method": "POST",
                    "path": "/platform-denied",
                    "body": {},
                },
            },
        )
    assert len(clients) == 1


@pytest.mark.parametrize(
    "central_transport",
    [
        {
            "PreToolUse": [
                {
                    "matcher": "mcp__native__platform_request",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "printf '%s' '{\"hookSpecificOutput\":"
                                '{"permissionDecision":"deny",'
                                '"permissionDecisionReason":"custom policy"}}\''
                            ),
                        }
                    ],
                }
            ]
        }
    ],
    indirect=True,
)
def test_platform_mcp_respects_custom_policy_before_http(central_transport):
    process, clients, _, _ = central_transport
    result = process.call(
        "tools/call",
        {
            "name": "platform_request",
            "arguments": {
                "id": "denied",
                "session_id": "fixture",
                "method": "POST",
                "path": "/platform-fixture",
                "body": {},
            },
        },
    )
    assert json.loads(result["content"][0]["text"])["deny"] == "custom policy"
    assert clients == []


def native_call(process, identifier, command):
    result = process.call(
        "tools/call",
        {
            "name": "invoke",
            "arguments": {
                "id": identifier,
                "tool": "Bash",
                "args": {"command": command},
                "session_id": "fixture",
            },
        },
    )
    return json.loads(result["content"][0]["text"])


@pytest.mark.anyio
async def test_codex_tool_retry_uses_the_executor_mutation_receipt(
    central_transport, tmp_path, monkeypatch
):
    _, _, _, workspace = central_transport
    monkeypatch.setenv("CHEESE_TOKEN", "fixture")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    tools = RemoteTools(json.loads((tmp_path / "central.json").read_text()))
    tools.routes = {"Bash": ("native", "Bash")}
    request = {
        "tool": "Bash",
        "callId": "codex-call",
        "arguments": {"command": "printf x >> once.txt; cat once.txt"},
    }
    first = await tools("item/tool/call", request)
    assert first["success"] is True
    assert await tools("item/tool/call", request) == first
    assert (workspace / "once.txt").read_text() == "x"


def test_generated_prefix_preserves_local_hook_and_remote_command_boundary(
    central_transport, tmp_path, monkeypatch
):
    _, _, _, remote_work = central_transport
    monkeypatch.setenv("CHEESE_TOKEN", "fixture")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setattr(central.os.path, "ismount", lambda _path: True)
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    source = Path(central.__file__)
    copied_helper = helpers / source.name
    shutil.copyfile(source, copied_helper)
    shutil.copyfile(source.with_name("proxy.js"), helpers / "proxy.js")
    shutil.copyfile(executor_transport.__file__, helpers / "executor_transport.py")
    monkeypatch.setattr(central, "__file__", str(copied_helper))
    target = json.loads((tmp_path / "central.json").read_text())
    command = "cat > 'hook receipt.txt'; printf '%s' 'quoted * ? [value]'"
    directory = tmp_path / "prepared with spaces"
    version_probe = tmp_path / "claude-version"
    version_probe.write_text("#!/bin/sh\nprintf '2.1.265\\n'\n")
    version_probe.chmod(0o700)
    launch = central.prepare(
        directory,
        target,
        claude=str(version_probe),
        base_settings={
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": command}]}
                ]
            }
        },
    )
    execution = json.loads((directory / "execution.json").read_text())
    token_file = Path(execution["token_file"])
    assert token_file.read_text() == "fixture"
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert "token" not in execution
    env = launch["env"]
    workspace = directory / "forwarded-project"
    prefix = env["CLAUDE_CODE_SHELL_PREFIX"]
    local = subprocess.run(
        [prefix, command],
        input='{"receipt":true}',
        cwd=workspace,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    assert local.stdout == "quoted * ? [value]"
    assert (workspace / "hook receipt.txt").read_text() == '{"receipt":true}'
    assert not (remote_work / "hook receipt.txt").exists()
    # Sharing a prefix with an allowed hook cannot make arbitrary commands local.
    changed = command + "; printf remote > appended.txt"
    subprocess.run(
        [prefix, changed],
        input="remote input",
        cwd=workspace,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    assert not (workspace / "appended.txt").exists()
    assert (remote_work / "appended.txt").read_text() == "remote"
    assert (workspace / "hook receipt.txt").read_text() == '{"receipt":true}'
    # Platform hooks no longer need to launch the Python command dispatcher.
    copied_helper.unlink()
    direct = subprocess.run(
        [prefix, command],
        input="second receipt",
        cwd=workspace,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    assert direct.stdout == "quoted * ? [value]"
    assert (workspace / "hook receipt.txt").read_text() == "second receipt"


@pytest.mark.parametrize(
    "central_transport",
    [
        {
            event: [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "cat >> publication-hooks.jsonl; "
                            "printf '\\n' >> publication-hooks.jsonl",
                        }
                    ],
                }
            ]
            for event in ("PreToolUse", "PostToolUse")
        }
    ],
    indirect=True,
)
def test_chat_publication_uses_resident_connection_and_stable_request_id(
    central_transport,
    tmp_path,
):
    process, clients, _, _ = central_transport
    for _ in range(2):
        result = native_call(process, "publication", "cheese chat send 'hello 世界'")
        assert json.loads(result["result"]["stdout"]) == {"content": "hello 世界"}
    assert len(process.publications) == 2
    assert (
        process.publications[0]["request_id"] == process.publications[1]["request_id"]
    )
    assert clients[0] == clients[1]
    events = [
        json.loads(line)
        for line in (tmp_path / "publication-hooks.jsonl").read_text().splitlines()
    ]
    assert [event["hook_event_name"] for event in events] == [
        "PreToolUse",
        "PostToolUse",
        "PreToolUse",
        "PostToolUse",
    ]
    assert events[1]["tool_response"]["stdout"] == result["result"]["stdout"]


def test_publication_connection_survives_worker_thread_exit(
    central_transport, tmp_path, monkeypatch
):
    _, clients, _, _ = central_transport
    config = json.loads((tmp_path / "central.json").read_text())
    monkeypatch.setenv("CHEESE_API", config["url"].removesuffix("/execution"))
    monkeypatch.setenv("CHEESE_TOKEN", "fixture")
    monkeypatch.setenv("CHEESE_TOPIC", "fixture")
    monkeypatch.setenv("CHEESE_TURN", "fixture-turn")
    publisher = executor_transport.RemoteClient(config)
    try:
        for index in range(2):
            with ThreadPoolExecutor(max_workers=1) as worker:
                result = worker.submit(
                    publisher.publish_message,
                    {"session_id": "fixture", "id": str(index)},
                    {"content": "shared connection"},
                ).result(timeout=10)
                assert (
                    json.loads(result["value"]["stdout"])["content"]
                    == "shared connection"
                )
        assert len(clients) == 2 and clients[0] == clients[1]
    finally:
        if publisher.publication:
            publisher.publication.transport.connection.close()


def test_chat_lost_response_is_not_replayed_or_sent_to_device(central_transport):
    process, _, drop, _ = central_transport
    drop.append(True)
    with pytest.raises(RuntimeError):
        native_call(process, "lost-publication", "cheese chat send 'hello'")
    assert len(process.publications) == 1
    native_call(process, "lost-publication", "cheese chat send 'hello'")
    assert process.publications[0] == process.publications[1]


def test_structured_chat_publishes_literal_content_without_executor(central_transport):
    process, clients, _, work = central_transport
    content = "hello 'world'\n$(touch forbidden); --literal"
    arguments = {"content": content, "id": "typed-publication", "session_id": "fixture"}
    tools = process.call("tools/list", {})["tools"]
    discovery_clients = len(clients)
    assert any(tool["name"] == "chat_send" for tool in tools)
    for _ in range(2):
        result = process.call(
            "tools/call", {"name": "chat_send", "arguments": arguments}
        )
        value = json.loads(result["content"][0]["text"])["result"]
        assert json.loads(value["stdout"])["content"] == content
    assert process.publications[0] == process.publications[1]
    assert len(clients) == discovery_clients + 2
    assert clients[-2] == clients[-1]
    assert not (work / "forbidden").exists()


def test_central_tools_reuse_process_and_http_connection(central_transport):
    process, clients, _, work = central_transport
    pid = process.process.pid
    for index in range(40):
        assert "result" in native_call(process, str(index), "printf x >> count")
    assert (work / "count").read_text() == "x" * 40
    # Each worker owns a connection; sequential replies may use different workers.
    assert len(set(clients)) < len(clients)
    assert process.process.pid == pid and process.process.poll() is None


def test_structured_chat_retry_retains_request_id(central_transport):
    process, _, drop, _ = central_transport
    args = {"content": "retry", "id": "first", "session_id": "fixture"}
    drop.append(True)
    with pytest.raises(RuntimeError, match="request_id="):
        process.call("tools/call", {"name": "chat_send", "arguments": args})
    args["id"] = "second"
    args["request_id"] = process.publications[0]["request_id"]
    process.call("tools/call", {"name": "chat_send", "arguments": args})
    assert process.publications[0] == process.publications[1]
    args["request_id"] = "invalid"
    with pytest.raises(RuntimeError):
        process.call("tools/call", {"name": "chat_send", "arguments": args})
    assert len(process.publications) == 2


def test_lost_http_response_is_not_replayed_and_original_id_recovers(central_transport):
    process, clients, drop, work = central_transport
    drop.append(True)
    with pytest.raises(RuntimeError):
        native_call(process, "same", "printf once >> count")
    assert (work / "count").read_text() == "once"
    assert len(clients) == 1
    assert "result" in native_call(process, "same", "printf once >> count")
    assert (work / "count").read_text() == "once"
    assert len(clients) == 2 and clients[0] != clients[1]


@pytest.mark.parametrize(
    "central_transport",
    [
        {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "printf '%s' '{\"hookSpecificOutput\":{"
                                '"permissionDecision":"deny",'
                                '"permissionDecisionReason":"blocked by policy"}}\''
                            ),
                        }
                    ],
                }
            ],
        }
    ],
    indirect=True,
)
def test_resident_transport_keeps_policy_denials(central_transport):
    process, clients, _, work = central_transport
    assert native_call(process, "denied", "touch forbidden") == {
        "deny": "blocked by policy"
    }
    assert not clients and not (work / "forbidden").exists()
    assert native_call(process, "denied-chat", "cheese chat send 'forbidden'") == {
        "deny": "blocked by policy"
    }
    assert not process.publications and not clients


@pytest.mark.parametrize(
    "central_transport",
    [
        {
            "PreToolUse": [
                {
                    "matcher": "mcp__native__chat_send",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "printf '%s' '{\"hookSpecificOutput\":"
                                '{"permissionDecision":"deny",'
                                '"permissionDecisionReason":"publication denied"}}\''
                            ),
                        }
                    ],
                }
            ]
        }
    ],
    indirect=True,
)
def test_structured_chat_preserves_policy_denial(central_transport):
    process, clients, _, _ = central_transport
    result = process.call(
        "tools/call",
        {
            "name": "chat_send",
            "arguments": {
                "content": "not published",
                "id": "denied",
                "session_id": "fixture",
            },
        },
    )
    assert json.loads(result["content"][0]["text"]) == {"deny": "publication denied"}
    assert not process.publications and not clients


def test_resident_transport_cancels_a_running_shell(central_transport):
    process, _, _, work = central_transport
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(
            native_call, process, "cancelled", "touch started; sleep 30; touch finished"
        )
        deadline = time.monotonic() + 5
        while not (work / "started").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        process.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": process.sequence},
            }
        )
        outcome = pending.result(timeout=5)
    assert outcome["result"]["interrupted"]
    assert not (work / "finished").exists()


def test_a_tool_call_waits_out_a_platform_that_is_being_redeployed(monkeypatch):
    """An app deploy recreates the endpoint the central session dials, so every
    tool call in every live room is refused for as long as it takes. A refused
    connect sent nothing, so waiting for it cannot run the call twice."""
    client = executor_transport.RemoteClient(
        {"kind": "device", "url": "http://executor.test"}
    )
    monkeypatch.setenv("CHEESE_TOKEN", "t")
    slept: list[float] = []
    monkeypatch.setattr(executor_transport.time, "sleep", slept.append)
    attempts = []

    class Response:
        status = 200

        @staticmethod
        def read():
            return b'{"ok": true}'

    class Connection:
        sock = None

        def request(self, method, path, *, body, headers):
            attempts.append(path)
            if len(attempts) < 4:
                raise ConnectionRefusedError(111, "Connection refused")

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            pass

    monkeypatch.setattr(client, "connection", lambda: (Connection(), "/execution"))
    client.transport.headers = {}
    assert client.call("invoke") == {"ok": True}
    assert len(attempts) == 4
    assert slept and all(
        delay <= executor_transport.CONNECT_RETRY_MAX_DELAY_S for delay in slept
    )


def test_a_refusal_that_outlasts_the_window_is_still_reported(monkeypatch):
    client = executor_transport.RemoteClient(
        {"kind": "device", "url": "http://executor.test"}
    )
    monkeypatch.setenv("CHEESE_TOKEN", "t")
    monkeypatch.setattr(executor_transport, "CONNECT_RETRY_WINDOW_S", 0)

    class Connection:
        sock = None

        def request(self, method, path, *, body, headers):
            raise ConnectionRefusedError(111, "Connection refused")

        @staticmethod
        def close():
            pass

    monkeypatch.setattr(client, "connection", lambda: (Connection(), "/execution"))
    client.transport.headers = {}
    with pytest.raises(ConnectionRefusedError):
        client.call("invoke")


def test_a_lost_response_is_never_replayed(monkeypatch):
    """The other failures can follow a mutation the executor already ran, so
    they travel straight up exactly as before."""
    client = executor_transport.RemoteClient(
        {"kind": "device", "url": "http://executor.test"}
    )
    monkeypatch.setenv("CHEESE_TOKEN", "t")
    attempts = []

    class Connection:
        sock = None

        def request(self, method, path, *, body, headers):
            attempts.append(path)

        @staticmethod
        def getresponse():
            raise ConnectionResetError("peer went away mid-answer")

        @staticmethod
        def close():
            pass

    monkeypatch.setattr(client, "connection", lambda: (Connection(), "/execution"))
    client.transport.headers = {}
    with pytest.raises(ConnectionResetError):
        client.call("invoke")
    assert len(attempts) == 1


def test_every_tool_listing_says_how_many_platform_tools_it_found(monkeypatch, capsys):
    """Three turns have been lost to a list that silently arrived without the
    `cheese_*` family, and each investigation ended at the same wall: nothing
    recorded what had been listed."""

    class Ready:
        @staticmethod
        def call(method, params=None):
            if method == "ping":
                return {"capabilities": ["prepare", "cli_worker"]}
            return {"tools": [{"name": "cheese_status", "inputSchema": {}}]}

    assert central._cli_tools(Ready()) == [{"name": "cheese_status", "inputSchema": {}}]
    assert "1 platform tools" in capsys.readouterr().err

    class NoWorker:
        @staticmethod
        def call(method, params=None):
            return {"capabilities": ["prepare"]}

    assert central._cli_tools(NoWorker()) == []
    assert "no CLI worker" in capsys.readouterr().err

    class Unreachable:
        @staticmethod
        def call(method, params=None):
            raise ConnectionRefusedError(111, "Connection refused")

    # An executor that cannot be reached still gets a listing: the file and
    # shell tools are the transport's own, and a first tool call must not race
    # a probe of something else.
    assert central._cli_tools(Unreachable()) == []
    assert "ConnectionRefusedError" in capsys.readouterr().err
