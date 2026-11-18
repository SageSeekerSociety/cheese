"""A session cannot put anything on the project's default branch.

A change enters the project only as a version of a delivery that a reviewer
adopts, and the platform merges it then with its own credential. So whatever
an AI teammate's session sends its forge through the proxy outside its
sandbox — a git push, or one of the forge's own APIs that commit, create,
move, rename or delete a branch, or change which branch is the default — is
refused when it would write the default branch, with nothing waiting for
review as much as with something. Its own task's branch, and the refs the
platform keeps a task's uncommitted work under, it still writes.

The proxy here is the real one (`_TerminatedHTTPHandler`, `ForgeProxyRuntime`)
in front of a forge that serves a real repository over git's HTTP protocol
(`git http-backend`) and records its API requests, and real `git` pushes
through it.
"""

import http.client
import json
import os
import socketserver
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent import forge_proxy
from tests.unit.test_forge_proxy_keeps_waiting_deliveries_open import (
    _graphql,
    _json,
    _UnixServer,
)

DEFAULT = "main"
TASK_BRANCH = "task/0a1b2c3d"
KEPT_REF = "refs/cheese/kept/0a1b2c3d-0000-4000-8000-000000000000"
#: Pull requests on the forge, by number, and the branch each one's head is.
PULL_HEADS = {7: DEFAULT, 8: TASK_BRANCH}
#: GitHub node ids the forge resolves.
NODES = {
    "REF_kwDOAAmain01": {"name": DEFAULT},
    "REF_kwDOAAtask02": {"name": TASK_BRANCH},
    "PR_kwDOAAmain03": {"headRefName": DEFAULT},
}
_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Teammate",
    "GIT_AUTHOR_EMAIL": "teammate@example.invalid",
    "GIT_COMMITTER_NAME": "Teammate",
    "GIT_COMMITTER_EMAIL": "teammate@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **_GIT_ENV},
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def forge(tmp_path):
    """A forge holding `acme/widgets` with one commit on its default branch,
    served to git by `git http-backend`; every API request is recorded."""
    root = tmp_path / "forge"
    bare = root / "acme" / "widgets.git"
    bare.mkdir(parents=True)
    _git("init", "-q", "--bare", "-b", DEFAULT, str(bare), cwd=tmp_path)
    _git("config", "http.receivepack", "true", cwd=bare)
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", DEFAULT, str(seed), cwd=tmp_path)
    (seed / "README").write_text("widgets\n")
    _git("add", "README", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    _git("push", "-q", str(bare), f"HEAD:refs/heads/{DEFAULT}", cwd=seed)
    api: list[tuple[str, str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            path, _, query = self.path.partition("?")
            if ".git/" in path:
                return self._git(path, query, body)
            api.append((self.command, self.path, body))
            payload: dict = {}
            if path == "/graphql" and b"nodes(ids:" in body:
                ids = json.loads(body)["variables"]["ids"]
                payload = {"data": {"nodes": [NODES.get(i) for i in ids]}}
            number = path.rsplit("/", 1)[-1]
            if "/pulls/" in path and number.isdigit():
                payload = {"head": {"ref": PULL_HEADS[int(number)]}}
            self._send(200, "application/json", json.dumps(payload).encode())

        def _git(self, path, query, body):
            env = {
                **os.environ,
                **_GIT_ENV,
                "GIT_PROJECT_ROOT": str(root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "REQUEST_METHOD": self.command,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
                "REMOTE_USER": "teammate",
            }
            out = subprocess.run(
                ["git", "http-backend"], input=body, env=env, capture_output=True
            ).stdout
            head, _, content = out.partition(b"\r\n\r\n")
            headers = dict(
                line.split(": ", 1) for line in head.decode().split("\r\n") if line
            )
            status = int(headers.pop("Status", "200").split()[0])
            self._send(status, headers.get("Content-Type", ""), content)

        def _send(self, status, kind, content):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], bare, api
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxy(forge):
    """The proxy in front of `forge`; its platform socket says the default
    branch is `main` and that nothing waits for review."""
    port, _bare, _api = forge
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
                        "default_branch": DEFAULT,
                    }
                },
                "/api/sandbox/forge-held": {"data": {"prs": []}},
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


@pytest.fixture
def checkout(tmp_path, forge, proxy):
    """A session's checkout of the repository, cloned through the proxy, with
    one new commit on top of the default branch."""
    work = tmp_path / "work"
    cloned = _git(
        "clone",
        "-q",
        f"http://127.0.0.1:{proxy}/acme/widgets.git",
        str(work),
        cwd=tmp_path,
    )
    assert cloned.returncode == 0, cloned.stderr
    (work / "feature.txt").write_text("feature\n")
    _git("add", "feature.txt", cwd=work)
    _git("commit", "-q", "-m", "feature", cwd=work)
    return work


def _tip(bare: Path, ref: str) -> str:
    return _git("rev-parse", "--verify", "-q", ref, cwd=bare).stdout.strip()


def _refused(push: subprocess.CompletedProcess) -> bool:
    """Git says the push was rejected, and why: hand the work in for review."""
    return push.returncode != 0 and "提交审阅" in push.stderr


def test_a_push_to_the_default_branch_is_refused(forge, checkout):
    _port, bare, _api = forge
    before = _tip(bare, DEFAULT)

    push = _git("push", "origin", f"HEAD:{DEFAULT}", cwd=checkout)

    assert _refused(push), push.stderr
    assert _tip(bare, DEFAULT) == before


def test_a_force_push_to_the_default_branch_is_refused(forge, checkout):
    _port, bare, _api = forge
    before = _tip(bare, DEFAULT)
    _git("checkout", "-q", "--orphan", "rewritten", cwd=checkout)
    _git("commit", "-q", "-m", "a history of its own", cwd=checkout)

    push = _git("push", "--force", "origin", f"HEAD:{DEFAULT}", cwd=checkout)

    assert _refused(push), push.stderr
    assert _tip(bare, DEFAULT) == before


def test_deleting_the_default_branch_is_refused(forge, checkout):
    _port, bare, _api = forge
    before = _tip(bare, DEFAULT)

    push = _git("push", "origin", f":{DEFAULT}", cwd=checkout)

    assert _refused(push), push.stderr
    assert _tip(bare, DEFAULT) == before


def test_a_push_that_also_names_the_default_branch_lands_nothing(forge, checkout):
    _port, bare, _api = forge
    before = _tip(bare, DEFAULT)

    push = _git(
        "push", "origin", f"HEAD:{TASK_BRANCH}", f"HEAD:{DEFAULT}", cwd=checkout
    )

    assert _refused(push), push.stderr
    assert _tip(bare, DEFAULT) == before
    assert _tip(bare, f"refs/heads/{TASK_BRANCH}") == ""


def test_its_own_task_branch_and_kept_work_still_land(forge, checkout):
    _port, bare, _api = forge
    head = _git("rev-parse", "HEAD", cwd=checkout).stdout.strip()

    to_branch = _git("push", "origin", f"HEAD:refs/heads/{TASK_BRANCH}", cwd=checkout)
    to_kept = _git("push", "origin", f"+HEAD:{KEPT_REF}", cwd=checkout)

    assert to_branch.returncode == 0, to_branch.stderr
    assert to_kept.returncode == 0, to_kept.stderr
    assert _tip(bare, f"refs/heads/{TASK_BRANCH}") == head
    assert _tip(bare, KEPT_REF) == head
    # And it may tidy its own branch: a force-push, then a delete.
    _git("commit", "-q", "--amend", "-m", "tidied", cwd=checkout)
    forced = _git("push", "--force", "origin", f"HEAD:{TASK_BRANCH}", cwd=checkout)
    assert forced.returncode == 0, forced.stderr
    deleted = _git("push", "origin", f":{TASK_BRANCH}", cwd=checkout)
    assert deleted.returncode == 0, deleted.stderr
    assert _tip(bare, f"refs/heads/{TASK_BRANCH}") == ""


def _send(port: int, method: str, path: str, body: bytes) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            method,
            path,
            body=body,
            headers={"Authorization": "token placeholder"},
        )
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        conn.close()


