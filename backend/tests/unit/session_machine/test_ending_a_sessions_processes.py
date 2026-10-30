"""Ending what a session started ends it, on Linux, macOS and Windows alike.

The runner ends a session's processes when it lets the session go
(``Runner.release``) and when it can no longer read the session's output
(``Runner._read``). A process that will not go when asked is ended anyway: a
session left running there goes on holding the inputs and work the runner has
already reported over. Windows has no SIGKILL to fall back on, which is why this
runs in the ``windows`` job of `.github/workflows/remote-execution.yml` too.
"""

import subprocess
import sys
from pathlib import Path

# As in the runner's archive (`driven/bundle.py`), where portable.py, which
# processes.py imports on Windows, sits at the root.
if sys.platform == "win32":
    sys.path.insert(
        0,
        str(
            Path(__file__).resolve().parents[3]
            / "app/domain/agent/harness/claude_code/remote_execution"
        ),
    )

from app.domain.agent.harness.claude_code.processes import end

STUBBORN = """
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("ready", flush=True)
time.sleep(600)
"""


def test_a_process_that_will_not_go_when_asked_is_ended():
    process = subprocess.Popen(
        [sys.executable, "-c", STUBBORN], stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        end([process.pid])
        assert process.wait(timeout=10) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
