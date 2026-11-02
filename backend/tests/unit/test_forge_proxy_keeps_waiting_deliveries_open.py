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

import gzip
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
#: A commit already on the forge with nothing over `main` (pushing the held
#: branch to it leaves the pull request empty, and the forge closes it).
BASE_TIP = "b" * 40
#: A commit the forge has not seen: it comes in with the push itself.
NEW_COMMIT = "c" * 40


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
            status = 200
            if self.path == "/graphql" and b"nodes(ids:" in body:
                ids = json.loads(body)["variables"]["ids"]
                payload = {"data": {"nodes": [{"number": NODES[i]} for i in ids]}}
            if "/compare/" in self.path:
                # GitHub's compare: how far the pushed commit is past the base.
                if self.path.endswith(BASE_TIP):
                    payload = {"status": "behind", "ahead_by": 0}
                else:
                    status, payload = 404, {"message": "No commit found"}
            raw = json.dumps(payload).encode()
            self.send_response(status)
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
                        "repo": "acme/widgets",
                    }
                },
                "/api/sandbox/forge-held": {
                    "data": {
                        "prs": [
                            {"number": HELD_PR, "branch": HELD_BRANCH, "base": "main"}
                        ]
                    }
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


def _send(port: int, method: str, path: str, body: bytes, **headers: str) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            method,
            path,
            body=body,
            headers={"Authorization": "token placeholder", **headers},
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


def _graphql(query: str, variables: dict | None = None) -> bytes:
    return _json({"query": query, "variables": variables or {}})


def _escaped_graphql_close(node: str) -> bytes:
    """`closePullRequest` with its first letter as a JSON `\\u` escape: the
    same request to a JSON parser, other bytes to a pattern."""
    return _graphql_close(node).replace(b"closePullRequest", b"\\u0063losePullRequest")


def _push(*updates: tuple[str, str, str], gzip_members: int = 0) -> bytes:
    lines = b""
    for index, (old, new, ref) in enumerate(updates):
        text = f"{old} {new} {ref}" + ("\0report-status" if index == 0 else "") + "\n"
        line = text.encode()
        lines += f"{len(line) + 4:04x}".encode() + line
    body = lines + b"0000"
    if not gzip_members:
        return body
    # Split into several gzip members, as a client may stream it.
    step = max(1, len(body) // gzip_members)
    parts = [body[i : i + step] for i in range(0, len(body), step)]
    return b"".join(gzip.compress(part) for part in parts)


GZIP = {"Content-Encoding": "gzip"}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
CLOSED = _json({"state": "closed"})

#: (method, path, body, headers): requests that close the held pull request,
#: or delete, rename or empty its branch, each said a different way.
CLOSES_THE_HELD_PR = {
    "REST close": ("PATCH", f"/repos/acme/widgets/pulls/{HELD_PR}", CLOSED, {}),
    "REST close as an issue": (
        "PATCH",
        f"/repos/acme/widgets/issues/{HELD_PR}",
        CLOSED,
        {},
    ),
    "REST close under the platform forge's base path": (
        "PATCH",
        f"/forge/api/v1/repos/acme/widgets/pulls/{HELD_PR}",
        CLOSED,
        {},
    ),
    "REST close as an issue under the base path": (
        "PATCH",
        f"/forge/api/v1/repos/acme/widgets/issues/{HELD_PR}",
        CLOSED,
        {},
    ),
    "REST close with doubled slashes": (
        "PATCH",
        f"//repos/acme//widgets/pulls//{HELD_PR}/",
        CLOSED,
        {},
    ),
    "REST close through /repositories/{id}": (
        "PATCH",
        f"/repositories/4242/pulls/{HELD_PR}",
        CLOSED,
        {},
    ),
    "REST close, form-encoded": (
        "PATCH",
        f"/repos/acme/widgets/pulls/{HELD_PR}",
        b"state=closed",
        FORM,
    ),
    "REST close as POST with a method override": (
        "POST",
        f"/repos/acme/widgets/pulls/{HELD_PR}",
        CLOSED,
        {"X-HTTP-Method-Override": "PATCH"},
    ),
    "REST edit with a body that cannot be read": (
        "PATCH",
        f"/repos/acme/widgets/pulls/{HELD_PR}",
        b"\xff\xfe not a body",
        {},
    ),
    "GraphQL close (gh pr close)": (
        "POST",
        "/graphql",
        _graphql_close("PR_kwDOAAheld01"),
        {},
    ),
    "GraphQL close spelled with a JSON escape": (
        "POST",
        "/graphql",
        _escaped_graphql_close("PR_kwDOAAheld01"),
        {},
    ),
    "GraphQL close under an alias, after a comment": (
        "POST",
        "/graphql",
        _graphql(
            "# tidy up\nmutation Tidy { done: closePullRequest(input: "
            '{pullRequestId: "PR_kwDOAAheld01"}) { clientMutationId } }'
        ),
        {},
    ),
    "GraphQL updateRefs on the branch": (
        "POST",
        "/graphql",
        _graphql(
            "mutation($r: ID!, $u: [RefUpdate!]!) { updateRefs(input: "
            "{repositoryId: $r, refUpdates: $u}) { clientMutationId } }",
            {
                "r": "R_kwDOAArepo01",
                "u": [{"name": f"refs/heads/{HELD_BRANCH}", "afterOid": BASE_TIP}],
            },
        ),
        {},
    ),
    "GraphQL mutation that cannot be parsed": (
        "POST",
        "/graphql",
        _graphql("mutation { closePullRequest(input: {pullRequestId: "),
        {},
    ),
    "REST branch delete": (
        "DELETE",
        f"/repos/acme/widgets/git/refs/heads/{HELD_BRANCH}",
        b"",
        {},
    ),
    "REST branch delete under the base path": (
        "DELETE",
        f"/forge/api/v1/repos/acme/widgets/branches/{HELD_BRANCH}",
        b"",
        {},
    ),
    "REST branch rename (then delete it under its new name)": (
        "POST",
        f"/repos/acme/widgets/branches/{HELD_BRANCH}/rename",
        _json({"new_name": "gone"}),
        {},
    ),
    "push deleting its branch": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        _push(("1" * 40, "0" * 40, f"refs/heads/{HELD_BRANCH}")),
        {},
    ),
    "push deleting its branch, gzip in several members": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        _push(
            ("2" * 40, "0" * 40, "refs/heads/cheese/other-task"),
            ("1" * 40, "0" * 40, f"refs/heads/{HELD_BRANCH}"),
            gzip_members=3,
        ),
        GZIP,
    ),
    "force-push leaving its branch with nothing over the base": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        _push(("1" * 40, BASE_TIP, f"refs/heads/{HELD_BRANCH}")),
        {},
    ),
    "REST close with the key in capitals (Go decodes keys case-insensitively)": (
        "PATCH",
        f"/repos/acme/widgets/pulls/{HELD_PR}",
        b'{"STATE": "closed"}',
        {},
    ),
    "REST close with the key folded (ſ is s to Go)": (
        "PATCH",
        f"/forge/api/v1/repos/acme/widgets/pulls/{HELD_PR}",
        '{"\u017ftate": "closed"}'.encode(),
        {},
    ),
    "REST close where a later key overrides an earlier one": (
        "PATCH",
        f"/forge/api/v1/repos/acme/widgets/pulls/{HELD_PR}",
        b'{"state": "open", "State": "closed"}',
        {},
    ),
    "REST delete of the pull request as an issue": (
        "DELETE",
        f"/forge/api/v1/repos/acme/widgets/issues/{HELD_PR}",
        b"",
        {},
    ),
    "REST delete of the repository": ("DELETE", "/repos/acme/widgets", b"", {}),
    "REST branch delete with the slash encoded twice": (
        "DELETE",
        "/repos/acme/widgets/git/refs/heads/cheese%252Fheld-task",
        b"",
        {},
    ),
    "GraphQL close by updatePullRequest with CLOSED as a variable default": (
        "POST",
        "/graphql",
        _graphql(
            "mutation($s: PullRequestUpdateState = CLOSED) { updatePullRequest("
            'input: {pullRequestId: "PR_kwDOAAheld01", state: $s}) '
            "{ clientMutationId } }"
        ),
        {},
    ),
    "GraphQL close with variables sent as a JSON string": (
        "POST",
        "/graphql",
        _json(
            {
                "query": "mutation($id: ID!) { closePullRequest(input: "
                "{pullRequestId: $id}) { clientMutationId } }",
                "variables": json.dumps({"id": "PR_kwDOAAheld01"}),
            }
        ),
        {},
    ),
    "push to the held pull request's base (it would be marked merged)": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        _push(("1" * 40, NEW_COMMIT, "refs/heads/main")),
        {},
    ),
    "REST update of the base branch's ref": (
        "PATCH",
        "/repos/acme/widgets/git/refs/heads/main",
        _json({"sha": "1" * 40, "force": False}),
        {},
    ),
    "REST merge of the held branch into the base without a pull request": (
        "POST",
        "/repos/acme/widgets/merges",
        _json({"base": "main", "head": HELD_BRANCH}),
        {},
    ),
    "push whose body cannot be decompressed": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        b"not gzip at all",
        GZIP,
    ),
}

