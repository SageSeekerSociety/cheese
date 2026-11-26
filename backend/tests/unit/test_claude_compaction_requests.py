"""What the pinned Claude Code sends when it compacts a conversation, and what
the metering proxy forwards of it, on either pool.

A request runs on the model admission bound and on no other (结论 46). The
proxy writes the binding into `model`, but a body can name more models than
that one: Claude Code 2.1.316 puts Anthropic's server-side fallback
(`"fallbacks":[{"model":"claude-opus-5"}]`) on its compaction requests, and
every compaction of a long conversation on the API-key pool failed with "Model
is blocked" because LiteLLM checked that fallback against the gateway's
blocked claude-* rows. Recorded fixtures could not have caught it: the member
appeared with a CLI bump, so these tests drive the pinned binary itself.

The CLI is launched the way a device launches it: its no-login placeholder as
the OAuth token, `HTTPS_PROXY` on a TLS-terminating stand-in for the meter, no
base URL. That launch shape matters: with `ANTHROPIC_BASE_URL` set the CLI
leaves the fallback off, and a test launched that way passes against the bug.
The stand-in answers the boot calls the way the meter does, refuses one turn
with "prompt is too long" so the CLI compacts on its own, then runs `/compact`.
Every model request it records then goes through the real addon
(deploy/metering-proxy/billing_addon.py, behind the same minimal mitmproxy stub
its unit tests use) with admission placing it on each pool.
"""

import asyncio
import datetime
import importlib.util
import json
import os
import ssl
import subprocess
import sys
import threading
import types
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.agent.harness.claude_code.device_launch import NO_LOGIN_PLACEHOLDER
from tests.pinned_claude import claude_binary
from tests.tls_gateway import write_pki

REPO_ROOT = Path(__file__).resolve().parents[3]
PROXY = REPO_ROOT / "deploy" / "metering-proxy"
ANTHROPIC = "api.anthropic.com"
PROJECT = "11111111-1111-1111-1111-111111111111"
TOPIC = "22222222-2222-2222-2222-222222222222"
# The model a device session is launched with and the gateway binds; the
# subscription pool binds a Claude model instead. Same CLI requests for both:
# the CLI does not know which pool it is on.
LAUNCH_MODEL = "deepseek-flash"
BOUND = {"gateway": LAUNCH_MODEL, "subscription": "claude-sonnet-5"}

# Every top-level member the pinned CLI has been seen to send on a model
# request, each reviewed for whether it names a model to run on: only `model`
# and `fallbacks` do, and the proxy drops `fallbacks` (`FallbackModels`).
REVIEWED_MEMBERS = frozenset(
    {
        "context_management",
        "diagnostics",
        "fallbacks",
        "max_tokens",
        "messages",
        "metadata",
        "model",
        "output_config",
        "stream",
        "system",
        "thinking",
        "tools",
    }
)
# What the conversation itself carries — text a person or a tool wrote, where
# a `model` key is content (an Agent tool call's input), not a routing choice.
CONVERSATION = frozenset({"messages", "system", "tools"})

_core_spec = importlib.util.spec_from_file_location(
    "cheese_billing_core", PROXY / "cheese_billing_core.py"
)
assert _core_spec and _core_spec.loader
core = importlib.util.module_from_spec(_core_spec)
_core_spec.loader.exec_module(core)


# —— the stand-in meter + Anthropic the real CLI talks to ——————————————————


def _sse(model: str, text: str) -> bytes:
    message = {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
    events = [
        {"type": "message_start", "message": message},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    ]
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
    ).encode()