#: (method, path, body): the forge's APIs, each writing the default branch.
WRITES_THE_DEFAULT_BRANCH = {
    "GitHub contents API, no branch named (commits to the default)": (
        "PUT",
        "/repos/acme/widgets/contents/README",
        _json({"message": "edit", "content": "d2lkZ2V0cw=="}),
    ),
    "GitHub contents API naming the default branch": (
        "PUT",
        "/repos/acme/widgets/contents/README",
        _json({"message": "edit", "content": "d2lkZ2V0cw==", "branch": DEFAULT}),
    ),
    "GitHub contents API deleting a file on the default branch": (
        "DELETE",
        "/repos/acme/widgets/contents/README",
        _json({"message": "drop", "sha": "1" * 40}),
    ),
    "Forgejo contents API, no branch named": (
        "POST",
        "/forge/api/v1/repos/acme/widgets/contents/NEW",
        _json({"message": "add", "content": "d2lkZ2V0cw=="}),
    ),
    "Forgejo change-files API naming the default branch as the new branch": (
        "POST",
        "/forge/api/v1/repos/acme/widgets/contents",
        _json(
            {"branch": TASK_BRANCH, "new_branch": DEFAULT, "files": []},
        ),
    ),
    "Forgejo patch API onto the default branch": (
        "POST",
        "/forge/api/v1/repos/acme/widgets/diffpatch",
        _json({"content": "diff --git a/x b/x", "message": "patch"}),
    ),
    "GitHub ref update of the default branch": (
        "PATCH",
        f"/repos/acme/widgets/git/refs/heads/{DEFAULT}",
        _json({"sha": "1" * 40, "force": True}),
    ),
    "GitHub ref delete of the default branch": (
        "DELETE",
        f"/repos/acme/widgets/git/refs/heads/{DEFAULT}",
        b"",
    ),
    "GitHub ref creation of the default branch": (
        "POST",
        "/repos/acme/widgets/git/refs",
        _json({"ref": f"refs/heads/{DEFAULT}", "sha": "1" * 40}),
    ),
    "Forgejo branch creation of the default branch": (
        "POST",
        "/forge/api/v1/repos/acme/widgets/branches",
        _json({"new_branch_name": DEFAULT, "old_ref_name": TASK_BRANCH}),
    ),
    "Forgejo branch delete of the default branch": (
        "DELETE",
        f"/forge/api/v1/repos/acme/widgets/branches/{DEFAULT}",
        b"",
    ),
    "GitHub rename of the default branch": (
        "POST",
        f"/repos/acme/widgets/branches/{DEFAULT}/rename",
        _json({"new_name": "old-main"}),
    ),
    "GitHub rename of a task branch to the default branch's name": (
        "POST",
        f"/repos/acme/widgets/branches/{TASK_BRANCH}/rename",
        _json({"new_name": DEFAULT}),
    ),
    "Forgejo rename of a task branch to the default branch's name": (
        "PATCH",
        f"/forge/api/v1/repos/acme/widgets/branches/{TASK_BRANCH}",
        _json({"name": DEFAULT}),
    ),
    "GitHub branch protection change on the default branch": (
        "DELETE",
        f"/repos/acme/widgets/branches/{DEFAULT}/protection",
        b"",
    ),
    "GitHub repository edit making another branch the default": (
        "PATCH",
        "/repos/acme/widgets",
        _json({"default_branch": TASK_BRANCH}),
    ),
    "Forgejo repository edit making another branch the default": (
        "PATCH",
        "/forge/api/v1/repos/acme/widgets",
        _json({"default_branch": TASK_BRANCH}),
    ),
    "GitHub repository delete": ("DELETE", "/repos/acme/widgets", b""),
    "GitHub merge-upstream into the default branch": (
        "POST",
        "/repos/acme/widgets/merge-upstream",
        _json({"branch": DEFAULT}),
    ),
    "GitHub update of a pull request whose head is the default branch": (
        "PUT",
        "/repos/acme/widgets/pulls/7/update-branch",
        b"",
    ),
    "Forgejo update of a pull request whose head is the default branch": (
        "POST",
        "/forge/api/v1/repos/acme/widgets/pulls/7/update",
        b"",
    ),
    "GitHub merge of a branch into the default branch": (
        "POST",
        "/repos/acme/widgets/merges",
        _json({"base": DEFAULT, "head": TASK_BRANCH}),
    ),
    "GraphQL createCommitOnBranch on the default branch": (
        "POST",
        "/graphql",
        _graphql(
            "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch("
            "input: $input) { commit { oid } } }",
            {
                "input": {
                    "branch": {
                        "repositoryNameWithOwner": "acme/widgets",
                        "branchName": DEFAULT,
                    },
                    "message": {"headline": "edit"},
                    "expectedHeadOid": "1" * 40,
                }
            },
        ),
    ),
    "GraphQL createRef of the default branch": (
        "POST",
        "/graphql",
        _graphql(
            'mutation { createRef(input: {repositoryId: "R_kwDOAArepo01", '
            f'name: "refs/heads/{DEFAULT}", oid: "{"1" * 40}"}}) '
            "{ ref { name } } }"
        ),
    ),
    "GraphQL updateRef of the default branch's ref by node id": (
        "POST",
        "/graphql",
        _graphql(
            "mutation($id: ID!) { updateRef(input: {refId: $id, "
            f'oid: "{"1" * 40}", force: true}}) {{ ref {{ name }} }} }}',
            {"id": "REF_kwDOAAmain01"},
        ),
    ),
    "GraphQL deleteRef of the default branch's ref by node id": (
        "POST",
        "/graphql",
        _graphql(
            'mutation { deleteRef(input: {refId: "REF_kwDOAAmain01"}) '
            "{ clientMutationId } }"
        ),
    ),
    "GraphQL updateRefs naming the default branch": (
        "POST",
        "/graphql",
        _graphql(
            "mutation($u: [RefUpdate!]!) { updateRefs(input: "
            '{repositoryId: "R_kwDOAArepo01", refUpdates: $u}) '
            "{ clientMutationId } }",
            {"u": [{"name": f"refs/heads/{DEFAULT}", "afterOid": "1" * 40}]},
        ),
    ),
    "GraphQL updatePullRequestBranch of a pull request headed by it": (
        "POST",
        "/graphql",
        _graphql(
            "mutation { updatePullRequestBranch(input: {pullRequestId: "
            '"PR_kwDOAAmain03"}) { clientMutationId } }'
        ),
    ),
    "GraphQL mergeBranch into the default branch": (
        "POST",
        "/graphql",
        _graphql(
            'mutation { mergeBranch(input: {repositoryId: "R_kwDOAArepo01", '
            f'base: "{DEFAULT}", head: "{TASK_BRANCH}"}}) {{ clientMutationId }} }}'
        ),
    ),
}

