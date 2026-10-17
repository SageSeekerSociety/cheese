"""The one door a session's harness reaches the project's MCP servers through.

Every harness here has an MCP client of its own (Claude Code, Codex, pi), so
the platform does not stand between the model and the tools with one of its
own: it serves each of the project's servers to that client as an ordinary
Streamable HTTP server on this machine's loopback, one path per server
(``/mcp/<name>``). Behind the door a call goes where the server is served
(``RemoteClient.call``): a remote server's to the platform, which holds its
credential, and a command's to the room's machine, which runs it. So none of
the three learns where a server lives or holds a credential for it, and a
server reaches all three the same way.

It serves tools and nothing else. What it answers itself is the handshake, of
the protocol revisions whose handshake is ``initialize``: no upstream session
is opened per client, the machine and the platform already hold theirs. A
client that asks first in the later, stateless revision (``server/discover``)
is told the method does not exist and falls back to ``initialize``, as those
revisions say it must. The revision answered is the client's own when it is
one of these, else the one all three clients share.

The hooks a project sets on tool calls are the harness's to fire, around the
tool call it makes here — so a tool call goes on with ``platform`` set, which
tells the machine they have already been fired — unless the harness fires
none of its own (``tool_call``).

A shared session machine runs many sessions, each with a door of its own; the
loopback is reachable by all of them, so each door takes only requests that
carry its own secret (``SECRET_HEADER``).

Standard library only: this runs inside the session machine's runner.
"""

import hmac
import json
import secrets
import threading
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote

SECRET_HEADER = "X-Cheese-Gateway"
#: The variables a harness's MCP configuration reads the gateway's port and
#: secret from, set on its process: a secret on a command line would be read
#: by every process on the shared machine.
PORT_ENV = "CHEESE_MCP_PORT"
SECRET_ENV = "CHEESE_MCP_SECRET"
#: How long a harness waits for a server to answer its first list: a command's
#: first start downloads its package (`npx`, `uvx`), and the room's machine may
#: still be being prepared.
STARTUP_S = 300
#: The revisions whose handshake is ``initialize``, oldest first.
REVISIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
#: Answered to a client asking for a revision not in ``REVISIONS``: the newest
#: one Claude Code, Codex and pi all speak.
SHARED_REVISION = "2025-06-18"
#: How long a closing door waits to have said so.
CLOSE_WAIT_S = 3
#: A JSON-RPC body larger than this is refused rather than read.
MAX_BODY = 16 * 1024 * 1024

Call = Callable[[str, dict], Any]
#: A tool call, ``{"id", "server", "tool", "args"}`` to what ``invoke`` returns.
ToolCall = Callable[[dict], dict]


