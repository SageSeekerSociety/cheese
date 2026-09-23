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

from app.domain.agent import cli_worker, execution, executor_transport
from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.harness.claude_code.remote_execution import client as central
from app.domain.agent.harness.claude_code.remote_execution import runtime
from app.domain.agent.harness.claude_code.remote_execution.client import (
    _local_chat_send_argv,
)
from app.domain.agent.harness.codex.tools import RemoteTools
from tests.pinned_claude import claude_binary
from tests.support import wire


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
def executor(tmp_path, request):
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
    project_servers = {}
    if getattr(request, "param", None) == "project-mcp":
        script = work / "repo_mcp.py"
        script.write_text("""import json, sys
from pathlib import Path
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request['method']
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'repo', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'publish', 'inputSchema': {'type': 'object'}}]}
    elif method == 'tools/call':
        Path('published.txt').write_text(request['params']['arguments']['value'])
        result = {'content': [{'type': 'text', 'text': str(Path.cwd())}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}),
          flush=True)
""")
        project_servers = {"repo": {"command": sys.executable, "args": [str(script)]}}
    subprocess.run(
        [sys.executable, str(helper), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(work),
                "claude": claude_binary(),
                "env": {},
                "mcp_servers": project_servers,
            }
        ),
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
    """The connector's half of an executor call, in Python and over the real
    socket: it does what ``cli/internal/host/executor.go`` does, so the frames
    it speaks come from ``tests.support.wire`` rather than being typed here."""

    def __init__(self):
        self.sizes = []
        self.devices = []
        self.timeouts = []
        self.hub = DeviceHub()

    async def call_executor(
        self, device_id, state, method, params, *, trace_id=None, timeout=660
    ):
        self.devices.append(device_id)
        self.timeouts.append(timeout)
        await self.hub.attach_device(device_id, self)
        await self.hub.on_device_message(device_id, {"t": "hello", "executor": True})
        return await self.hub.call_executor(
            device_id, state, method, params, timeout=timeout, trace_id=trace_id
        )

    async def send_json(self, message):
        if message["t"] == "welcome":
            return
        call = wire.ExecutionCall.parse(message)
        reader, writer = await asyncio.open_unix_connection(
            runtime.socket_path(call.path)
        )
        writer.write(call.stdin.encode() + b"\n")
        await writer.drain()
        while chunk := await reader.read(64 * 1024):
            self.sizes.append(len(chunk))
            await self.hub.on_device_message(
                self.devices[-1], wire.execution_data(call.id, chunk)
            )
        writer.close()
        await writer.wait_closed()
        await self.hub.on_device_message(
            self.devices[-1], wire.execution_result(call.id)
        )


@pytest.mark.anyio
async def test_large_file_control_crosses_connector_without_truncation(executor):
    target, work, state = executor
    content = "中文 text\n" * 200_000
    (work / "large.txt").write_text(content)
    device = SocketDevice()
    result = await execution.call(
        target,
        "control",
        {"subtype": "read_file", "path": "large.txt"},
        hub=device,
        timeout=30,
    )
    assert result["contents"] == content
    assert device.timeouts == [30]
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
            elif self.path == "/topics/fixture/shown":
                assert self.headers["X-Cheese-Turn"] == "fixture-turn"
                platform_calls.append(payload)
                result = {"data": {"content": payload.get("path"), "ok": True}}
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


def test_send_user_file_publishes_the_bytes_on_the_rooms_own_shown_route(
    central_transport,
):
    """A `SendUserFile` the plugin already read must land where this room shows
    what 芝士 points at — `POST /topics/{id}/shown`, the route `cheese show`
    publishes through — and answer the caller with the attachments it promised.
    """
    process, _, _, work = central_transport
    raw = b"%PDF-1.4 report"
    result = process.call(
        "tools/call",
        {
            "name": "send_user_file",
            "arguments": {
                "id": "delivered",
                "session_id": "fixture",
                "files": [
                    {
                        "path": "docs/report.pdf",
                        "name": "report.pdf",
                        "data_b64": base64.b64encode(raw).decode("ascii"),
                    }
                ],
                "caption": "the failing case is row 42",
                "status": "normal",
                "display": "render",
            },
        },
    )
    value = json.loads(result["content"][0]["text"])["result"]
    assert value["attachments"] == [
        {
            "path": str(work / "docs/report.pdf"),
            "size": len(raw),
            "isImage": False,
            "media_type": "application/pdf",
            "pathValidated": True,
        }
    ]
    assert value["caption"] == "the failing case is row 42"
    assert value["display"] == "render"
    shown = [call for call in process.platform_calls if "content_b64" in call]
    assert shown == [
        {
            "path": "docs/report.pdf",
            "content_b64": base64.b64encode(raw).decode("ascii"),
        }
    ]
    assert [note["content"] for note in process.publications] == [
        "the failing case is row 42"
    ]


