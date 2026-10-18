"""How many test workers a room's tools may start, from the CPU it is given.

`pytest -n auto` and vitest size their worker pool from the processors the
machine reports, and a sandbox's CPU quota does not change what they report:
`cpu.max` caps bandwidth, not affinity, so `nproc` still answers the host's
four cores — and four sandboxes on that host would start sixteen workers
between them (measured 2026-10-18, `沙箱池 load1 取证`). So the room is told
the number its quota allows, as process environment:

- `PYTEST_XDIST_AUTO_NUM_WORKERS` is what xdist's `auto` reads instead of the
  processor count; an explicit `-n N` still wins.
- `VITEST_MAX_FORKS` is what `frontend/vite.config.ts` sizes its own worker
  pool from (and what vitest's fork pool read before 4). Deliberately not
  `VITEST_MAX_WORKERS`: vitest applies that one AFTER everything else,
  command-line flags included, so it would undo a harness's own decision —
  `ci:fast` passes `--maxWorkers=1 --no-file-parallelism` from a memory
  budget, and two 2 GB forks in a 3 GiB sandbox is the failure this whole
  change is meant to prevent. Config is a default; a flag is somebody's
  answer.

WHERE THE QUOTA COMES FROM. A platform sandbox's quota travels in the
executor payload (`sandbox.cpus`): it is the same number `sandbox_host.limit`
writes into the sandbox's `cpu.max`, and unlike the file it is known here
before the sandbox is started. A room with no per-sandbox limit asks its own
cgroup instead — `/proc/self/cgroup` names the group this process runs in, and
that group's `cpu.max` is `<quota> <period>` in microseconds or `max` for
none. A room nothing caps gets neither variable: `auto` and vitest's own
default already size themselves from the processors it can see, which is what
they did before this existed.

Standard library only, and Python 3.9: shipped beside the executor's runtime
(`remote-execution/worker_quota.py`) and exec'd standalone there, where it
cannot import `app.*`.
"""

from __future__ import annotations

from pathlib import Path

#: The cgroup v2 tree, and the file naming a group's CPU bandwidth limit:
#: "<quota> <period>", both in microseconds, or "max <period>" for no limit.
CGROUP = Path("/sys/fs/cgroup")
CPU_MAX = "cpu.max"
#: Where this process's cgroup is: one "hierarchy:controllers:path" line per
#: hierarchy, and cgroup v2's is the one with an empty controller list.
OWN = Path("/proc/self/cgroup")
#: The variables a test runner reads a worker count from, by the name it reads.
VARIABLES = ("PYTEST_XDIST_AUTO_NUM_WORKERS", "VITEST_MAX_FORKS")


def environment(sandbox, cgroup=CGROUP, own=OWN):
    """What a room's test runners are told, for the CPU it may use — and
    nothing when nothing caps it, so the tools keep the default they had."""
    cores = quota(sandbox, cgroup=cgroup, own=own)
    if cores is None:
        return {}
    workers = max(1, int(cores))
    return {name: str(workers) for name in VARIABLES}


def quota(sandbox, cgroup=CGROUP, own=OWN):
    """The CPU this room may use, in cores, or None when nothing caps it.

    The sandbox's own limit first: this runs outside the sandbox, before its
    cgroup exists, and `sandbox.cpus` is what that cgroup is created with.
    """
    cpus = sandbox.get("cpus") if isinstance(sandbox, dict) else None
    return float(cpus) if cpus else own_quota(cgroup=cgroup, own=own)


def own_quota(cgroup=CGROUP, own=OWN):
    """The CPU quota this process is under, or None when nothing caps it.

    Every cgroup from its own up to the root, and the smallest quota among
    them: a leaf that says `max` is a process whose budget is its parent's,
    and a budget set closer to the root cannot be exceeded further down, so
    the one that counts is the tightest on the way, not the nearest.
    """
    for line in _read(own).splitlines():
        hierarchy, _, path = line.partition("::")
        if hierarchy != "0":
            continue
        parts = [part for part in path.strip("/").split("/") if part]
        limits = [
            cores(_read(cgroup.joinpath(*parts[:depth]) / CPU_MAX))
            for depth in range(len(parts), -1, -1)
        ]
        found = [limit for limit in limits if limit is not None]
        return min(found) if found else None
    return None


def cores(text):
    """A `cpu.max` reading as a CPU count: "200000 100000" is 2, "max" none."""
    parts = (text or "").split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        return int(parts[0]) / int(parts[1])
    except (ValueError, ZeroDivisionError):
        return None


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
