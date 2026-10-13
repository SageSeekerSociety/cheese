"""What install.sh leaves behind is enough to connect.

The page tells a person to run the installer, then `cheesehost link connect`.
That second command only works when the machine knows which server it came
from and the shell can find the binary: on a fresh macOS, ~/.local/bin is not
on PATH. The served script is run for real against a local server.
"""

import hashlib
import http.server
import json
import os
import stat
import subprocess
import threading

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.routes.installer import router


class _ServesBinary(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'#!/bin/sh\necho cheesehost "$@"\n'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        # install.sh checks the file against the digest the response announces.
        self.send_header("X-Checksum-SHA256", hashlib.sha256(body).hexdigest())
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _install(tmp_path, home, *, path_has_bin: bool):
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
        tools = tmp_path / "tools"
        tools.mkdir(exist_ok=True)
        uname = tools / "uname"
        uname.write_text('#!/bin/sh\n[ "$1" = -m ] && echo x86_64 || echo Linux\n')
        uname.chmod(uname.stat().st_mode | stat.S_IEXEC)
        path = f"{tools}:{os.environ['PATH']}"
        if path_has_bin:
            path = f"{home / '.local' / 'bin'}:{path}"
        run = subprocess.run(
            ["/bin/sh", str(script)],
            env={"PATH": path, "HOME": str(home), "NO_PROXY": "*", "no_proxy": "*"},
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        server.shutdown()
    assert run.returncode == 0, run.stdout + run.stderr
    config = home / ".config" / "cheese" / "config.json"
    return run.stdout, local, json.loads(config.read_text())


def _next_command(stdout: str) -> list[str]:
    line = next(x for x in stdout.splitlines() if x.startswith("next: "))
    return line.removeprefix("next: ").split("   ")[0].split()


def test_a_fresh_machine_knows_its_server_and_the_printed_command_runs(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stdout, server, config = _install(tmp_path, home, path_has_bin=False)

    assert config["base"] == f"{server}/connector"
    command = _next_command(stdout)
    ran = subprocess.run(command, capture_output=True, text=True, env={})
    assert ran.returncode == 0 and "link connect" in ran.stdout


def test_a_machine_already_tied_to_a_server_stays_tied_to_it(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "cheese").mkdir(parents=True)
    kept = {"base": "https://elsewhere.test/connector", "token": "t"}
    (home / ".config" / "cheese" / "config.json").write_text(json.dumps(kept))

    stdout, server, config = _install(tmp_path, home, path_has_bin=True)

    assert config["base"] == kept["base"] and config["token"] == "t"
    assert _next_command(stdout)[-1] == f"{server}/connector"


def test_the_printed_command_is_short_when_the_shell_already_finds_it(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    stdout, _server, _config = _install(tmp_path, home, path_has_bin=True)
    assert _next_command(stdout)[0] == "cheesehost"