def test_send_user_file_reads_a_file_the_plugin_host_never_saw(central_transport):
    """The plugin reads through `$.fs`, which is capped and is not where a
    private container keeps its files. A path that arrives without bytes is read
    off the executor instead — the same machine every other project file call
    goes to.
    """
    process, _, _, work = central_transport
    (work / "shot.png").write_bytes(b"\x89PNG-shot")
    result = process.call(
        "tools/call",
        {
            "name": "send_user_file",
            "arguments": {
                "id": "machine-read",
                "session_id": "fixture",
                "files": [{"path": str(work / "shot.png"), "name": "shot.png"}],
                "status": "proactive",
            },
        },
    )
    value = json.loads(result["content"][0]["text"])["result"]
    # `path` is the resolved filesystem path `SendUserFile` promises the caller —
    # not the room-relative pointer, which lives on the POST body's own `path`.
    assert value["attachments"][0]["path"] == str(work / "shot.png")
    assert value["attachments"][0]["isImage"] is True
    assert "upload_error" not in value["attachments"][0]
    shown = process.platform_calls[-1]
    assert shown["path"] == "shot.png"
    assert "as" not in shown
    assert base64.b64decode(shown["content_b64"]) == b"\x89PNG-shot"


def test_send_user_file_reports_what_it_could_not_deliver_without_lying(
    central_transport,
):
    """One bad file must not take the good ones down with it, and a failure is
    reported in the entry it belongs to — never a success the room never sees.
    """
    process, _, _, work = central_transport
    (work / "good.md").write_text("# ok\n")
    result = process.call(
        "tools/call",
        {
            "name": "send_user_file",
            "arguments": {
                "id": "partial",
                "session_id": "fixture",
                "files": [
                    {"path": "missing.bin", "name": "missing.bin"},
                    {"path": "good.md", "name": "good.md"},
                    {
                        "path": "huge.bin",
                        "name": "huge.bin",
                        "upload_error": "file is over the 10MB limit",
                    },
                ],
                "status": "normal",
            },
        },
    )
    value = json.loads(result["content"][0]["text"])["result"]
    missing, good, huge = value["attachments"]
    assert "upload_error" in missing
    assert "upload_error" not in good
    assert good["path"] == str(work / "good.md")
    assert huge["upload_error"] == "file is over the 10MB limit"
    shown_paths = [
        call["path"] for call in process.platform_calls if "content_b64" in call
    ]
    assert shown_paths == ["good.md"]
    assert process.publications == []


def test_send_user_file_paths_keep_the_workspaces_address_and_invent_one_outside_it():
    """`POST /topics/{id}/shown` takes a workspace-relative pointer and refuses
    an absolute one; a file the agent left in `/tmp` still has to arrive, under
    the name this room already gives a paste with no name of its own.
    """
    config = {"workspace": "/work", "central_workspace": "/center"}
    assert central.send_user_file_paths("docs/report.pdf", config) == (
        "/work/docs/report.pdf",
        "docs/report.pdf",
    )
    assert central.send_user_file_paths("/work/shot.png", config) == (
        "/work/shot.png",
        "shot.png",
    )
    # The model is told the executor's paths; the plugin host sees the mount.
    assert central.send_user_file_paths("/center/shot.png", config) == (
        "/work/shot.png",
        "shot.png",
    )
    machine, rel = central.send_user_file_paths("/tmp/scratch.bin", config)
    assert machine == "/tmp/scratch.bin"
    assert rel.startswith("uploads/") and rel.endswith("/scratch.bin")
    _, escaped = central.send_user_file_paths("/work/../etc/passwd", config)
    assert escaped.startswith("uploads/")
    assert central.send_user_file_body(b"%PDF") == {
        "content_b64": base64.b64encode(b"%PDF").decode("ascii"),
    }
    # No `as` is declared at all: `POST /topics/{id}/shown` reads the kind off
    # the path, so this and the room's table cannot drift — and a .gif or a
    # .webp is not silently filed as text/html the way a client-side fallback
    # used to file it.
    assert "as" not in central.send_user_file_body(b"GIF89a")
    for name, mime in (("shot.gif", "image/gif"), ("shot.webp", "image/webp")):
        entry = central.send_user_file_entry(name, name, 4)
        assert entry["isImage"] is True
        assert entry["media_type"] == mime


