"""The installer checks a download against the digest the server announces.

`/connector/latest/<target>/cheesehost` serves the bytes and, in the same
response, `X-Checksum-SHA256` naming them. Nothing but those bytes may end up
on PATH: a transfer a relay truncated, or anything that substituted one, is
exactly what the header is there to catch. A response carrying no digest at all
is a failure too — treated as one, a proxy that strips the header cannot turn
the check off silently.

The served script is run for real, with real curl, against a local server.
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


class _ServesBinary(http.server.BaseHTTPRequestHandler):
    """Serves BINARY, announcing ``announced`` as its sha256.

    ``announced`` is a class attribute because the handler is built by the
    server; None means the response carries no digest header at all."""

    announced: str | None = DIGEST

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(BINARY)))
        if type(self).announced:
            self.send_header("X-Checksum-SHA256", type(self).announced)
        self.end_headers()
        self.wfile.write(BINARY)

    def log_message(self, *args):
        pass


def _install(tmp_path, announced: str | None):
    """Run the served installer against a server announcing ``announced``."""
    _ServesBinary.announced = announced
    app = FastAPI()
    app.include_router(router)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ServesBinary)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    local = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        body = TestClient(app).get("/connector/install.sh").text
        origin = body.split('ORIGIN="')[1].split('"')[0]
        script = tmp_path / "install.sh"
        script.write_text(body.replace(f'ORIGIN="{origin}"', f'ORIGIN="{local}"'))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        uname = bin_dir / "uname"
        uname.write_text('#!/bin/sh\n[ "$1" = -m ] && echo x86_64 || echo Linux\n')
        uname.chmod(uname.stat().st_mode | stat.S_IEXEC)
        home = tmp_path / "home"
        home.mkdir()
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(home),
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
        run = subprocess.run(
            ["sh", str(script)], env=env, capture_output=True, text=True, timeout=60
        )
    finally:
        server.shutdown()
    return run, home


def test_a_download_that_does_not_match_the_announced_digest_is_not_installed(
    tmp_path,
):
    run, home = _install(tmp_path, "0" * 64)
    assert run.returncode != 0
    assert not (home / ".local/bin/cheesehost").exists()
    assert not (home / ".local/bin/cheesehost.part").exists()


def test_a_response_carrying_no_digest_is_not_installed_either(tmp_path):
    run, home = _install(tmp_path, None)
    assert run.returncode != 0
    assert not (home / ".local/bin/cheesehost").exists()
    assert not (home / ".local/bin/cheesehost.part").exists()


def test_a_download_matching_the_announced_digest_is_installed(tmp_path):
    run, home = _install(tmp_path, DIGEST)
    assert run.returncode == 0, run.stdout + run.stderr
    installed = home / ".local/bin/cheesehost"
    assert installed.read_bytes() == BINARY
    assert os.access(installed, os.X_OK)
