"""The processes running on this machine, as a test can see them."""

import contextlib
import os
import signal
import subprocess
from pathlib import Path


def process_list() -> list[tuple[int, str]]:
    """Every process's pid and command line.

    Read from /proc where there is one: it is the same list `ps` prints, and
    starting `ps` for it cost 13 ms per test on a hosted runner, for every
    test that has a `tmp_path`. A process that exits mid-read is skipped.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        # -ww: a long tmp path is otherwise cut at the terminal width.
        listing = subprocess.run(
            ["ps", "-A", "-ww", "-o", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
        rows = []
        for line in listing.stdout.splitlines():
            pid, _, args = line.strip().partition(" ")
            rows.append((int(pid), args))
        return rows
    rows = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        rows.append(
            (int(entry.name), raw.replace(b"\0", b" ").decode(errors="replace"))
        )
    return rows


def end_processes_under(directory: Path) -> None:
    """Kill every process whose command line names `directory`.

    For what a pinned build leaves behind when it exits: its session hook
    (`cheese sync-agents`, through the shell prefix) reaches for a machine a
    test has none of, and the prefix waits a minute for one and its stop
    watcher ten more, each in a session of its own, so nothing the build's
    process group gets reaches them.
    """
    for pid, args in process_list():
        if str(directory) in args:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