def test_send_user_file_machine_reads_go_out_through_the_unreachable_breaker():
    """A file the plugin host never saw is read by a command on the executor,
    and that command has to take the same exit as every other project-tool call
    — `invoke_on_the_machine`, the one that trips `unreachable_since`. Calling
    the connection directly reintroduces the timeout storm the breaker exists
    to stop: every SendUserFile on a sandbox session takes this path.
    """
    commands = []

    def invoke(payload, args):
        commands.append((payload["id"], payload["tool"], args["command"]))
        if args["command"].startswith("wc"):
            return {"value": {"stdout": "2\n"}}
        return {"value": {"stdout": base64.b64encode(b"hi").decode("ascii")}}

    assert central.stat_file_on_the_machine(invoke, "/work/a", "id-stat") == 2
    assert central.read_file_on_the_machine(invoke, "/work/a", "id-read") == b"hi"
    assert [(tool, command.split()[0]) for _, tool, command in commands] == [
        ("Bash", "wc"),
        ("Bash", "base64"),
    ]

    def refuse_invoke(payload, args):
        return {"error": "machine is out of reach"}

    with pytest.raises(RuntimeError, match="machine is out of reach"):
        central.read_file_on_the_machine(refuse_invoke, "/work/a", "id-down")


def test_send_user_file_refuses_an_oversize_machine_file_before_reading_it(
    monkeypatch,
):
    """The proxy's `$.fs.stat` is what stops an oversize file before it is read,
    but a private container's file never reaches `$.fs`. The fallback has to
    ask the machine how big the file is and stop there — walking it with
    `base64` first is the exact transfer the ceiling is meant to prevent.
    """
    monkeypatch.setenv("CHEESE_TOPIC", "fixture")
    commands = []

    def invoke(payload, args):
        commands.append(args["command"])
        if args["command"].startswith("wc"):
            return {"value": {"stdout": str(11 * 1024 * 1024)}}
        raise AssertionError("an oversize file must not be read")

    class Client:
        def platform_request(self, args):
            raise AssertionError("an oversize file must not be published")

        def publish_message(self, payload, args):
            raise AssertionError("nothing to caption")

    result = central.deliver_send_user_file(
        Client(),
        {"workspace": "/work", "central_workspace": "/center"},
        {"id": "over"},
        {"files": [{"path": "/work/huge.bin", "name": "huge.bin"}]},
        invoke,
    )
    entry = result["value"]["attachments"][0]
    assert "文件太大" in entry["upload_error"]
    assert entry["path"] == "/work/huge.bin"
    assert len(commands) == 1 and commands[0].startswith("wc ")


def test_send_user_file_names_the_object_form_it_cannot_take(central_transport):
    """The tool text also allows a pre-resolved {file_uuid, file_name, size,
    is_image} entry — a file already in Anthropic's filestore, which this room
    has no way to pull. Reading one as a path yields a file called `file` far
    downstream; refuse the form by name instead.
    """
    process, _, _, _ = central_transport
    with pytest.raises(RuntimeError, match="file_uuid"):
        process.call(
            "tools/call",
            {
                "name": "send_user_file",
                "arguments": {
                    "id": "object-form",
                    "session_id": "fixture",
                    "files": [
                        {
                            "file_uuid": "f-1",
                            "file_name": "shot.png",
                            "size": 3,
                            "is_image": True,
                        }
                    ],
                    "status": "normal",
                },
            },
        )


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


def test_large_edit_receipt_preserves_the_successful_mutation(central_transport):
    process, _, _, work = central_transport
    path = work / "large.txt"
    original = "some text on a line\n" * 12_000 + "before\n"
    path.write_text(original)
    response = process.call(
        "tools/call",
        {
            "name": "invoke",
            "arguments": {
                "id": "large-edit",
                "tool": "Edit",
                "args": {
                    "file_path": str(path),
                    "old_string": "before",
                    "new_string": "after",
                },
                "session_id": "fixture",
            },
        },
    )
    encoded = response["content"][0]["text"]
    assert len(encoded) < 32_000
    envelope = json.loads(encoded)
    result = json.loads(Path(envelope["receipt_path"]).read_text())
    assert "deny" not in result
    assert result["result"]["originalFile"] == original
    assert path.read_text() == original.replace("before", "after")


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
    version_probe.write_text("#!/bin/sh\nprintf '2.1.277\\n'\n")
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
    """窗口走完还是没人接，报的就是「这台机器够不着」。

    一个字也没发出去，所以这条路上没有任何改动可能已经落地 —— 说它够不着是安全
    的，而说出来才使这一轮余下的文件与命令调用不必各自再排一次同样的队。
    """
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
    with pytest.raises(executor_transport.MachineOutOfReach) as raised:
        client.call("invoke")
    assert isinstance(raised.value.__cause__, ConnectionRefusedError)


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


