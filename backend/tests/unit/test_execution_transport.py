"""Exercise connector output limits and executor mutation receipts together."""

import asyncio
import base64
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

import pytest

from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.harness.claude_code.remote_execution import client as central
from app.domain.agent.harness.claude_code.remote_execution import runtime


@pytest.fixture
def executor(tmp_path):
    home = tmp_path / "session home"
    helper = home / ".claude/remote-execution/runtime.py"
    helper.parent.mkdir(parents=True)
    shutil.copyfile(runtime.__file__, helper)
    state = home / ".claude/executor"
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
    _, work, state = executor
    clients = []
    drop = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):
            assert self.headers["X-Cheese-Token"] == "fixture"
            clients.append(self.client_address)
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            result = runtime.request(state, payload["method"], payload["params"])
            if drop:
                drop.pop()
                self.close_connection = True
                return
            data = json.dumps(result).encode()
            self.send_response(200)
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
        {**os.environ, "NO_PROXY": "127.0.0.1", "CHEESE_TOKEN": "fixture"},
        log,
    )
    try:
        yield process, clients, drop, work
    finally:
        process.close()
        server.shutdown()
        server.server_close()
        log.close()


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


def test_central_tools_reuse_process_and_http_connection(central_transport):
    process, clients, _, work = central_transport
    pid = process.process.pid
    for index in range(3):
        assert "result" in native_call(process, str(index), "printf x >> count")
    assert (work / "count").read_text() == "xxx"
    assert len(set(clients)) == 1
    assert process.process.pid == pid and process.process.poll() is None


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