class _Recorder:
    """What the CLI sent, and whether the next ordinary turn is refused as
    too long so that the CLI compacts on its own."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.arm_too_long = False
        self.lock = threading.Lock()


def _anthropic_handler(recorder: _Recorder):
    class Anthropic(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _reply(self, status: int, data: bytes, ctype="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            path = self.path.split("?", 1)[0]
            answer = core.control_answer(ANTHROPIC, path, PROJECT, TOPIC)
            if answer is not None:
                return self._reply(answer.status, answer.body)
            local = core.no_login_answer(ANTHROPIC, path)
            if local is not None:
                return self._reply(200, local)
            if not path.startswith("/v1/messages"):
                return self._reply(404, b'{"type":"error"}')
            if path.endswith("/count_tokens"):
                return self._reply(200, b'{"input_tokens":10}')
            headers = {k.lower(): v for k, v in self.headers.items()}
            body = json.loads(raw)
            with recorder.lock:
                recorder.requests.append(
                    {"path": self.path, "headers": headers, "body": raw.decode()}
                )
                too_long = (
                    recorder.arm_too_long
                    and headers.get("x-claude-code-request-class") != "compaction"
                    and bool(body.get("tools"))
                )
                if too_long:
                    recorder.arm_too_long = False
            if too_long:
                error = {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "prompt is too long: 250000 tokens > 200000 maximum",
                    },
                }
                return self._reply(400, json.dumps(error).encode())
            compaction = headers.get("x-claude-code-request-class") == "compaction"
            text = "<summary>fixture summary</summary>" if compaction else "ok"
            if body.get("stream"):
                return self._reply(
                    200, _sse(body.get("model", ""), text), "text/event-stream"
                )
            message = {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": body.get("model", ""),
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            }
            return self._reply(200, json.dumps(message).encode())

        do_GET = do_POST = do_PUT = do_DELETE = _handle

    return Anthropic


def _connect_handler(context: ssl.SSLContext, anthropic):
    class Connect(BaseHTTPRequestHandler):
        """The meter's CONNECT listener: terminates TLS for Anthropic's name
        and refuses every other host, so nothing leaves the test."""

        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            self.close_connection = True
            if self.path.split(":", 1)[0] != ANTHROPIC:
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            try:
                tls = context.wrap_socket(self.connection, server_side=True)
                anthropic(tls, self.client_address, self.server)
            except (OSError, ssl.SSLError):
                pass

        def finish(self):
            try:
                super().finish()
            except OSError:
                pass

    return Connect


def _drive_the_cli(tmp_path: Path) -> list[dict]:
    """One conversation: a turn, a turn refused as too long (the CLI compacts
    on its own and retries), then `/compact`. Returns every model request."""
    ca, cert, key = write_pki(
        tmp_path, expires_in=datetime.timedelta(days=1), name=ANTHROPIC
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    recorder = _Recorder()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), _connect_handler(context, _anthropic_handler(recorder))
    )
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "HTTPS_PROXY": f"http://127.0.0.1:{server.server_port}",
        "NODE_EXTRA_CA_CERTS": ca,
        "CLAUDE_CODE_OAUTH_TOKEN": NO_LOGIN_PLACEHOLDER,
        "ANTHROPIC_MODEL": LAUNCH_MODEL,
        "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
        "DISABLE_AUTOUPDATER": "1",
    }

    def run(prompt: str, *, resume: bool) -> dict:
        process = subprocess.run(
            [
                claude_binary(),
                "--print",
                "--output-format",
                "json",
                "--strict-mcp-config",
                "--setting-sources",
                "user",
                "--mcp-config",
                '{"mcpServers":{}}',
                *(["--continue"] if resume else []),
                "--",
                prompt,
            ],
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert process.returncode == 0, process.stdout[-2000:] + process.stderr
        return json.loads(process.stdout)

    try:
        run("first turn", resume=False)
        recorder.arm_too_long = True
        after_compaction = run("second turn", resume=True)
        assert after_compaction.get("is_error") is False, after_compaction
        run("/compact", resume=True)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        (tmp_path / "provider-requests.json").write_text(
            json.dumps(recorder.requests, indent=2)
        )
    return recorder.requests


@pytest.fixture(scope="module")
def cli_requests(tmp_path_factory) -> list[dict]:
    requests = _drive_the_cli(tmp_path_factory.mktemp("compaction"))
    compactions = {
        r["headers"].get("x-claude-code-compaction")
        for r in requests
        if r["headers"].get("x-claude-code-request-class") == "compaction"
    }
    # Without both, the assertions below would pass on a run that never
    # compacted, which is the request that broke.
    assert {"reactive", "manual"} <= compactions, compactions
    return requests


# —— the real addon, behind the mitmproxy stub ————————————————————————————


def _install_mitmproxy_stub(monkeypatch) -> None:
    class Response:
        def __init__(self, status_code=200, content=b"", headers=None):
            self.status_code = status_code
            self.content = content
            self.headers = headers or {}

        @classmethod
        def make(cls, status_code=200, content=b"", headers=None):
            return cls(status_code, content, headers or {})

    http_mod = types.ModuleType("mitmproxy.http")
    http_mod.Response = Response
    http_mod.HTTPFlow = type("HTTPFlow", (), {})
    tls_mod = types.ModuleType("mitmproxy.tls")
    tls_mod.ClientHelloData = type("ClientHelloData", (), {})
    root = types.ModuleType("mitmproxy")
    root.http = http_mod
    root.tls = tls_mod
    monkeypatch.setitem(sys.modules, "mitmproxy", root)
    monkeypatch.setitem(sys.modules, "mitmproxy.http", http_mod)
    monkeypatch.setitem(sys.modules, "mitmproxy.tls", tls_mod)


def _load_addon(monkeypatch, tmp_path: Path):
    """A fresh billing_addon whose platform holds a Claude login, attributing
    turns from `x-cheese-attr` (the scoped-token road is not what is tested)."""
    _install_mitmproxy_stub(monkeypatch)
    credential = tmp_path / "claude-credential" / "credential"
    credential.parent.mkdir(parents=True, exist_ok=True)
    credential.write_text("sk-ant-oat01-PLATFORM-FIXTURE")
    for name, value in {
        "CHEESE_USAGE_LOG": str(tmp_path / "usage.jsonl"),
        "CHEESE_SCOPED_SECRET": "",
        "CHEESE_ALLOW_HEADER_ATTR": "1",
        "CHEESE_ADMISSION_URL": "",
        "CHEESE_GATEWAY_BASE": "",
        "CHEESE_TOKEN_CAP": "0",
        "CHEESE_CLAUDE_CREDENTIAL": str(credential),
        "CHEESE_CHATGPT_CREDENTIALS": str(tmp_path / "chatgpt"),
        "CHEESE_CHATGPT_KEY": "",
    }.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location(
        f"billing_addon_compaction_{uuid.uuid4().hex}", PROXY / "billing_addon.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Request:
    """mitmproxy's request as far as the addon reads it: setting `content`
    sets the bytes on the wire too, as it does there."""

    def __init__(self, path: str, headers: dict[str, str]):
        self.path = path
        self.http_version = "HTTP/1.1"
        self.host = ANTHROPIC
        self.scheme = "https"
        self.port = 443
        self.headers = headers
        self.stream = None
        self.raw_content = b""

    @property
    def content(self) -> bytes:
        return self.raw_content

    @content.setter
    def content(self, value: bytes) -> None:
        self.raw_content = value


def _forward(mod, record: dict) -> tuple[SimpleNamespace, bytes]:
    """One recorded request through the addon's hooks; what it sends upstream."""
    body = record["body"].encode()
    headers = {
        **record["headers"],
        "content-length": str(len(body)),
        "x-cheese-attr": f"{PROJECT}/{TOPIC}",
    }
    flow = SimpleNamespace(
        id=str(uuid.uuid4()),
        request=_Request(record["path"], headers),
        client_conn=SimpleNamespace(sni=ANTHROPIC, tls_established=True, id="c1"),
        server_conn=SimpleNamespace(via=None),
        metadata={},
        response=None,
    )
    asyncio.run(mod.requestheaders(flow))
    if flow.request.stream is False:
        flow.request.content = body
        asyncio.run(mod.request(flow))
        return flow, flow.request.content
    chunks = [body[i : i + 4096] for i in range(0, len(body), 4096)] + [b""]
    return flow, b"".join(flow.request.stream(c) for c in chunks)


