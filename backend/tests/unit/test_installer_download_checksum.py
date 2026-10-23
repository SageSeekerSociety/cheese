"""The installer checks a download against the digest the server announces.

`/connector/latest/<target>/cheesehost` serves the bytes and, in the same
response, `X-Checksum-SHA256` naming them. Nothing but those bytes may end up
on PATH: a transfer a relay truncated, or anything that substituted one, is
exactly what the header is there to catch. A response carrying no digest at all
is a failure too — treated as one, a proxy that strips the header cannot turn
the check off silently.

The served script is run for real, with real curl, against a local server.
"""

import gzip
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

    Class attributes because the handler is built by the server: ``announced``
    is None for a response carrying no digest header at all, and ``asked``
    records whether the request accepted the packed copy, so a test can see
    which copy the installer asked for rather than infer it."""

    announced: str | None = DIGEST
    asked: list[bool] = []

    def do_GET(self):
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        type(self).asked.append(accepts_gzip)
        if accepts_gzip:
            body = gzip.compress(BINARY)
            encoding = "gzip"
        else:
            body = BINARY
            encoding = ""
        announced = type(self).announced
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        if announced:
            self.send_header("X-Checksum-SHA256", announced)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _install(tmp_path, announced: str | None, without=()):
    """Run the served installer against a server announcing ``announced``.

    ``without`` names tools to keep off the script's PATH, so a machine that
    lacks one can be exercised."""
    _ServesBinary.announced = announced
    _ServesBinary.asked = []
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
        # A copy of the tools on PATH with the named ones left out: a shim of
        # every entry that either is not the tool or is not a tool of its own
        # (a directory, a broken link) still lets the others be found.
        path = f"{bin_dir}:{os.environ['PATH']}"
        if without:
            shims = tmp_path / "without"
            shims.mkdir()
            for entry in os.environ["PATH"].split(os.pathsep):
                if not os.path.isdir(entry):
                    continue
                for name in os.listdir(entry):
                    if name in without or (shims / name).exists():
                        continue
                    source = os.path.join(entry, name)
                    if os.path.isfile(source):
                        (shims / name).symlink_to(source)
            path = f"{bin_dir}:{shims}"
        home = tmp_path / "home"
        home.mkdir()
        env = {
            "PATH": path,
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


def test_the_packed_copy_is_asked_for_and_unpacked_before_the_install(tmp_path):
    """On a slow link the packed copy is what fits; the machine still runs the
    bytes the digest names."""
    run, home = _install(tmp_path, DIGEST)
    assert run.returncode == 0, run.stdout + run.stderr
    assert (home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert _ServesBinary.asked == [True]


def test_a_machine_without_gzip_installs_the_plain_copy(tmp_path):
    """gzip is what unpacks the packed copy, so a machine without it must not
    be handed one: it is served the plain copy and installs that."""
    run, home = _install(tmp_path, DIGEST, without=("gzip",))
    assert run.returncode == 0, run.stdout + run.stderr
    assert (home / ".local/bin/cheesehost").read_bytes() == BINARY
    assert _ServesBinary.asked == [False]
