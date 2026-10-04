"""The platform CLI starts on a Mac whose only python3 is Apple's 3.9.

A Mac connected as a device without Homebrew has the command line tools'
python3, which is 3.9, and that is what its executor and the CLI worker beside
it run on. The CLI used to import a name that only exists from 3.11, so the
worker died at startup and the room's teammate lost its files and commands
on a machine that showed as online.

These run the shipped files under a real 3.9 (uv fetches one when the machine
has none), the way the device does: `cheese --help`, and the worker that
preloads the CLI reporting ready.
"""

import hashlib
import select
import shutil
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
CHEESE = BACKEND / "sandbox/cheese"
WORKER = BACKEND / "app/domain/agent/cli_worker.py"


def python39(*args: str) -> list[str]:
    uv = shutil.which("uv")
    # A failure, not a skip: a missing interpreter is how this check would
    # quietly stop running.
    assert uv, "uv is needed to fetch the Python 3.9 this test runs on"
    return [uv, "run", "--no-project", "--python", "3.9", "python", *args]


def test_cheese_help_runs_on_python_39():
    done = subprocess.run(
        python39(str(CHEESE), "--help"), capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, done.stderr
    assert "cheese" in done.stdout


def test_cli_worker_preloads_the_cli_on_python_39(tmp_path):
    # Fetch the interpreter first, so the wait below is the worker's own.
    fetched = subprocess.run(python39("-c", "pass"), capture_output=True, timeout=300)
    assert fetched.returncode == 0, fetched.stderr
    # macOS limits Unix socket paths to 104 bytes; pytest's temp root is longer.
    address = (
        "/tmp/cheese-cli-39-" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:24]
    )
    worker = subprocess.Popen(
        python39(str(WORKER), address, str(CHEESE)),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert worker.stdout is not None and worker.stdin is not None
    ready = select.select([worker.stdout], [], [], 30)[0]
    line = worker.stdout.readline().strip() if ready else ""
    worker.stdin.close()
    try:
        worker.wait(timeout=10)
    finally:
        if worker.poll() is None:
            worker.kill()
    assert worker.stderr is not None
    errors = worker.stderr.read()
    assert line == "ready", errors
    assert worker.returncode == 0, errors