def _model_names(value) -> list[str]:
    """Every value under a `model` key, at any depth."""
    if isinstance(value, dict):
        found = [v for k, v in value.items() if k == "model" and isinstance(v, str)]
        return found + [n for v in value.values() for n in _model_names(v)]
    if isinstance(value, list):
        return [n for v in value for n in _model_names(v)]
    return []


@pytest.mark.parametrize("pool", ["gateway", "subscription"])
def test_a_compacting_cli_reaches_upstream_naming_only_the_bound_model(
    monkeypatch, tmp_path, cli_requests, pool
):
    """Every model request the pinned CLI sends in a conversation that
    compacts, automatically and by `/compact`, leaves the proxy naming the
    bound model and no other: `model` carries the binding, and no top-level
    member outside the conversation itself names a model to run on instead."""
    mod = _load_addon(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "GATEWAY_BASE", "http://litellm.invalid:4000")
    verdict = core.Verdict(
        allow=True,
        reason="",
        pool=pool,
        key="sk-virtual-project-key" if pool == "gateway" else None,
        model=BOUND[pool],
        fail_open=False,
    )
    monkeypatch.setattr(mod, "ADMISSION_URL", "http://control-plane.invalid/admission")
    monkeypatch.setattr(
        mod, "ADMISSION", SimpleNamespace(check=lambda *args, **kwargs: verdict)
    )

    wrong = []
    for record in cli_requests:
        flow, sent = _forward(mod, record)
        assert flow.response is None, (record["headers"], flow.response.content)
        body = json.loads(sent)
        asked = json.loads(record["body"])["model"]
        # The subscription leaves the CLI's own background requests on the
        # haiku family it serves (`keep_haiku`); everything else is bound.
        background = (
            pool == "subscription"
            and core.is_haiku_name(asked)
            and record["headers"].get("x-claude-code-request-class")
            not in {"subagent", "workflow"}
        )
        expected = asked if background else BOUND[pool]
        others = {
            member: names
            for member, value in body.items()
            if member != "model" and member not in CONVERSATION
            if (names := _model_names(value))
        }
        if body.get("model") != expected or others:
            wrong.append(
                {
                    "class": record["headers"].get("x-claude-code-request-class"),
                    "compaction": record["headers"].get("x-claude-code-compaction"),
                    "model": body.get("model"),
                    "expected": expected,
                    "other members naming a model": others,
                }
            )
    assert wrong == [], json.dumps(wrong, indent=2)


def test_the_pinned_cli_sends_no_top_level_member_nobody_reviewed(cli_requests):
    """A CLI bump that adds a top-level member to its model requests is caught
    here before it ships, because such a member can name a model to run on —
    `fallbacks` arrived exactly that way and broke every compaction on the
    gateway."""
    sent = {member for r in cli_requests for member in json.loads(r["body"])}
    new = sorted(sent - REVIEWED_MEMBERS)
    assert not new, (
        f"the pinned Claude Code now sends top-level member(s) {new} on its model "
        "requests (bodies in provider-requests.json). Review each: if it names a "
        "model to run on, or one a router may fall back to, add it to "
        "FallbackModels.DROPPED in deploy/metering-proxy/cheese_billing_core.py; "
        "then add it to REVIEWED_MEMBERS here."
    )
