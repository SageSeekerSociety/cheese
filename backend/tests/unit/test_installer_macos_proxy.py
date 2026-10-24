"""install.sh downloads the connector through the Mac's own proxy setting.

macOS keeps the proxy in System Settings and curl does not read it, so a Mac on
a campus network fetched the connector straight and slowly, or not at all. The
served script is run for real, with real curl, against a stand-in `scutil` that
reports a proxy and a separate server standing in for that proxy.
"""

import hashlib
import http.server
import os
import stat
import subprocess
import threading

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.routes.installer import router

BINARY = bytes(range(256)) * 4096  # 1 MiB, every byte position distinguishable
DIGEST = hashlib.sha256(BINARY).hexdigest()

# Which servers answered a request, by port, so a test can tell the platform
# from the proxy.
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


def _serve() -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ServesTheBinary)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _scutil_reporting(port: int) -> str:
    return f"""#!/bin/sh
[ "$1" = "--proxy" ] || exit 1
cat <<'EOF'
<dictionary> {{
  ExceptionsList : <array> {{
    0 : *.local
    1 : 169.254/16
  }}
  ExcludeSimpleHostnames : 0
  HTTPEnable : 1
  HTTPPort : {port}
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : {port}
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


def _install(tmp_path, scutil_body, extra_env: dict[str, str]) -> _Run:
    """scutil_body is the stand-in script, or a callable taking its own port —
    the one case where the port is only known once the server is listening."""
    app = FastAPI()
    app.include_router(router)
    body = TestClient(app).get("/connector/install.sh").text
    advertised = body.split('ORIGIN="')[1].split('"')[0]

    proxy, origin = _serve(), _serve()
    try:
        if callable(scutil_body):
            scutil_body = scutil_body(proxy.server_address[1])
        script = tmp_path / "install.sh"
        script.write_text(
            body.replace(
                f'ORIGIN="{advertised}"',
                f'ORIGIN="http://127.0.0.1:{origin.server_address[1]}"',
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
        env.update({"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home)})
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
    # The stand-in scutil names the port the proxy is actually listening on.
    loaded = _install(tmp_path, _scutil_reporting, {})

    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    # curl names the request in absolute form to the proxy; the platform itself
    # must not have been asked at all.
    assert loaded.answered_by(loaded.proxy), "the proxy System Settings named was never used"
    assert not loaded.answered_by(loaded.origin), "the platform was asked directly"


def test_an_environment_proxy_keeps_the_last_word(tmp_path):
    # The stand-in scutil names a port nothing listens on. If the script
    # preferred it to the environment, the download would fail.
    dead = _serve()
    dead_port = dead.server_address[1]
    dead.shutdown()

    live = _serve()
    try:
        # Lowercase: curl refuses uppercase HTTP_PROXY (the httpoxy fix), so
        # that is the spelling an environment-configured proxy arrives in.
        loaded = _install(
            tmp_path,
            _scutil_reporting(dead_port),
            {"http_proxy": f"http://127.0.0.1:{live.server_address[1]}"},
        )
        live_answers = loaded.answered_by(live)
    finally:
        live.shutdown()

    assert loaded.run.returncode == 0, loaded.run.stdout + loaded.run.stderr
    assert (loaded.home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert live_answers, "the proxy from the environment was not used"