def test_deferred_tools_acquire_once_and_read_project_rules_before_writing(
    executor, tmp_path, monkeypatch
):
    _, work, state = executor
    (work / "CLAUDE.md").write_text("Always preserve the protected file.")
    local = tmp_path / "session-machine"
    local.mkdir()
    (local / "protected").write_text("session only")
    seen = []
    generation = str(uuid.uuid4())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/lease":
                seen.append("lease")
                result = {
                    "data": {
                        "target": {
                            "kind": "device",
                            "workspace": str(work),
                            "url": base + "/execute",
                            "generation": generation,
                        },
                        "token": "execution-only",
                    }
                }
            else:
                seen.append(body["method"])
                assert 0 < body["timeout"] <= 660
                assert self.headers["X-Cheese-Token"] == "execution-only"
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
    target_file = local / "execution.json"
    config = {
        "kind": "deferred",
        "workspace": "/unavailable-project",
        "lease_path": "/lease",
        "target_file": str(target_file),
    }
    target_file.write_text(json.dumps(config))
    client = executor_transport.RemoteClient(config)
    request = {
        "id": "first-write",
        "tool": "Bash",
        "args": {"command": "printf remote > /unavailable-project/output.txt"},
    }
    try:
        assert client.call("context_fs", {"operation": "tree"})["entries"] == {}
        assert seen == [], "Context discovery must not acquire a machine"
        with pytest.raises(RuntimeError, match="Always preserve the protected file"):
            client.call("invoke", request)
        assert "invoke" not in seen
        assert not (work / "output.txt").exists()
        assert (
            "CLAUDE.md"
            in json.loads((local / "context-tree.json").read_text())["entries"]
        )
        result = client.call("invoke", request)
        assert "error" not in result, result
        assert (work / "output.txt").read_text() == "remote"
        assert (local / "protected").read_text() == "session only"
        assert not (local / "output.txt").exists()
        assert seen.count("context") == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("executor", ["project-mcp"], indirect=True)
def test_project_mcp_calls_remain_on_work_machine_and_have_dispatch_identity(
    tmp_path, executor
):
    _, work, state = executor
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            assert payload["method"] == "mcp"
            assert payload["params"]["server"] == "repo"
            reply = runtime.request(state, payload["method"], payload["params"])
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "project-tools.json"
    config.write_text(
        json.dumps(
            {
                "kind": "device",
                "url": f"http://127.0.0.1:{server.server_port}/execution",
                "workspace": "/remote-project",
                "mcp_servers": ["repo"],
            }
        )
    )
    with (tmp_path / "transport.log").open("w") as log:
        process = runtime.MCPProcess(
            [sys.executable, central.__file__, "transport", str(config)],
            str(tmp_path),
            {
                **os.environ,
                "CHEESE_TOKEN": "session-credential",
                "NO_PROXY": "127.0.0.1",
            },
            log,
        )
        try:
            assert "project_tools" in {
                tool["name"] for tool in process.call("tools/list")["tools"]
            }

            def invoke(**args):
                response = process.call(
                    "tools/call",
                    {
                        "name": "project_tools",
                        "arguments": {
                            "id": "mutation-id",
                            "session_id": "native-session",
                            **args,
                        },
                    },
                )
                return json.loads(response["content"][0]["text"])

            assert invoke()["result"]["servers"] == ["repo"]
            assert calls == []
            assert invoke(server="repo")["result"]["tools"][0]["name"] == "publish"
            assert "id" not in calls[-1]["params"]
            assert invoke(
                server="repo", name="publish", arguments={"value": "literal"}
            )["result"]["content"][0]["text"] == str(work)
            assert (work / "published.txt").read_text() == "literal"
            assert not (tmp_path / "published.txt").exists()
            assert calls[-1]["params"]["id"] == "mutation-id"
            assert calls[-1]["params"]["tool"] == "mcp__repo__publish"
        finally:
            process.close()
            server.shutdown()
            server.server_close()
            thread.join()
