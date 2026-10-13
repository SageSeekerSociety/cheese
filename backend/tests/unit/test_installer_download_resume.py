"""install.sh survives a download cut mid-transfer.

The connector binary is tens of MB and reaches users through a relay that
drops connections; a cut used to fail the whole install (curl 56). The served
script is run for real, with real curl, against a server that closes the first
transfer halfway and honours Range afterwards.
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
# The digest the response announces for BINARY: install.sh checks the file
# against it, so this stands in for what the route's header carries — on the
# resumed 206 too, where the real route repeats it.
DIGEST = hashlib.sha256(BINARY).hexdigest()


class _CutsFirstTransfer(http.server.BaseHTTPRequestHandler):
    requests: list[str | None] = []

    def do_GET(self):  # noqa: N802
        byte_range = self.headers.get("Range")
        type(self).requests.append(byte_range)
        if byte_range is None:
            # Promise the whole file, send half, drop the connection.
            self.send_response(200)
            self.send_header("Content-Length", str(len(BINARY)))
            self.send_header("X-Checksum-SHA256", DIGEST)
            self.end_headers()
            self.wfile.write(BINARY[: len(BINARY) // 2])
            self.wfile.flush()
            self.connection.shutdown(2)
            self.close_connection = True
            return
        start = int(byte_range.removeprefix("bytes=").split("-")[0])
        self.send_response(206)
        self.send_header(
            "Content-Range", f"bytes {start}-{len(BINARY) - 1}/{len(BINARY)}"
        )
        self.send_header("Content-Length", str(len(BINARY) - start))
        self.send_header("X-Checksum-SHA256", DIGEST)
        self.end_headers()
        self.wfile.write(BINARY[start:])

    def log_message(self, *args):
        pass


def test_install_script_resumes_a_download_cut_midway(tmp_path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    _CutsFirstTransfer.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CutsFirstTransfer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    local = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        body = client.get("/connector/install.sh").text
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

    assert run.returncode == 0, run.stdout + run.stderr
    installed = home / ".local/bin/cheesehost"
    assert installed.read_bytes() == BINARY
    assert os.access(installed, os.X_OK)
    assert not (home / ".local/bin/cheesehost.part").exists()
    # The second request picked up where the cut left off, not from zero.
    assert _CutsFirstTransfer.requests[0] is None
    assert _CutsFirstTransfer.requests[1] == f"bytes={len(BINARY) // 2}-"