class Gateway:
    """``call`` is ``RemoteClient.call`` (or what stands in for it in a test);
    ``servers`` says which names are served right now, read on every request,
    so a server added while the session runs is reachable without a restart.

    ``tool_call`` makes a tool call; by default it goes on marked as already
    hooked. A harness that fires no hooks of its own (Codex, whose every tool
    is the platform's) passes one that has them run around the call.

    ``observe(server, error)`` hears how each list of a server's tools went
    (``error`` None when it went well), and may answer with what to tell the
    client instead of the error; ``on_close`` is told the door is closing.
    Both are `RemoteClient`'s (`local_outcome`, `local_closed`)."""

    def __init__(
        self,
        call: Call,
        servers: Callable[[], list[str]],
        *,
        tool_call: ToolCall | None = None,
        observe: Callable[[str, str | None], str | None] | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.call = call
        self.servers = servers
        self.observe = observe
        self.on_close = on_close
        self.tool_call = tool_call or (
            lambda params: call("invoke", {**params, "platform": True})
        )
        self.secret = secrets.token_urlsafe(32)
        self.server: ThreadingHTTPServer | None = None

    def start(self) -> int:
        gateway = self

        class Handler(_Handler):
            owner = gateway

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="mcp-gateway",
            daemon=True,
        ).start()
        return self.port

    @property
    def port(self) -> int:
        assert self.server is not None, "the gateway has not started"
        return self.server.server_address[1]

    def url(self, server: str) -> str:
        return f"http://127.0.0.1:{self.port}/mcp/{server}"

    @property
    def headers(self) -> dict[str, str]:
        return {SECRET_HEADER: self.secret}

    def close(self) -> None:
        if self.on_close is not None:
            # Said, not waited on: a platform that does not answer must not
            # hold a session's ending.
            closing = threading.Thread(target=self.on_close, daemon=True)
            closing.start()
            closing.join(CLOSE_WAIT_S)
            self.on_close = None
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def answer(self, server: str, message: dict) -> dict | None:
        """The response to one JSON-RPC message, or None for a notification."""
        if "id" not in message:
            return None
        method = message.get("method")
        params = message.get("params") or {}
        try:
            result = self._result(server, method, params)
        except _NoSuchMethod:
            return _error(message["id"], -32601, f"Method not found: {method}")
        except Exception as exc:
            return _error(message["id"], -32603, str(exc) or type(exc).__name__)
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}

    def _result(self, server: str, method: Any, params: dict):
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {
                "protocolVersion": asked if asked in REVISIONS else SHARED_REVISION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": server, "version": "1"},
            }
        if method == "ping":
            return {}
        # Only tools are served (``capabilities`` says so). Resources and
        # prompts are not asked of the server: answering them would start a
        # command, or take the machine, for a list nobody reads.
        if method not in ("tools/list", "tools/call"):
            raise _NoSuchMethod
        if method == "tools/call":
            receipt = self.tool_call(
                {
                    # Fresh for every call: a client numbers its requests
                    # from the start again whenever it reconnects, and the
                    # machine answers a repeated id with what it answered first.
                    "id": f"mcp-{uuid.uuid4().hex}",
                    "server": server,
                    "tool": params.get("name"),
                    "args": params.get("arguments") or {},
                }
            )
            # A call that did not happen — a hook said no, the machine is out of
            # reach, the server failed — is the tool's answer, not the
            # protocol's: the model reads it and decides what to do next.
            return receipt.get("value") or {
                "content": [{"type": "text", "text": receipt.get("error", "")}],
                "isError": True,
            }
        try:
            listed = self.call(
                "mcp", {"server": server, "method": "tools/list", "params": params}
            )
        except Exception as exc:
            said = self.observe(server, str(exc)) if self.observe else None
            if said is not None:
                raise RuntimeError(said) from exc
            raise
        if self.observe is not None:
            self.observe(server, None)
        return listed


class _NoSuchMethod(Exception):
    pass


def _error(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


class _Handler(BaseHTTPRequestHandler):
    owner: Gateway
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        server = self._server()
        if server is None:
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(413, None)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            self._send(400, _error(None, -32700, "Parse error"))
            return
        if isinstance(body, list):
            answers = [
                a
                for m in body
                if isinstance(m, dict)
                if (a := self.owner.answer(server, m)) is not None
            ]
            self._send(200 if answers else 202, answers or None)
        elif isinstance(body, dict):
            answer = self.owner.answer(server, body)
            self._send(202 if answer is None else 200, answer)
        else:
            self._send(400, _error(None, -32600, "Invalid Request"))

    def do_GET(self) -> None:
        # No server-sent stream: nothing here speaks first.
        if self._server() is not None:
            self._send(405, None)

    def do_DELETE(self) -> None:
        # There is no session to end; a client that ends one is told so.
        if self._server() is not None:
            self._send(405, None)

    def _server(self) -> str | None:
        given = self.headers.get(SECRET_HEADER, "")
        if not hmac.compare_digest(given.encode(), self.owner.secret.encode()):
            self._send(401, None)
            return None
        prefix, _, quoted = self.path.split("?", 1)[0].rpartition("/")
        name = unquote(quoted)
        if prefix != "/mcp" or name not in self.owner.servers():
            self._send(404, None)
            return None
        return name

    def _send(self, status: int, payload: Any) -> None:
        data = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
