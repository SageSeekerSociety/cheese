"""What the platform does to one session's home on a cloud host.

Piped to ``python3 -`` on the host (``lifecycle.program``) after the source of
``agent/resource_cleanup.py``, whose helpers arrive as ``cleanup``: this file
runs there with nothing of ours importable, like that one.

- ``destroy``: end the session's sandbox and delete its home. Before its
  executor is asked to stop, what is still running in the sandbox is read
  from its cgroup — each process's command line, with what looks like a
  secret masked — so the session can be told what went with it (``processes``).
  Its executor is asked to stop, then whatever still holds the home — a dev
  server, a background command — is ended, and the home, the legacy work
  directory and the sandbox's temporary directory are removed. A home already
  gone is not an error.
- ``disk``: how full the filesystem the homes are on is, in bytes and in
  inodes, and the machine's own when the homes are on another (``system``),
  and which sandbox homes on this machine are not among ``live`` and
  were last written before ``grace_s`` — homes whose sandbox no longer exists
  (``disk_guard``). On a platform host each sandbox's home has a project
  quota of its own (``sandbox_host.py``), and the kernel answers a
  ``statvfs`` of the home with that quota's limit and use: what each home
  holds, read without walking it. Also what the machine itself uses — its
  memory and swap, its memory pressure, whether zswap is loaded, the CPU time
  its processes have taken, its load — and what each sandbox's cgroup holds
  in memory now and at most, its anonymous and file pages, its swap and how
  often it was OOM-killed (``machine``, ``sandboxes``), for the admin machines
  page (``readings``). A reading this machine cannot give, on one with no
  /proc, no sandbox cgroups, no PSI or no quotas, is left out, not guessed.
  ``swap``, ``freeze``, ``thaw`` and ``reclaim`` wrap the helper's commands of
  those names, which the lifecycle uses to freeze an idle sandbox and to wake
  it (``freeze`` stops its processes, ``swap`` changes how much of the host's
  swap it may use, ``reclaim`` makes it give memory back). Each sandbox named
  in ``idle`` is asked to give back its page cache first
  (``sandbox_host.py reclaim``), where the machine has the helper.

Answers one line of JSON.
"""

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

#: The machine's cgroup root, and where the sandbox helper puts each sandbox:
#: a cgroup named after its home (``sandbox_host.CGROUP``, a copy held to this
#: by test_footprint_root.py). Its files are readable by every user.
CGROUP_ROOT = Path("/sys/fs/cgroup")
SANDBOX_CGROUPS = CGROUP_ROOT / "cheese-sandboxes"
PROC = Path("/proc")
MEMINFO = PROC / "meminfo"
PRESSURE_MEMORY = PROC / "pressure/memory"
ZSWAP = Path("/sys/module/zswap")

#: How many of a destroyed sandbox's processes are named to its session, and
#: how much of each command line: enough to recognise what was running, short
#: enough to sit in a tool result.
LOST_PROCESSES_MAX = 10
LOST_PROCESS_CHARS = 200
#: Programs that are the sandbox's own machinery or only wait on the process
#: beside them, and would name nothing the session started.
_PLUMBING_SUFFIXES = ("remote-execution/runtime.py", "cheese-environment.py")
_SHELLS = {"sh", "bash", "dash", "zsh", "fish", "busybox"}
_PRIVILEGE = {"sudo", "su"}

#: A value a command line gives a secret to, by the flag or variable it is on:
#: ``--token=…``, ``--password …``, ``PASSWORD=…``.
_SECRET_FLAG = re.compile(
    r"(?i)\b((?:--?)?[a-z0-9_-]*(?:token|password|passwd|secret|api[-_]?key|"
    r"access[-_]?key|private[-_]?key|authorization)[a-z0-9_-]*)"
    r"(=|\s+)(\S+)"
)
#: ``Authorization: Bearer …`` and a bare ``Bearer …``.
_AUTH_HEADER = re.compile(r"(?i)\b(authorization\s*:\s*)(?:bearer\s+)?(\S+)")
_BEARER = re.compile(r"(?i)\b(bearer\s+)([^\s]+)")
#: A run long enough to be a key or token rather than a word or a path: no
#: dots or slashes in it, and both letters and digits (see ``_looks_random``).
_RANDOM_RUN = re.compile(r"(?<!\S)[A-Za-z0-9_+=-]{32,}(?!\S)")


