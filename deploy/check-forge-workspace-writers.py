"""Refuse a migration while host processes still use its source tree."""

import os
from pathlib import Path
import sys


def workspace_users(root: Path, proc: Path = Path("/proc")) -> list[int]:
    root = root.resolve()
    if not root.is_dir():
        # An unmounted or mistyped root has no users to find, which must not
        # read as "nobody is writing": the migration would start on a live tree.
        raise FileNotFoundError(root)
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
    try:
        users = workspace_users(Path(sys.argv[1]))
    except FileNotFoundError as error:
        raise SystemExit(f"cannot check workspace users: {error}")
    if users:
        raise SystemExit(
            "Legacy workspace users remain: PIDs " + ", ".join(map(str, users))
        )
