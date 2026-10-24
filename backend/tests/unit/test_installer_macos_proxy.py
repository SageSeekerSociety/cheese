"""install.sh downloads the connector through the Mac's own proxy setting.

macOS keeps the proxy in System Settings and curl does not read it, so a Mac on
a campus network fetched the connector straight and slowly, or not at all. The
served script is run for real, with real curl, against a stand-in `scutil` that
reports a proxy and a proxy the request has to pass through.

The origin is served over TLS, as the deployment's is: curl reads a different
variable for an https URL than for an http one, and a test that fetched over
plain http would leave the variable production uses unexercised.
"""

import datetime
import hashlib
import http.server
import os
import select
import socket
import ssl
import stat
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.routes.installer import router
from tests.tls_gateway import write_pki

BINARY = bytes(range(256)) * 4096  # 1 MiB, every byte position distinguishable
DIGEST = hashlib.sha256(BINARY).hexdigest()

# Requests each server answered, by port, so a test can tell which one carried
# the bytes.
answered: list[tuple[int, str]] = []


class _ServesTheBinary(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        answered.append((self.server.server_address[1], self.path))
        self.send_response(200)
        self.send_header("Content-Length", str(len(BINARY)))
        self.send_header("X-Checksum-SHA256", DIGEST)
        self.end_headers()
        self.wfile.write(BINARY)

    def log_message(self, *args):
        pass


class _Tunnels(http.server.BaseHTTPRequestHandler):
    """A proxy: records the CONNECT and relays the tunnel to the origin."""

    def do_CONNECT(self):
        answered.append((self.server.server_address[1], f"CONNECT {self.path}"))
        host, _, port = self.path.partition(":")
        try:
            upstream = socket.create_connection((host, int(port)), 5)
        except OSError:
            self.send_response(502)
            self.end_headers()
            return
        self.send_response(200, "Connection established")
        self.end_headers()
        try:
            while True:
                ready, _, _ = select.select([self.connection, upstream], [], [], 5)
                for sock in ready:
                    chunk = sock.recv(65536)
                    if not chunk:
                        return
                    (upstream if sock is self.connection else self.connection).sendall(
                        chunk
                    )
        except OSError:
            pass
        finally:
            upstream.close()

    def do_GET(self):
        # An https client reaches a proxy by CONNECT, never with a bare request;
        # record one so a test seeing it knows the wrong variable was in play.
        answered.append((self.server.server_address[1], f"GET {self.path}"))
        self.send_response(502)
        self.end_headers()

    def log_message(self, *args):
        pass


def _dead_port() -> int:
    """A port nothing listens on: a proxy entry pointing here cannot answer."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(handler, tls: ssl.SSLContext | None = None):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _scutil_reporting(
    https_port: int,
    exceptions: tuple[str, ...] = ("*.local",),
    http_port: int | None = None,
) -> str:
    """What `scutil --proxy` prints for a Mac whose proxy listens on
    ``https_port``. ``http_port`` is where the HTTP entry points, defaulting to
    the same; a test gives the HTTP entry a port nothing listens on so that an
    https download can only succeed through the HTTPS entry."""
    if http_port is None:
        http_port = https_port
    entries = "\n".join(f"    {i} : {e}" for i, e in enumerate(exceptions))
    return f"""#!/bin/sh
[ "$1" = "--proxy" ] || exit 1
cat <<'EOF'
<dictionary> {{
  ExceptionsList : <array> {{
{entries}
  }}
  ExcludeSimpleHostnames : 0
  HTTPEnable : 1
  HTTPPort : {http_port}
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : {https_port}
  HTTPSProxy : 127.0.0.1
  SOCKSEnable : 0
}}
EOF
"""


class _Run:
    def __init__(self, run, proxy, origin, home):
        self.run, self.proxy, self.origin, self.home = run, proxy, origin, home

    def answered_by(self, server) -> list[str]:
        port = server.server_address[1]
        return [path for seen, path in answered if seen == port]


def _install(
    tmp_path: Path,
    scutil_body,
    extra_env: dict[str, str],
    exceptions: tuple[str, ...] = ("*.local",),
) -> _Run:
    """scutil_body is the stand-in script, or a callable taking the proxy's port —
    the case where that port is only known once the server is listening."""
    app = FastAPI()
    app.include_router(router)
    body = TestClient(app).get("/connector/install.sh").text
    advertised = body.split('ORIGIN="')[1].split('"')[0]

    pki = tmp_path / "pki"
    pki.mkdir()
    ca, cert, key = write_pki(pki, expires_in=datetime.timedelta(days=1))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    proxy, origin = _serve(_Tunnels), _serve(_ServesTheBinary, tls)
    try:
        if callable(scutil_body):
            scutil_body = scutil_body(proxy.server_address[1], exceptions)
        script = tmp_path / "install.sh"
        # localhost, not the IP: the certificate names it, and curl verifies it.
        script.write_text(
            body.replace(
                f'ORIGIN="{advertised}"',
                f'ORIGIN="https://localhost:{origin.server_address[1]}"',
            )
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        # A Mac, so the script takes its darwin branch.
        uname = bin_dir / "uname"
        uname.write_text('#!/bin/sh\n[ "$1" = -m ] && echo arm64 || echo Darwin\n')
        scutil = bin_dir / "scutil"
        scutil.write_text(scutil_body)
        for stub in (uname, scutil):
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

        home = tmp_path / "home"
        home.mkdir()
        # Nothing from the ambient environment may stand in for the setting
        # under test — no proxy variables, and no NO_PROXY to bypass them.
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.lower().endswith("_proxy")
        }
        env.update(
            {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "HOME": str(home),
                # What trusts the stand-in origin's certificate.
                "CURL_CA_BUNDLE": ca,
            }
        )
        env.update(extra_env)
        answered.clear()
        run = subprocess.run(
            ["sh", str(script)], env=env, capture_output=True, text=True, timeout=60
        )
        return _Run(run, proxy, origin, home)
    finally:
        proxy.shutdown()
        origin.shutdown()


def test_the_download_goes_through_the_proxy_system_settings_name(tmp_path):
    # The stand-in scutil names the port the HTTPS proxy is listening on. The
    # HTTP entry names a dead port, so an https download can only succeed
    # through the HTTPS entry — which is the entry production uses.
    loaded = _install(
        tmp_path,
        lambda port, exceptions: _scutil_reporting(
            port, exceptions, http_port=_dead_port()
        ),
        {},
    )

    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    # The https download reaches the origin through a CONNECT on the proxy. (The
    # origin sees the request either way — a tunnel relays it — so the proxy's
    # own record is what tells the two apart.)
    assert any(
        path.startswith("CONNECT ") for path in loaded.answered_by(loaded.proxy)
    ), "the proxy System Settings named was never used"


def test_a_host_in_the_bypass_list_is_not_forced_through_the_proxy(tmp_path):
    # localhost is how the origin is reached, and it is in the pane's "Bypass
    # proxy settings for these hosts" list: curl must honour that list.
    loaded = _install(
        tmp_path, _scutil_reporting, {}, exceptions=("*.local", "localhost")
    )

    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert not loaded.answered_by(loaded.proxy), (
        "a bypassed host was forced through the proxy"
    )
    assert loaded.answered_by(loaded.origin), "the bypassed host was not fetched"


@pytest.mark.parametrize(
    "variable",
    [
        # The spellings curl reads for an https URL: its own, in both cases.
        "https_proxy",
        "HTTPS_PROXY",
        # curl falls back to this one for any scheme.
        "all_proxy",
        "ALL_PROXY",
    ],
)
def test_an_environment_proxy_keeps_the_last_word(tmp_path, variable):
    # The stand-in scutil names a port nothing listens on. If the script
    # preferred it to the environment, the download would fail.
    dead = _serve(_Tunnels)
    dead_port = dead.server_address[1]
    dead.shutdown()

    live = _serve(_Tunnels)
    try:
        loaded = _install(
            tmp_path,
            _scutil_reporting(dead_port),
            {variable: f"http://127.0.0.1:{live.server_address[1]}"},
        )
        live_answers = loaded.answered_by(live)
    finally:
        live.shutdown()

    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert live_answers, "the proxy from the environment was not used"


@pytest.mark.parametrize("variable", ["no_proxy", "NO_PROXY"])
def test_a_bypass_list_alone_does_not_hide_the_system_proxy(tmp_path, variable):
    """Naming what goes direct does not name a proxy: a stray bypass list in the
    environment must not send this download past the System Settings proxy."""
    loaded = _install(
        tmp_path,
        _scutil_reporting,
        {variable: "internal.corp"},
    )

    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert any(
        path.startswith("CONNECT ") for path in loaded.answered_by(loaded.proxy)
    ), "a bypass list in the environment suppressed the System Settings proxy"


def test_an_inherited_bypass_list_is_kept(tmp_path):
    """The system's list is merged with the environment's, not substituted for
    it: localhost is in the environment's list, so the download goes direct."""
    loaded = _install(
        tmp_path,
        _scutil_reporting,
        {"no_proxy": "localhost"},
    )

    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert not loaded.answered_by(loaded.proxy), (
        "the environment's bypass list was replaced by the system's"
    )
    assert loaded.answered_by(loaded.origin), "the bypassed host was not fetched"


def test_uppercase_http_proxy_does_not_hide_the_system_proxy(tmp_path):
    """curl ignores uppercase HTTP_PROXY (the httpoxy fix), so it is not a proxy
    this download can use: the System Settings entry must still be taken."""
    # A dead port in the variable curl will not read. If the script treated it
    # as "the environment named a proxy", it would skip System Settings and the
    # download would fail.
    loaded = _install(
        tmp_path,
        _scutil_reporting,
        {"HTTP_PROXY": f"http://127.0.0.1:{_dead_port()}"},
    )

    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert any(
        path.startswith("CONNECT ") for path in loaded.answered_by(loaded.proxy)
    ), "uppercase HTTP_PROXY stood in for the System Settings proxy"


def test_an_inherited_uppercase_http_proxy_is_not_exported(tmp_path):
    """With no proxy enabled in System Settings, an uppercase HTTP_PROXY in the
    environment is not a proxy curl reads — so it must not be handed out as a
    lowercase one either. (It was, while the block used that name as its own
    working variable.)"""
    # System Settings enables nothing, so the block finds no proxy of its own.
    no_proxy_setting = (
        '#!/bin/sh\n[ "$1" = "--proxy" ] || exit 1\n'
        "echo '<dictionary> {'\n"
        "echo '  HTTPEnable : 0'\n"
        "echo '  HTTPSEnable : 0'\n"
        "echo '  SOCKSEnable : 0'\n"
        "echo '}'\n"
    )
    live = _serve(_Tunnels)
    try:
        loaded = _install(
            tmp_path,
            no_proxy_setting,
            {"HTTP_PROXY": f"http://127.0.0.1:{live.server_address[1]}"},
        )
        live_answers = loaded.answered_by(live)
    finally:
        live.shutdown()

    # The download goes direct and succeeds; the environment's uppercase value
    # never became a proxy curl uses.
    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert not live_answers, (
        "the inherited uppercase HTTP_PROXY was exported as a proxy"
    )
