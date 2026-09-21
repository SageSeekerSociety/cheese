"""Refuse a migration while host processes still use its source tree."""

import os
from pathlib import Path
import sys


def workspace_users(root: Path, proc: Path = Path("/proc")) -> list[int]:
    root = root.resolve()
    users = []
    for process in proc.iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            paths = [process / "cwd", *(process / "fd").iterdir()]
            if any(path.resolve().is_relative_to(root) for path in paths):
                users.append(int(process.name))
        except FileNotFoundError:
            # A process exiting during the scan no longer holds the source.
            continue
    return sorted(users)


if __name__ == "__main__":
    users = workspace_users(Path(sys.argv[1]))
    if users:
        raise SystemExit(
            "Legacy workspace users remain: PIDs " + ", ".join(map(str, users))
        )