#: The same APIs writing the session's own task branch.
WRITES_ITS_OWN_BRANCH = {
    "GitHub contents API on the task branch": (
        "PUT",
        "/repos/acme/widgets/contents/README",
        _json({"message": "edit", "content": "d2lkZ2V0cw==", "branch": TASK_BRANCH}),
    ),
    "Forgejo change-files API onto a new task branch": (
        "POST",
        "/forge/api/v1/repos/acme/widgets/contents",
        _json({"branch": DEFAULT, "new_branch": TASK_BRANCH, "files": []}),
    ),
    "GitHub ref creation of the task branch": (
        "POST",
        "/repos/acme/widgets/git/refs",
        _json({"ref": f"refs/heads/{TASK_BRANCH}", "sha": "1" * 40}),
    ),
    "GitHub ref delete of the task branch": (
        "DELETE",
        f"/repos/acme/widgets/git/refs/heads/{TASK_BRANCH}",
        b"",
    ),
    "GitHub update of a pull request headed by the task branch": (
        "PUT",
        "/repos/acme/widgets/pulls/8/update-branch",
        b"",
    ),
    "GitHub repository edit of its description": (
        "PATCH",
        "/repos/acme/widgets",
        _json({"description": "Widgets"}),
    ),
    "GraphQL updateRef of the task branch's ref by node id": (
        "POST",
        "/graphql",
        _graphql(
            'mutation { updateRef(input: {refId: "REF_kwDOAAtask02", '
            f'oid: "{"1" * 40}"}}) {{ ref {{ name }} }} }}'
        ),
    ),
}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    WRITES_THE_DEFAULT_BRANCH.values(),
    ids=WRITES_THE_DEFAULT_BRANCH.keys(),
)
def test_an_api_writing_the_default_branch_is_refused(forge, proxy, method, path, body):
    _port, _bare, api = forge

    status, answer = _send(proxy, method, path, body)

    assert status == 403
    # It says what to do instead: hand the work in.
    assert "cheese_card(action=request)" in answer
    assert (method, path, body) not in api


@pytest.mark.parametrize(
    ("method", "path", "body"),
    WRITES_ITS_OWN_BRANCH.values(),
    ids=WRITES_ITS_OWN_BRANCH.keys(),
)
def test_an_api_writing_its_own_branch_reaches_the_forge(
    forge, proxy, method, path, body
):
    _port, _bare, api = forge

    status, _answer = _send(proxy, method, path, body)

    assert status == 200
    assert (method, path, body) in api
