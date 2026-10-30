"""The processes a Claude Code session started, and ending them.

What the runner stops when it lets a session go (``Runner.release``) or ends one
it can no longer read: the session's own process and everything under it.

Standard library only: this module travels in the runner archive.
"""

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    # Shipped at the archive's root (`driven/bundle.py`).
    import portable


def end(pids: list[int]) -> None:
    """End these processes: asked first and, where they do not go, killed.

    Windows has no request a console-less process can answer, nor a SIGKILL,
    so there each one is ended outright.
    """
    if sys.platform == "win32":
        portable.terminate(pids)
        return
    for number, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 1.0)):
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, number)
        deadline = time.monotonic() + wait
        while pids and time.monotonic() < deadline:
            time.sleep(0.1)
            pids = [pid for pid in pids if _alive(pid)]
        if not pids:
            return


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def started_by(session: int) -> list[int]:
    """What a session started and could still be running: its descendants.

    A session's work is under it — the shell it started a command in, what that
    shell runs, and the helpers it started for itself — so its descendants are
    what has to be stopped for it to be let go of work it would otherwise wait
    out (``Runner.release``). Its own process is not in the list: it is the one
    being ended, and it should be the one to exit.
    """
    if sys.platform == "win32":
        return [
            pid
            for pid in portable.tree([(session, portable.created(session))])
            if pid != session
        ]
    children: dict[int, list[int]] = {}
    for pid, parent in _processes():
        children.setdefault(parent, []).append(pid)
    found: list[int] = []
    queue = [session]
    while queue:
        for child in children.get(queue.pop(), []):
            found.append(child)
            queue.append(child)
    return found


def _processes() -> list[tuple[int, int]]:
    """Every process on this machine as (pid, parent), from /proc where there is
    one and from ``ps`` where there is not."""
    proc = Path("/proc")
    if proc.is_dir():
        pairs = []
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                # Past the name, which may itself hold a ')'.
                _, parent = (entry / "stat").read_bytes().split(b")")[-1].split()[:2]
            except (OSError, ValueError):
                continue
            pairs.append((int(entry.name), int(parent)))
        return pairs
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pairs = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            pairs.append((int(fields[0]), int(fields[1])))
    return pairs