def _looks_random(run: str) -> bool:
    return any("0" <= char <= "9" for char in run) and any(
        char.isalpha() for char in run
    )


def _redact(line: str) -> str:
    """``line`` with what looks like a secret masked: the value after a flag
    or variable that names one, an authorization header, and a long random
    run (an access key, a token). Wrong in the safe direction only: a value
    wrongly masked costs a reader a detail, a secret left in a tool result is
    in the transcript for good."""
    line = _AUTH_HEADER.sub(r"\1***", line)
    line = _BEARER.sub(r"\1***", line)
    line = _SECRET_FLAG.sub(r"\1\2***", line)
    return _RANDOM_RUN.sub(
        lambda match: "***" if _looks_random(match.group()) else match.group(), line
    )


def processes(name) -> list:
    """What runs in the sandbox whose cgroup is named ``name``, read from its
    cgroup's ``cgroup.procs``: each process's command line, the executor and
    bare shell wrappers left out (their own children are listed), secrets
    masked, at most ``LOST_PROCESSES_MAX`` of them.

    Read before the sandbox is stopped: afterwards there is nothing to read.
    A cgroup that is not there, or a pid that went between the two reads,
    contributes nothing — this is a description of what was there, not an
    inventory that has to be complete."""
    try:
        pids = (SANDBOX_CGROUPS / str(name) / "cgroup.procs").read_text().split()
    except OSError:
        return []
    found = []
    for pid in pids:
        try:
            raw = (PROC / pid / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if not argv or _plumbing(argv):
            continue
        line = _redact(" ".join(argv))[:LOST_PROCESS_CHARS]
        if line not in found:
            found.append(line)
        if len(found) >= LOST_PROCESSES_MAX:
            break
    return found


def _plumbing(argv) -> bool:
    """Whether a process is the sandbox's own machinery rather than something
    the session started: the executor, or a shell that only carries the
    command beside it in this cgroup."""
    if any(arg.endswith(_PLUMBING_SUFFIXES) for arg in argv):
        return True
    command = Path(argv[0]).name
    if command in _PRIVILEGE:
        return True
    return command in _SHELLS and "-c" in argv


def destroy(cleanup, project, resource) -> dict:
    home, work = cleanup["resource_paths"](Path.home(), project, resource)
    # A frozen sandbox's executor cannot answer the stop below: its processes
    # are stopped by the freezer, so a request sent over its socket waits for
    # a reply that only a thaw can allow. Thaw first; it changes nothing on a
    # sandbox that is not frozen, and the helper's `down` below kills whatever
    # is left even if this cannot run.
    thaw(cleanup, [home.name])
    # Thawed, but before the executor is stopped: what the session loses with
    # this sandbox. Read here, after the thaw, because a process stopped by
    # the freezer still has its command line.
    running = processes(home.name)
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
    return {"destroyed": True, "processes": running}


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


# Unannotated for the same reason as `_number`: this runs on Python 3.9 too.
def _fields(path, *names):
    """The integers a kernel file keeps as ``key value`` lines, by key."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in names and parts[1].isdigit():
            found[parts[0]] = int(parts[1])
    return found


def _pressure(path):
    """Memory pressure from /proc/pressure/memory: for ``some`` and ``full``,
    the share of time it was stalled over the last minute and five, as the
    kernel reports it (percent, not a fraction). None where the machine has
    no such file (whenever the kernel was built without PSI)."""
    try:
        text = path.read_text()
    except OSError:
        return None
    found = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] not in ("some", "full"):
            continue
        values = {}
        for part in parts[1:]:
            key, _, value = part.partition("=")
            if key not in ("avg10", "avg60"):
                continue
            try:
                values[key] = float(value)
            except ValueError:
                pass
        if values:
            found[parts[0]] = values
    return found or None


def machine() -> dict:
    """What this machine uses: its memory from /proc/meminfo, which in a
    MicroCloud LXC is the container's own (lxcfs), and its swap; memory
    pressure from /proc/pressure/memory; whether zswap is loaded; the CPU
    time its processes have taken, from its cgroup root, a counter the
    platform turns into a share between two readings; its one-minute load."""
    found = {}
    total = _number(MEMINFO, "MemTotal:")
    available = _number(MEMINFO, "MemAvailable:")
    if total and available is not None:
        found["memory"] = total * 1024
        found["memory_available"] = available * 1024
    swap_total = _number(MEMINFO, "SwapTotal:")
    swap_free = _number(MEMINFO, "SwapFree:")
    if swap_total is not None and swap_free is not None:
        found["swap_bytes"] = swap_total * 1024
        found["swap_free_bytes"] = swap_free * 1024
    pressure = _pressure(PRESSURE_MEMORY)
    if pressure is not None:
        found["memory_pressure"] = pressure
    found["zswap"] = ZSWAP.is_dir()
    usage = _number(CGROUP_ROOT / "cpu.stat", "usage_usec")
    if usage is not None:
        found["cpu_usage_usec"] = usage
    try:
        found["load1"] = round(os.getloadavg()[0], 2)
    except OSError:
        pass
    return found


def sandboxes() -> list:
    """Each sandbox the helper gave a cgroup: its home's name; the memory it
    holds now and has held at most since it started, and how much of that is
    anonymous and page cache; how much of the host's swap it uses (none at
    `memory.swap.max` 0); and how many times the kernel has had to kill one
    of its processes for running out of memory."""
    found = []
    groups = sorted(SANDBOX_CGROUPS.iterdir()) if SANDBOX_CGROUPS.is_dir() else []
    for group in groups:
        memory = _number(group / "memory.current") if group.is_dir() else None
        if memory is None:
            continue
        stats = _fields(group / "memory.stat", "anon", "file")
        found.append(
            {
                "resource": group.name,
                "memory": memory,
                "memory_peak": _number(group / "memory.peak"),
                "swap": _number(group / "memory.swap.current"),
                "anon": stats.get("anon"),
                "file": stats.get("file"),
                "oom_kill": _fields(group / "memory.events", "oom_kill").get(
                    "oom_kill"
                ),
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


def _helper(cleanup, *argv) -> dict:
    """Run one of the helper's commands on the machine, which must be there:
    a machine without it (a person's own computer) skips this."""
    if not Path(cleanup["SANDBOX_HOST"]).exists():
        return {}
    result = cleanup["run_command"](["sudo", "-n", cleanup["SANDBOX_HOST"], *argv])
    if result.returncode:
        return {f"{argv[0]}_error": result.stderr.strip()[-300:]}
    return json.loads(result.stdout)


def swap(cleanup, name, mib) -> dict:
    """Set how much of the host's swap one sandbox may use, in MiB
    (`sandbox_host.py swap`). A frozen sandbox gets its memory limit while it
    sleeps, so pages the kernel can do without may leave it; waking it sets
    this back to 0 (`machine/lifecycle.py`)."""
    return _helper(cleanup, "swap", str(name), str(mib))


def freeze(cleanup, names) -> dict:
    """Stop every process in these sandboxes (`sandbox_host.py freeze`): the
    idle freeze, before `swap` and `reclaim` give back what their memory the
    machine can do without (`machine/lifecycle.py`)."""
    return _helper(cleanup, "freeze", *[str(name) for name in names])


def thaw(cleanup, names) -> dict:
    """Resume the processes of these sandboxes (`sandbox_host.py thaw`), the
    wake a session's next tool call asks for (`machine/lifecycle.py`)."""
    return _helper(cleanup, "thaw", *[str(name) for name in names])


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
    # The machine's own disk, where its releases, toolchain and Claude are,
    # when the homes are on another (a platform host's data disk).
    system = {}
    if root.is_dir() and root.stat().st_dev != Path.home().stat().st_dev:
        system = {"system": filesystem(Path.home())}
    return {
        "filesystem": whole,
        **system,
        "homes": homes,
        "left_behind": left_behind,
        "machine": machine(),
        "sandboxes": sandboxes(),
        **reclaimed,
    }


ACTIONS = {
    "destroy": destroy,
    "disk": disk,
    "swap": swap,
    "freeze": freeze,
    "thaw": thaw,
    "reclaim": reclaim,
}


def main(cleanup, request):
    started = time.monotonic()
    answer = ACTIONS[request.pop("action")](cleanup, **request)
    answer["seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(answer))
    sys.stdout.flush()
