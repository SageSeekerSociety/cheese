"""The forge proxy does not let an agent close a delivery waiting on a person.

A pull request whose card waits on a person is that person's to decide.
Closing it — or deleting its branch, which closes it too — voids the delivery:
the card poller sees the pull request closed and revokes the card, and the
task then closes as if nothing were waiting. The task's own close refuses the
agent while the delivery waits, so the proxy outside the sandbox, which holds
the forge credential, refuses it the other way round as well. Every other pull
request and branch is the agent's to close as before.

The proxy here is the real one (`_TerminatedHTTPHandler`, `ForgeProxyRuntime`)
in front of a recording forge, asking a stand-in platform socket which pull
requests wait.
"""

import http.client
import json
import os
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent import forge_proxy

HELD_PR = 1
HELD_BRANCH = "cheese/held-task"
#: GitHub node ids the forge resolves: the held pull request and another one.
NODES = {"PR_kwDOAAheld01": HELD_PR, "PR_kwDOAAfree02": 2}


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("socket", 0)


@pytest.fixture
def forge():
    """A forge that records every request it is sent and answers node lookups."""
    seen: list[tuple[str, str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            seen.append((self.command, self.path, body))
            payload: dict = {}
            if self.path == "/graphql" and b"nodes(ids:" in body:
                ids = json.loads(body)["variables"]["ids"]
                payload = {"data": {"nodes": [{"number": NODES[i]} for i in ids]}}
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_PATCH = do_DELETE = _answer

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], seen
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxy(forge):
    """The proxy in front of `forge`, its platform socket saying which pull
    request waits on a person."""
    port, _seen = forge
    platform_dir = Path(tempfile.mkdtemp(dir="/tmp"))

    class Platform(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = {
                "/api/sandbox/forge-token": {
                    "data": {
                        "kind": "github_app",
                        "token": "installation-token",
                        "api_url": f"http://127.0.0.1:{port}",
                    }
                },
                "/api/sandbox/forge-held": {
                    "data": {"numbers": [HELD_PR], "branches": [HELD_BRANCH]}
                },
            }[self.path]
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    platform = _UnixServer(str(platform_dir / "platform.sock"), Platform)
    threading.Thread(target=platform.serve_forever, daemon=True).start()
    runtime = forge_proxy.ForgeProxyRuntime(
        platform_dir=platform_dir,
        ca_dir=platform_dir / "ca",
        api_url="http://platform.invalid/api",
        preview_url="",
        proof="proof",
    )

    def handler(request, address, server):
        return forge_proxy._TerminatedHTTPHandler(
            request,
            address,
            server,
            target_host="127.0.0.1",
            target_port=port,
            target_scheme="http",
        )

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server.proxy = runtime  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    platform.shutdown()
    platform.server_close()
    os.unlink(platform_dir / "platform.sock")


def _send(port: int, method: str, path: str, body: bytes) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            method,
            path,
            body=body,
            headers={"Authorization": "token placeholder"},
        )
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def _json(value: dict) -> bytes:
    return json.dumps(value).encode()


def _graphql_close(node: str) -> bytes:
    return _json(
        {
            "query": "mutation($input:ClosePullRequestInput!)"
            "{closePullRequest(input:$input){pullRequest{state}}}",
            "variables": {"input": {"pullRequestId": node}},
        }
    )


def _push_deleting(branch: str) -> bytes:
    line = f"{'1' * 40} {'0' * 40} refs/heads/{branch}\0report-status\n".encode()
    return f"{len(line) + 4:04x}".encode() + line + b"0000"


CLOSES_THE_HELD_PR = {
    "REST close": (
        "PATCH",
        f"/repos/acme/widgets/pulls/{HELD_PR}",
        _json({"state": "closed"}),
    ),
    "REST close as an issue": (
        "PATCH",
        f"/repos/acme/widgets/issues/{HELD_PR}",
        _json({"state": "closed"}),
    ),
    "GraphQL close (gh pr close)": (
        "POST",
        "/graphql",
        _graphql_close("PR_kwDOAAheld01"),
    ),
    "REST branch delete": (
        "DELETE",
        f"/repos/acme/widgets/git/refs/heads/{HELD_BRANCH}",
        b"",
    ),
    "push deleting its branch": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        _push_deleting(HELD_BRANCH),
    ),
}

LEAVES_THE_HELD_PR = {
    "REST close of another PR": (
        "PATCH",
        "/repos/acme/widgets/pulls/2",
        _json({"state": "closed"}),
    ),
    "REST retitle of the held PR": (
        "PATCH",
        f"/repos/acme/widgets/pulls/{HELD_PR}",
        _json({"title": "fix(login): keep the session"}),
    ),
    "GraphQL close of another PR": (
        "POST",
        "/graphql",
        _graphql_close("PR_kwDOAAfree02"),
    ),
    "push deleting another branch": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        _push_deleting("cheese/other-task"),
    ),
}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    CLOSES_THE_HELD_PR.values(),
    ids=CLOSES_THE_HELD_PR.keys(),
)
def test_closing_a_waiting_delivery_is_refused(proxy, forge, method, path, body):
    _port, seen = forge

    status = _send(proxy, method, path, body)

    assert status == 403
    # Nothing that request said reached the forge (looking a node id up is
    # the proxy's own question, not the agent's request).
    assert (method, path, body) not in seen


@pytest.mark.parametrize(
    ("method", "path", "body"),
    LEAVES_THE_HELD_PR.values(),
    ids=LEAVES_THE_HELD_PR.keys(),
)
def test_everything_else_reaches_the_forge(proxy, forge, method, path, body):
    _port, seen = forge

    status = _send(proxy, method, path, body)

    assert status == 200
    assert (method, path, body) in seen
