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

# A run of the suite with one of its launcher tests, started the way a room
# starts one: a fresh interpreter whose environment is the room's. It imports
# the suite's own conftest first — that module's scope is what strips the room
# out of the environment, and a run of the suite reaches it before any test —
# and then calls the launcher test the suite would run. Calling it rather than
# paying pytest to collect it keeps the whole check at the conftest import plus
# one launcher, instead of conftest plus a second pytest startup.
#
# The test's temporary home is this process's argument; it must match what the
# suite's own ``tmp_path`` hands the launcher tests.
IN_A_ROOM = """
import os
import pathlib
import sys

sys.path.insert(0, os.getcwd())
import tests.conftest  # noqa: F401

from app.domain.agent import machine_launcher
from tests.unit import test_machine_launcher

home = pathlib.Path(sys.argv[1])

# A launched session keeps its temporary files on the host, so the suite's own
# ``_session_tmp_per_test`` fixture points each test's session at a directory of
# that test's own. No fixture runs here, so the same redirection is made by
# hand: without it this check would leave a session directory behind in
# ``/var/tmp`` on every machine that ran the suite.
machine_launcher.SESSION_TMP = str(home.parent / "var-tmp")

test_machine_launcher.test_the_platform_cli_is_on_path_for_whatever_runs(home)
"""


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
        suite = subprocess.Popen(
            [sys.executable, "-c", IN_A_ROOM, str(tmp_path / "home")],
            cwd=BACKEND,
            env=room,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # A session of its own, so the handle below names everything that
            # run started and nothing else on this machine.
            start_new_session=True,
        )
        out, err = suite.communicate(timeout=300)
        assert suite.returncode == 0, out[-3000:] + err[-3000:]
        _wait_for_the_group_to_empty(suite.pid)
    finally:
        platform.shutdown()
        platform.server_close()

    assert seen == [], f"the suite reached the room's platform: {seen}"


def _wait_for_the_group_to_empty(pgid: int) -> None:
    """Block until nothing the run started is left in its process group.

    The install the launcher starts is detached, because it belongs to the
    machine rather than to the session that first needed it, so it outlives the
    run that triggered it: a fetch it began reaches the platform after that run
    has already answered. Waiting for the group to empty gives it exactly as
    long as it needs, and costs nothing when the run started nothing — the only
    outcome a suite that stays off its room's platform can have.
    """
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
