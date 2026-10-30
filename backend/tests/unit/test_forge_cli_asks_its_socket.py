"""The forge CLIs' launcher on a machine with a platform socket.

The rules, as a person would state them: the forge credentials are asked of
the socket, with no token; a forge tunnel connection is opened on a credential
the socket hands out for it.
"""

import json
import os
import socketserver
import ssl
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent import forge_cli, forge_proxy


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("socket", 0)


@pytest.fixture
def platform_socket(monkeypatch):
    asked = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(
                (
                    self.path,
                    self.headers.get("authorization"),
                )
            )
            payload = {
                "/api/sandbox/forge-token": {"data": {"kind": "github_app"}},
                "/credential": {"token": "handed", "expires_in": 60},
            }[self.path]
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "platform.sock")
    server = _Server(path, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("CHEESE_PLATFORM_SOCKET", path)
    monkeypatch.setenv("CHEESE_TOKEN", "the-machines-own")
    yield asked
    server.shutdown()
    server.server_close()


def test_the_forge_credentials_are_asked_of_the_socket_without_a_token(
    platform_socket,
):
    assert forge_cli.credentials() == {"kind": "github_app"}
    assert platform_socket == [("/api/sandbox/forge-token", None)]


def test_the_forge_proxy_refuses_pr_merges_and_injects_the_token():
    assert forge_proxy.is_pr_merge_request(
        "PUT", "/repos/SageSeekerSociety/cheese/pulls/4896/merge"
    )
    assert forge_proxy.is_pr_merge_request(
        "PUT", "/api/v1/repos/org/repo/pulls/12/merge?foo=1"
    )
    assert forge_proxy.is_pr_merge_request(
        "POST",
        "/graphql",
        b'{"query": "mutation { mergePullRequest(input: {pullRequestId: \\"1\\"}) { clientMutationId } }"}',
    )
    assert forge_proxy.is_pr_merge_request(
        "POST",
        "/graphql",
        b'{"query": "mutation { enablePullRequestAutoMerge(input: {}) { clientMutationId } }"}',
    )
    assert not forge_proxy.is_pr_merge_request(
        "GET", "/repos/SageSeekerSociety/cheese/pulls/4896"
    )
    assert not forge_proxy.is_pr_merge_request(
        "POST", "/graphql", b'{"query": "query { viewer { login } }"}'
    )
    assert (
        forge_proxy.rewrite_authorization(
            "Bearer placeholder", "x-access-token", "ghs_real"
        )
        == "Bearer ghs_real"
    )
    assert (
        forge_proxy.rewrite_authorization(
            "token placeholder", "x-access-token", "ghs_real"
        )
        == "token ghs_real"
    )


def test_the_forge_proxy_terminates_tls_with_machine_ca_and_blocks_merge(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    ca_dir = tmp_path / "ca"
    platform_dir = tmp_path / "platform"
    _, bundle = forge_proxy.ensure_ca(ca_dir, platform_dir)
    assert (ca_dir / "ca.key").is_file()
    assert (platform_dir / "ca.pem").is_file()
    assert bundle.is_file()

    seen_auth = []

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen_auth.append((self.path, self.headers.get("Authorization")))
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        runtime = forge_proxy.ForgeProxyRuntime(
            platform_dir=platform_dir,
            ca_dir=ca_dir,
            api_url="http://127.0.0.1:9",
            preview_url="",
            proof="proof",
        )
        runtime.platform._cached_forge = {
            "kind": "github_app",
            "project_id": "p1",
            "token": "ghs_secret_single_repo_token",
            "username": "x-access-token",
            "url": f"http://127.0.0.1:{upstream.server_port}/org/repo.git",
            "api_url": f"http://127.0.0.1:{upstream.server_port}",
        }
        runtime.platform._cached_until = 1e18

        forge_sock = Path(tempfile.mkdtemp(dir="/tmp")) / "forge.sock"
        srv = forge_proxy._ForgeSocketServer(
            str(forge_sock), forge_proxy._ForgeSocketHandler, runtime
        )
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        from app.domain.agent.harness.claude_code.remote_execution.runtime import (
            _start_forge_bridge,
        )

        bridge = _start_forge_bridge(forge_sock)
        try:
            proxy_url = f"http://127.0.0.1:{bridge.server_address[1]}"
            ctx = ssl.create_default_context(cafile=str(bundle))
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"https": proxy_url}),
                urllib.request.HTTPSHandler(context=ctx),
            )
            req = urllib.request.Request(
                f"https://127.0.0.1:{upstream.server_port}/repos/org/repo/pulls/1",
                headers={"Authorization": "Bearer placeholder"},
            )
            with opener.open(req, timeout=10) as resp:
                assert json.load(resp) == {"ok": True}
            assert seen_auth == [
                ("/repos/org/repo/pulls/1", "Bearer ghs_secret_single_repo_token")
            ]

            merge_req = urllib.request.Request(
                f"https://127.0.0.1:{upstream.server_port}/repos/org/repo/pulls/1/merge",
                data=b"{}",
                headers={"Authorization": "Bearer placeholder"},
                method="PUT",
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                opener.open(merge_req, timeout=10)
            assert exc_info.value.code == 403
        finally:
            bridge.shutdown()
            bridge.server_close()
            srv.shutdown()
            srv.server_close()
    finally:
        upstream.shutdown()
        upstream.server_close()
