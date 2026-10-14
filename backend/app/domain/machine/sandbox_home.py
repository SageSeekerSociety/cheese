"""What the platform does to one session's home on a cloud host.

Piped to ``python3 -`` on the host (``lifecycle.program``) after the source of
``agent/resource_cleanup.py``, whose helpers arrive as ``cleanup``: this file
runs there with nothing of ours importable, like that one.

- ``destroy``: end the session's sandbox and delete its home. Its executor is
  asked to stop, then whatever still holds the home — a dev server, a
  background command — is ended, and the home, the legacy work directory and
  the sandbox's temporary directory are removed. A home already gone is not
  an error.
- ``disk``: how full the filesystem the homes are on is, in bytes and in
  inodes, and which sandbox homes on this machine are not among ``live`` and
  were last written before ``grace_s`` — homes whose sandbox no longer exists
  (``disk_guard``). With ``walk``, also what each home holds (its /tmp is
  inside it) and the directory that holds most of it: a walk of every file,
  which is why the guard asks for it far less often than for the rest.

Answers one line of JSON.
"""

import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

#: How long one ``disk`` reading may walk the homes. A walk cut short says so
#: (``partial``); what it counted is still a floor of what is there.
WALK_S = 60
#: How deep the directory a home's usage is reported under goes.
TOP_DEPTH = 4


def destroy(cleanup, project, resource) -> dict:
    home, work = cleanup["resource_paths"](Path.home(), project, resource)
    if home.exists():
        cleanup["stop_executor"](home, resource)
    cleanup["end_holders"]([home, work])
    cleanup["check_no_writers"]([home, work])
    for path in (work, home, cleanup["resource_tmp"](resource)):
        if path.exists():
            cleanup["remove_tree"](path)
    # Last, as the room's cleanup does: until the home is gone, it is the
    # sandbox's to have written.
    cleanup["sandbox_marker"](home).unlink(missing_ok=True)
    return {"destroyed": True}


def filesystem(path: Path) -> dict:
    """Total and free bytes and inodes of the filesystem ``path`` is on. A
    system that reports no inodes (0 total) has no such limit to keep."""
    if not hasattr(os, "statvfs"):
        total, _, free = shutil.disk_usage(path)
        return {"bytes": total, "bytes_free": free, "inodes": 0}
    fs = os.statvfs(path)
    return {
        "bytes": fs.f_blocks * fs.f_frsize,
        "bytes_free": fs.f_bavail * fs.f_frsize,
        "inodes": fs.f_files,
        "inodes_free": fs.f_favail,
    }


def usage(root: Path, deadline: float) -> dict:
    """Bytes and inodes under ``root``, never following a link or leaving its
    filesystem, and the directory holding most of them: the deepest one, down
    to ``TOP_DEPTH``, that holds over half of its parent's inodes or bytes."""
    try:
        device = root.lstat().st_dev
    except OSError:
        return {"bytes": 0, "inodes": 0, "top": "", "partial": False}
    # Per directory up to TOP_DEPTH below root: [bytes, inodes] beneath it.
    beneath: dict[tuple[str, ...], list[int]] = {(): [0, 0]}
    stack: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    partial = False
    while stack:
        if time.monotonic() > deadline:
            partial = True
            break
        directory, parts = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            size = getattr(info, "st_blocks", 0) * 512 or info.st_size
            here = parts + (entry.name,) if len(parts) < TOP_DEPTH else parts
            for depth in range(len(here) + 1):
                totals = beneath.setdefault(here[:depth], [0, 0])
                totals[0] += size
                totals[1] += 1
            if stat.S_ISDIR(info.st_mode) and info.st_dev == device:
                stack.append((Path(entry.path), here))
    total = beneath[()]
    top: tuple[str, ...] = ()
    while True:
        children = [
            (path, totals)
            for path, totals in beneath.items()
            if len(path) == len(top) + 1 and path[: len(top)] == top
        ]
        parent = beneath[top]
        heavy = [
            (path, totals)
            for path, totals in children
            if totals[1] * 2 > parent[1] or totals[0] * 2 > parent[0]
        ]
        if not heavy:
            break
        top = max(heavy, key=lambda item: (item[1][1], item[1][0]))[0]
    return {
        "bytes": total[0],
        "inodes": total[1],
        "top": "/".join(top),
        "partial": partial,
    }


def disk(cleanup, live, grace_s, walk=True) -> dict:
    """What the homes on this machine hold, and the sandbox homes left behind.

    ``live`` is every (project, resource) of a sandbox the platform has on
    this machine. A sandbox home is one with the platform's sandbox marker
    (``cleanup["sandbox_marker"]``); a home without one — a room's own
    directory on a computer that runs it without a sandbox — is counted but
    never called left behind."""
    root = Path.home() / ".cheese" / "home"
    deadline = time.monotonic() + WALK_S
    live = {(str(project), str(resource)) for project, resource in live}
    homes, left_behind = [], []
    for project in sorted(root.iterdir()) if root.is_dir() else []:
        for home in sorted(project.iterdir()) if project.is_dir() else []:
            if home.is_symlink() or not home.is_dir():
                continue
            key = (project.name, home.name)
            marker = cleanup["sandbox_marker"](home)
            sandbox = marker.exists()
            if walk:
                used = usage(home, deadline)
                homes.append(
                    {"project": key[0], "resource": key[1], "sandbox": sandbox, **used}
                )
            written = max(
                (path.stat().st_mtime for path in (home, marker) if path.exists()),
                default=0,
            )
            if sandbox and key not in live and time.time() - written > grace_s:
                left_behind.append({"project": key[0], "resource": key[1]})
    return {
        "filesystem": filesystem(root if root.is_dir() else Path.home()),
        "homes": homes,
        "left_behind": left_behind,
    }


ACTIONS = {"destroy": destroy, "disk": disk}


def main(cleanup, request):
    started = time.monotonic()
    answer = ACTIONS[request.pop("action")](cleanup, **request)
    answer["seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(answer))
    sys.stdout.flush()
