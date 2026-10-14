"""The processes running on this machine, as a test can see them."""

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
