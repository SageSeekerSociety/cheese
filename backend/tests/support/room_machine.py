"""A room's machine for a pi runner under test: a real executor over a checkout.

pi's tools reach the room's machine through the executor every harness uses
(`remote_execution/runtime.py`); here one runs locally over a directory of its
own, and a runner is given it as its execution target, reached the way a room
reaches it when the machine is local to the client (its `request` command).
"""

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from app.domain.agent.executor_transport import DEFERRED_WORKSPACE

RUNTIME = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution/runtime.py"
)

#: A session that has not needed its machine yet: it sees the placeholder, and
#: nothing it does takes one.
NO_MACHINE = {"kind": "deferred", "workspace": DEFERRED_WORKSPACE, "mcp_servers": []}


@contextmanager
def room_machine(
    root: Path,
    *,
    env: dict[str, str] | None = None,
    checkout: Path | None = None,
    mcp_servers: dict[str, dict] | None = None,
    home: Path | None = None,
    claude: str | None = None,
):
    """A running executor over `checkout` (`root / "room"` unless named), with
    the checkout's stdio MCP servers as the machine found them; the execution
    target for it.

    `home` is the machine's user's home, whose shell profile commands start
    from; `claude` the build the executor takes that profile's snapshot with."""
    checkout = checkout or root / "room"
    checkout.mkdir(parents=True, exist_ok=True)
    state = root / "executor"
    subprocess.run(
        [sys.executable, str(RUNTIME), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(checkout),
                "env": env or {},
                "mcp_servers": mcp_servers or {},
                **({"claude": claude} if claude else {}),
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
        env=(
            {**os.environ, "HOME": str(home), "SHELL": "/bin/bash"}
            if home is not None
            else None
        ),
    )
    try:
        yield {
            "command": [sys.executable, str(RUNTIME)],
            "state": str(state),
            "workspace": str(checkout),
            "mcp_servers": sorted(mcp_servers or {}),
        }
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=30,
        )
