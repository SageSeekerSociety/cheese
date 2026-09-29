"""The suite, run from inside one of our own rooms, stays off that room's platform.

An agent working on this repository runs its tests on the machine the room was
launched on, so the process that starts pytest carries the room's session: the
live deployment's ``CHEESE_API`` and a real token. The launcher tests start the
real machine launcher from the environment they were given, and a launcher that
sees a platform installs from it — the whole document toolchain and the harness
binaries, into a throwaway temporary home, detached so the download outlives the
test. Every run of the suite in a room pulled several hundred megabytes from
the deployment serving that room.
"""

import http.server
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
# A launcher test that builds its screen from the surrounding environment.
LAUNCHER_TEST = (
    "tests/unit/test_machine_launcher.py"
    "::test_the_platform_cli_is_on_path_for_whatever_runs"
)


def test_a_launcher_test_run_inside_a_room_fetches_nothing_from_its_platform(
    tmp_path,
):
    seen: list[str] = []

    class Platform(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    platform = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Platform)
    threading.Thread(target=platform.serve_forever, daemon=True).start()
    try:
        room = {
            **os.environ,
            "CHEESE_API": f"http://127.0.0.1:{platform.server_port}",
            "CHEESE_TOKEN": "room-token",
            "CHEESE_HOME": "$HOME/.cheese/home/room",
            "CHEESE_STORE": "$HOME/.cheese/store/project",
        }
        room.pop("PYTEST_XDIST_WORKER", None)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(tmp_path / "inner"),
                LAUNCHER_TEST,
            ],
            cwd=BACKEND,
            env=room,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
        # The install is detached from the launcher; give a fetch it started the
        # moment it needs to reach the platform.
        time.sleep(2)
    finally:
        platform.shutdown()
        platform.server_close()

    assert seen == [], f"the suite reached the room's platform: {seen}"
