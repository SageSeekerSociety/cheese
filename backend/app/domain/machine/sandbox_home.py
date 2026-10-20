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
  (``disk_guard``). On a platform host each sandbox's home has a project
  quota of its own (``sandbox_host.py``), and the kernel answers a
  ``statvfs`` of the home with that quota's limit and use: what each home
  holds, read without walking it. Also what the machine itself uses — its
  memory, the CPU time its processes have taken, its load — and what each
  sandbox's cgroup holds in memory now and at most (``machine``,
  ``sandboxes``), for the admin machines page (``readings``). A reading this
  machine cannot give, on one with no /proc, no sandbox cgroups or no
  quotas, is left out, not guessed. Each sandbox named in ``idle`` is asked to
  give back its page cache first (``sandbox_host.py reclaim``), where the
  machine has the helper.

Answers one line of JSON.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

#: The machine's cgroup root, and where the sandbox helper puts each sandbox:
#: a cgroup named after its home (``sandbox_host.CGROUP``, a copy held to this
#: by test_footprint_root.py). Its files are readable by every user.
CGROUP_ROOT = Path("/sys/fs/cgroup")
SANDBOX_CGROUPS = CGROUP_ROOT / "cheese-sandboxes"
MEMINFO = Path("/proc/meminfo")


def destroy(cleanup, project, resource) -> dict:
    home, work = cleanup["resource_paths"](Path.home(), project, resource)
    if home.exists():
        cleanup["stop_executor"](home, resource)
    cleanup["end_holders"]([home, work])
    cleanup["check_no_writers"]([home, work])
    for path in (work, home, cleanup["resource_tmp"](resource)):
        if path is not None and path.exists():
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


# Unannotated: this runs on a person's computer too, macOS's own Python 3.9
# among them, which cannot evaluate `int | None`.
def _number(path, field=None):
    """An integer a kernel file holds — the whole file, or the value after
    ``field`` on one of its lines — or None where there is none."""
    try:
        text = path.read_text()
    except OSError:
        return None
    if field is None:
        text = text.strip()
        return int(text) if text.isdigit() else None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == field and parts[1].isdigit():
            return int(parts[1])
    return None


def machine() -> dict:
    """What this machine uses: its memory from /proc/meminfo, which in a
    MicroCloud LXC is the container's own (lxcfs); the CPU time its processes
    have taken, from its cgroup root, a counter the platform turns into a
    share between two readings; its one-minute load."""
    found = {}
    total = _number(MEMINFO, "MemTotal:")
    available = _number(MEMINFO, "MemAvailable:")
    if total and available is not None:
        found["memory"] = total * 1024
        found["memory_available"] = available * 1024
    usage = _number(CGROUP_ROOT / "cpu.stat", "usage_usec")
    if usage is not None:
        found["cpu_usage_usec"] = usage
    try:
        found["load1"] = round(os.getloadavg()[0], 2)
    except OSError:
        pass
    return found


def sandboxes() -> list:
    """Each sandbox the helper gave a cgroup: its home's name, and the memory
    it holds now and has held at most since it started."""
    found = []
    groups = sorted(SANDBOX_CGROUPS.iterdir()) if SANDBOX_CGROUPS.is_dir() else []
    for group in groups:
        memory = _number(group / "memory.current") if group.is_dir() else None
        if memory is None:
            continue
        found.append(
            {
                "resource": group.name,
                "memory": memory,
                "memory_peak": _number(group / "memory.peak"),
            }
        )
    return found


def used(home: Path, whole: dict):
    """What a home holds by its own quota, or None where it has none: a
    ``statvfs`` of a directory in an XFS project answers the project's limit
    as its size, which the filesystem's own size tells apart."""
    own = filesystem(home)
    if (own["bytes"], own["inodes"]) == (whole["bytes"], whole["inodes"]):
        return None
    return {
        "bytes": own["bytes"] - own["bytes_free"],
        "inodes": own["inodes"] - own["inodes_free"],
        "limit_bytes": own["bytes"],
        "limit_inodes": own["inodes"],
    }


def reclaim(cleanup, names) -> dict:
    """Have the sandboxes in ``names`` give back their page cache, and say
    how much each did. Only the helper can (``sandbox_host.py``), so a machine
    without it skips this."""
    if not names or not Path(cleanup["SANDBOX_HOST"]).exists():
        return {}
    result = cleanup["run_command"](
        ["sudo", "-n", cleanup["SANDBOX_HOST"], "reclaim", *names]
    )
    if result.returncode:
        return {"reclaim_error": result.stderr.strip()[-300:]}
    return json.loads(result.stdout)


def disk(cleanup, live, grace_s, idle=()) -> dict:
    """What the homes on this machine hold, and the sandbox homes left behind.

    ``live`` is every (project, resource) of a sandbox the platform has on
    this machine. A sandbox home is one with the platform's sandbox marker
    (``cleanup["sandbox_marker"]``); a home without one — a room's own
    directory on a computer that runs it without a sandbox — is never called
    left behind."""
    root = Path.home() / os.environ.get("CHEESE_PROFILE_ROOT", ".cheese") / "home"
    reclaimed = reclaim(cleanup, list(idle))
    whole = filesystem(root if root.is_dir() else Path.home())
    live = {(str(project), str(resource)) for project, resource in live}
    homes, left_behind = [], []
    for project in sorted(root.iterdir()) if root.is_dir() else []:
        for home in sorted(project.iterdir()) if project.is_dir() else []:
            if home.is_symlink() or not home.is_dir():
                continue
            key = (project.name, home.name)
            marker = cleanup["sandbox_marker"](home)
            sandbox = marker.exists()
            held = used(home, whole) if sandbox else None
            if held is not None:
                homes.append({"project": key[0], "resource": key[1], **held})
            written = max(
                (path.stat().st_mtime for path in (home, marker) if path.exists()),
                default=0,
            )
            if sandbox and key not in live and time.time() - written > grace_s:
                left_behind.append({"project": key[0], "resource": key[1]})
    return {
        "filesystem": whole,
        "homes": homes,
        "left_behind": left_behind,
        "machine": machine(),
        "sandboxes": sandboxes(),
        **reclaimed,
    }


ACTIONS = {"destroy": destroy, "disk": disk}


def main(cleanup, request):
    started = time.monotonic()
    answer = ACTIONS[request.pop("action")](cleanup, **request)
    answer["seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(answer))
    sys.stdout.flush()
