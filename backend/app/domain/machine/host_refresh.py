"""Bring a pool host up to date where it is, instead of rebuilding it.

A host's image version has two halves, the machine and its enrollment script
(`enrollment.host_image_version`). When only the script changed, a new machine
buys nothing the script cannot do on the old one, and costs everything the old
one had built up: its sandbox homes, every project's package store and copy of
the repository, the project environments set up there, and its parked
sandboxes. On dev on 2026-11-13 one change to the script rebuilt all ten hosts
and threw away 59 parked sandboxes; preparing a sandbox went from 3.6 s to
21.9 s at p95, opening a task's checkout from 40 s to 48 s, until each host had
built it all up again.

So such a host is drained the same way (no new sandbox, its frozen ones parked
rather than destroyed), and once nothing but parked sandboxes is on it the
script runs again on it, through its connector — the machine keeps its identity
(`bootstrap_script(identity=False)`). The script restarts the connector, so it
runs as a transient unit of its own (`systemd-run --user`), outside the
connector's, and leaves its exit status in a file the sweep reads back. A run
that fails, or does not finish within ``REFRESH_WAIT``, falls back to the
rebuild it replaced.
"""

from __future__ import annotations

import base64
import logging
import shlex
from datetime import datetime, timedelta
from pathlib import Path

from app.domain.agent import sandbox_image
from app.domain.agent.place import footprint_root
from app.domain.machine import enrollment

logger = logging.getLogger(__name__)

#: How long a run may take before the host is rebuilt instead. The script
#: may download the pinned Claude Code and the sandbox root filesystem over
#: HTTP, which a new machine is given over SSH instead.
REFRESH_WAIT = timedelta(minutes=40)

#: Where on the host a run keeps its script, its output and its exit status.
REFRESH_DIR = "$HOME/.cheese/refresh"

_HELPERS = (
    (Path(sandbox_image.__file__), "sandbox-image.py"),
    (
        Path(sandbox_image.__file__).parent
        / "harness/claude_code/remote_execution/sandbox_host.py",
        "sandbox-host.py",
    ),
)


def refresh_script() -> str:
    """What a host brought up to date runs: the platform's helper programs a
    new machine is given over SSH, then the enrollment script with the
    machine's own config kept."""
    runtime = f"$HOME/{footprint_root()}/runtime"
    drops = [f'mkdir -p "{runtime}"']
    for source, name in _HELPERS:
        encoded = base64.b64encode(source.read_bytes()).decode()
        drops.append(
            f"base64 -d > \"{runtime}/{name}.refresh-new\" <<'CHEESE_REFRESH_EOF'\n"
            f"{encoded}\nCHEESE_REFRESH_EOF\n"
            f'chmod +x "{runtime}/{name}.refresh-new"\n'
            f'mv "{runtime}/{name}.refresh-new" "{runtime}/{name}"'
        )
    script = enrollment.bootstrap_script(
        origin="",
        token="",
        device_id="",
        sandbox_disk=True,
        identity=False,
    )
    return "set -eu\n" + "\n".join(drops) + "\n" + script


#: Started through the connector, with the script on stdin: kept on the host,
#: then run as a unit of its own so the connector's restart does not end it.
START = (
    "set -eu\n"
    f'd="{REFRESH_DIR}"\n'
    'mkdir -p "$d"\n'
    'rm -f "$d/status"\n'
    'cat > "$d/refresh.sh"\n'
    "systemd-run --user --collect --quiet "
    '--unit="cheese-refresh-$(date +%s)" '
    "/bin/bash -lc "
    + shlex.quote(
        f'bash "{REFRESH_DIR}/refresh.sh" > "{REFRESH_DIR}/output" 2>&1; '
        f'echo $? > "{REFRESH_DIR}/status"'
    )
    + "\n"
)

#: Read back by the sweep: the exit status once the run is over, else
#: ``running``; then the end of what it wrote.
POLL = (
    f'd="{REFRESH_DIR}"\n'
    'if [ -f "$d/status" ]; then cat "$d/status"; else echo running; fi\n'
    'tail -c 1500 "$d/output" 2>/dev/null || true\n'
)


async def start(hub, device_id: str) -> None:
    """Start the run on ``device_id``. Raises when the host does not take it."""
    answer = await hub.exec(
        device_id, ["bash", "-c", START], stdin=refresh_script(), timeout=60
    )
    if answer.get("exit") != 0:
        raise RuntimeError(
            "refresh did not start: " + str(answer.get("stderr") or "")[-500:]
        )


async def poll(hub, device_id: str) -> tuple[str, str]:
    """``("running", "")`` while the run goes on, ``("ok", "")`` once it
    succeeded, ``("failed", what it wrote)`` once it failed. A host whose
    connector is restarting answers nothing: it is still running."""
    try:
        answer = await hub.exec(device_id, ["bash", "-c", POLL], timeout=30)
    except Exception:  # the connector restarting, the link not back yet
        return "running", ""
    lines = str(answer.get("stdout") or "").splitlines()
    first = lines[0].strip() if lines else "running"
    if first == "running":
        return "running", ""
    if first == "0":
        return "ok", ""
    return "failed", "\n".join(lines[1:])[-1500:]


def overdue(started: datetime, now: datetime) -> bool:
    return now - started >= REFRESH_WAIT
