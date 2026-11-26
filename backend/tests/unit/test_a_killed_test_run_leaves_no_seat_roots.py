"""A test process killed before its exit handlers run (a timeout's SIGTERM,
Ctrl-C, SIGKILL) leaves no seat-channel root behind once the next test process
makes a channel, and a root whose process is still running is never taken from
under it: a test standing in for a restarted backend hands its root to the next
process while it waits on it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

# Test processes in a fresh TMPDIR. Each forked one makes a seat channel, says
# its root and waits; it is killed with the signals named in argv (none: left
# running). Then this process makes a channel of its own, as the next test
# process does, and says which of those roots are still there.
PROCESSES = """
import json, os, signal, sys, time
from pathlib import Path
from tests.support.seat_channel import SeatChannel

roots = []
for name in json.loads(sys.argv[1]) or [None]:
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read)
        os.write(write, str(SeatChannel().root).encode())
        os.close(write)
        time.sleep(600)
    os.close(write)
    root = Path(os.read(read, 4096).decode())
    os.close(read)
    assert root.is_dir()
    if name:
        os.kill(pid, getattr(signal, name))
        os.waitpid(pid, 0)
    roots.append((name, pid, root))
SeatChannel()
print(json.dumps({str(root): root.exists() for _name, _pid, root in roots}))
for name, pid, _root in roots:
    if not name:
        os.kill(pid, signal.SIGKILL)
"""


def _left(tmp_path: Path, *signals: str) -> dict[str, bool]:
    done = subprocess.run(
        [sys.executable, "-c", PROCESSES, json.dumps(signals)],
        cwd=BACKEND,
        env={**os.environ, "PYTHONPATH": ".", "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


def test_the_next_run_removes_a_killed_runs_root(tmp_path):
    left = _left(tmp_path, "SIGTERM", "SIGKILL")

    assert left and not any(left.values()), left


def test_a_running_process_keeps_its_root(tmp_path):
    left = _left(tmp_path)

    assert left and all(left.values()), left