LEAVES_THE_HELD_PR = {
    "REST close of another PR": (
        "PATCH",
        "/repos/acme/widgets/pulls/2",
        CLOSED,
        {},
    ),
    "REST retitle of the held PR": (
        "PATCH",
        f"/repos/acme/widgets/pulls/{HELD_PR}",
        _json({"title": "fix(login): keep the session"}),
        {},
    ),
    "REST retitle of the held PR, form-encoded": (
        "PATCH",
        f"/forge/api/v1/repos/acme/widgets/pulls/{HELD_PR}",
        b"title=keep+the+session",
        FORM,
    ),
    "GraphQL close of another PR": (
        "POST",
        "/graphql",
        _graphql_close("PR_kwDOAAfree02"),
        {},
    ),
    "GraphQL query naming closePullRequest only as a string": (
        "POST",
        "/graphql",
        _graphql(
            'query { search(query: "closePullRequest", type: ISSUE, first: 1) { issueCount } }'
        ),
        {},
    ),
    "REST edit where a later key reopens (Go keeps the last)": (
        "PATCH",
        f"/repos/acme/widgets/pulls/{HELD_PR}",
        b'{"State": "closed", "state": "open"}',
        {},
    ),
    "GraphQL retitle of the held PR": (
        "POST",
        "/graphql",
        _graphql(
            'mutation { updatePullRequest(input: {pullRequestId: "PR_kwDOAAheld01", '
            'title: "keep the session"}) { clientMutationId } }'
        ),
        {},
    ),
    "push deleting another branch": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        _push(("2" * 40, "0" * 40, "refs/heads/cheese/other-task")),
        {},
    ),
    "push of new commits to the held branch": (
        "POST",
        "/acme/widgets.git/git-receive-pack",
        _push(("1" * 40, NEW_COMMIT, f"refs/heads/{HELD_BRANCH}"), gzip_members=2),
        GZIP,
    ),
}


@pytest.mark.parametrize(
    ("method", "path", "body", "headers"),
    CLOSES_THE_HELD_PR.values(),
    ids=CLOSES_THE_HELD_PR.keys(),
)
def test_closing_a_waiting_delivery_is_refused(
    proxy, forge, method, path, body, headers
):
    _port, seen = forge

    status = _send(proxy, method, path, body, **headers)

    assert status == 403
    # Nothing that request said reached the forge (looking a node id up is
    # the proxy's own question, not the agent's request).
    assert (method, path, body) not in seen


@pytest.mark.parametrize(
    ("method", "path", "body", "headers"),
    LEAVES_THE_HELD_PR.values(),
    ids=LEAVES_THE_HELD_PR.keys(),
)
def test_everything_else_reaches_the_forge(proxy, forge, method, path, body, headers):
    _port, seen = forge

    status = _send(proxy, method, path, body, **headers)

    assert status == 200
    assert (method, path, body) in seen
