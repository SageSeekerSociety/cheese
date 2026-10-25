"""Device-side checks and deletion, restricted to a recorded resource directory."""

# Annotations stay unevaluated: this runs on whatever Python 3 the machine has,
# macOS's own 3.9 among them, which cannot evaluate `dict | None`.
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import runpy
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid
from pathlib import Path

if sys.platform != "win32":
    import fcntl

# The one directory the platform writes under the machine's own `$HOME`. A copy
# of `place.footprint_root()`, not a second answer: this file is piped to the
# machine on stdin and runs there with no `__file__` and nothing of ours
# importable, so it carries the name; the copy is held to the original by
# test_footprint_root.py.
#
# This is the deleting side of what `device_provider.DEVICE_ROOT` builds, which
# is why it is spelled once here and read below rather than typed at each use:
# a name that drifts from the building side makes every room's home and work
# tree unreachable to the teardown, and the teardown reports success on paths
# that never existed while the real hundreds of gigabytes stay on the machine.
FOOTPRINT_ROOT = ".cheese"
# Where a session's temporary files go: on disk, outside the footprint, because
# the system keeps its on-disk temporary files there. The launcher reads it here.
SESSION_TMP = "/var/tmp"

# The checkout inside a room's home — a copy of `place.CHECKOUT_DIR`, held to it
# by test_footprint_root.py. The teardown has to look in the same directory the
# launcher built, so a rename that reaches only one of them cannot happen.
CHECKOUT_DIR = "room"

# Which rooms run in a sandbox, and the release each was started from — a copy
# of `place.SANDBOXES_DIR`, held to it by test_footprint_root.py. A sandboxed
# room can write all of its own home, so for one this teardown takes nothing
# from there that decides what it runs: not the programs in the room's
# installation, and not git's config, hooks and attributes in its checkouts,
# which name programs git runs.
SANDBOXES = "sandboxes"
# What gives a sandboxed room its network and limits, and takes them down — a
# copy of `bootstrap.SANDBOX_HOST`, held to it by test_footprint_root.py.
SANDBOX_HOST = "/usr/local/libexec/cheese-sandbox"

# Where an archived room's session transcripts wait under FOOTPRINT_ROOT until
# their copy in the private bucket is confirmed and their retention is over
# (room_cleanup/retire.py).
TRANSCRIPTS_ROOT = "transcripts"

# Where a resource's harness sessions keep their state under FOOTPRINT_ROOT — a
# copy of the directory `machine_launcher.state_dir` names, held to it by
# test_footprint_root.py: Codex's and pi's own session files are in there, not
# in the room's home.
HARNESS_ROOT = "harness"

# Where the seats of a room keep the files that belong to one teammate's session
# rather than to the room — a copy of `place.SEATS_DIR`, held to it by
# test_footprint_root.py, because this file is piped to the machine on stdin and
# can import nothing of ours. The target that names the room's executor is one
# of those files now, and the teardown reads it to decide whether there is an
# executor to stop and a private scratch to release: a copy that drifts here is
# a room torn down with its executor still running and its container never
# released.
SEATS_DIR = "seats"

# The platform's directories INSIDE a room's home, the current one first — the
# pair is `place.session_platform_dirs()`, and it is a pair only in there. The
# machine's own `$HOME` has never held more than FOOTPRINT_ROOT.
#
# Tearing a room down reads what preparing it wrote, and a
# room prepared under an earlier root still has all of it where that launcher
# put it — it does not move until something prepares the room again, and a room
# being deleted never will. Reading only where we would install today answers
# "this room never had an executor" for a room that has one running, and the
# answer is acted on: the detached daemon is left alive under a home that is
# then removed from under it, the private seat it holds is never released, and
# its room directory is taken for one from before rooms had an executor.
PLATFORM_DIRS = (FOOTPRINT_ROOT, ".claude")

# How long an archived room's processes get to leave on their own before the
# cleanup ends them. The graceful path below — the session's launcher asked to
# stop, the executor asked to stop — is what the first attempts do, and a
# room whose agent is between tool calls is gone within a minute of it. What
# it cannot reach is everything else the room spawned: a dev server the agent
# started, a viewer still attached to the terminal, an agent wedged inside a
# tool call. Measured on dev 2026-09-18: 173 such processes across 45 archived
# rooms, some days old, every one of them holding its room's cleanup at
# "resource still has processes holding files" forever. The room is archived;
# past this grace, a process still inside it is a leak, not a session.
FORCE_AFTER_S = float(os.environ.get("CHEESE_CLEANUP_FORCE_AFTER_S", "600"))


class StillRunning(RuntimeError):
    """The room is not quiescent yet — the one refusal that time may overrule.

    Every other RuntimeError the checks raise is a safety refusal (another
    user's socket, a session that does not identify this resource), and no
    amount of waiting makes those go away, so nothing escalates
    on them.
    """


# How this script says `StillRunning` to the backend that ran it on a device:
# waiting is not a failure, and the exit code is all that crosses the exec.
STILL_RUNNING_EXIT = 75


def platform_dir(home: Path) -> Path:
    """Where this room's platform files actually are.

    Resolved once per room rather than per file, so a room's executor state, the
    runtime that speaks to it and the markers that describe it are always read
    out of the same installation — a room migrated mid-life has leftovers under
    both, and picking a runtime from one root to drive a state directory in the
    other would be worse than reading neither.
    """
    for name in PLATFORM_DIRS:
        directory = home / name
        if (directory / "executor").exists() or (
            directory / "remote-target.json"
        ).exists():
            return directory
    return home / PLATFORM_DIRS[0]


def lock(file) -> None:
    """flock(LOCK_EX). This file arrives on stdin and cannot load portable.py
    for the Windows lock, so it carries the same one."""
    if sys.platform == "win32":
        # deferred-import: Windows-only module; absent on other platforms
        import msvcrt

        while True:
            os.lseek(file.fileno(), 0x7FFFFFF0, os.SEEK_SET)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.05)
    fcntl.flock(file, fcntl.LOCK_EX)


def portable(paths: list[Path]) -> dict | None:
    """The Windows primitives out of a room's own executor installation.

    Every process the platform starts in a room comes from that installation,
    so a room without one has nothing of ours running in it.
    """
    for path in paths:
        helper = platform_dir(path) / "remote-execution/portable.py"
        if helper.is_file():
            return runpy.run_path(str(helper))
    return None


def windows_holders(paths: list[Path]) -> list[int]:
    """What `lsof +D` answers, as far as Windows lets a process be asked.

    A process holds the room when its executable, its command line or its
    working directory is inside it. Files it merely has open are not visible
    this way — but Windows refuses to delete a file that is open, so deletion
    fails there rather than going ahead underneath it.
    """
    helper = portable(paths)
    if helper is None:
        return []
    roots = [os.path.normcase(str(path.resolve())) + os.sep for path in paths]
    own = {os.getpid(), os.getppid()}
    found = []
    for row in helper["processes"](directories=True):
        places = [row["image"], row["command"], row.get("cwd", "")]
        if row["pid"] not in own and any(
            root in os.path.normcase(place) + os.sep
            for root in roots
            for place in places
        ):
            found.append(row["pid"])
    return found


#: Where Linux shows each process's working directory, root, executable, open
#: files and mapped files. Read there rather than by lsof, which a stock Linux
#: image need not have: the Debian cloud image behind the MicroCloud VM offering
#: has none, and every sandbox destroy on those hosts failed for it (dev,
#: 2026-10-20). macOS has no /proc and ships lsof.
PROC = Path("/proc")


def proc_index(paths: list[Path]) -> set:
    """Every file and directory under these, as (device, inode): what a process
    holding something of the room holds, whatever path it reaches it by. A
    process in another mount namespace names the room differently (a private
    session sees its directory as `/work`), and on a platform host the homes are
    also reachable through the data disk they are bound from; the inode is the
    same in every case. `lsof +D` matches the same way.

    The walk goes by directory descriptors and never follows a symlink below
    the paths given: what is inside the room is the room's to change while it
    is walked, and a directory it swaps for a link to `/usr/lib` must not make
    every process with libc mapped one to end.

    A part of the room it cannot read is a RuntimeError, never a part left out:
    an index missing a subtree finds nothing holding it and lets the room be
    deleted under whoever does. Only an entry that is gone, or is no longer a
    directory, is passed over. One descriptor is held per level, so a tree
    deeper than the descriptors left (about a thousand levels under the usual
    limit) is refused this way; `remove_tree` holds one per level too and
    could not delete it either."""
    found = set()

    def unreadable(path: str, error: OSError) -> RuntimeError:
        # i18n-exempt: runs on the machine; a cleanup's reason, for the logs and the row
        return RuntimeError(
            "could not establish whether the resource has active writers: "
            f"cannot read {path}: {error.strerror or error}"
        )

    def enter(descriptor: int, path: str) -> tuple:
        """Index this directory and its files; the subdirectories it lists are
        opened one at a time as the walk reaches them, so the descriptors held
        open are as many as the tree is deep."""
        try:
            info = os.fstat(descriptor)
            found.add((info.st_dev, info.st_ino))
            subdirectories = []
            for entry in os.scandir(descriptor):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        subdirectories.append(entry.name)
                        continue
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                found.add((info.st_dev, info.st_ino))
        except OSError as error:
            os.close(descriptor)
            raise unreadable(path, error) from error
        return descriptor, path, iter(subdirectories)

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for path in paths:
        try:
            top = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise unreadable(str(path), error) from error
        stack = [enter(top, str(path))]
        try:
            while stack:
                descriptor, where, pending = stack[-1]
                name = next(pending, None)
                if name is None:
                    os.close(descriptor)
                    stack.pop()
                    continue
                inside = os.path.join(where, name)
                try:
                    child = os.open(name, flags, dir_fd=descriptor)
                except (FileNotFoundError, NotADirectoryError):
                    continue  # gone, or no longer a directory
                except OSError as error:
                    if error.errno == errno.ELOOP:
                        continue  # now a link: nothing of the room's to follow
                    raise unreadable(inside, error) from error
                stack.append(enter(child, inside))
        finally:
            for descriptor, _, _ in stack:
                os.close(descriptor)
    return found


#: statx(2), which can be told to answer from what the kernel already knows
#: of a file (`AT_STATX_DONT_SYNC`) instead of asking its filesystem: a
#: process may have a file open on a FUSE mount whose daemon has stopped
#: answering, and a plain stat of it waits for that daemon for as long as it is
#: stuck. The device and inode never need asking for. (On NFS, following the
#: /proc link can still revalidate the file with its server first.)
#:
#: Called by its number rather than through libc's wrapper, which glibc has
#: only from 2.28 and musl only from 1.2.5; the kernel has had the call since
#: 4.11. The numbers are each architecture's own (`asm/unistd.h`).
_STATX_NUMBER = {
    "x86_64": 332,
    "i386": 383,
    "i686": 383,
    "aarch64": 291,
    "arm64": 291,
    "riscv64": 291,
    "loongarch64": 291,
    "armv7l": 397,
    "armv6l": 397,
    "ppc64le": 383,
    "ppc64": 383,
    "s390x": 379,
}
_STATX_DONT_SYNC = 0x4000
_STATX_TYPE_INO = 0x0001 | 0x0100
_AT_FDCWD = -100
#: What statx answers when it cannot look at anything at all (an old kernel, a
#: seccomp profile that refuses it), as against at this one file: taking those
#: for "nothing here" would report every room free.
_STATX_UNUSABLE = {errno.ENOSYS, errno.EPERM, errno.EINVAL, errno.EFAULT}


def _statx():
    """A lookup of (device, inode, mode) by path that never waits on the
    filesystem the path leads into. It answers None when this one file cannot
    be looked at (gone, or another user's process), and raises when statx
    cannot be used here at all."""
    number = _STATX_NUMBER.get(os.uname().machine)
    if number is None:
        # i18n-exempt: runs on the machine; a cleanup's reason, for the logs and the row
        raise RuntimeError(
            "could not establish whether the resource has active writers: "
            f"no statx call known for {os.uname().machine}"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    buffer = ctypes.create_string_buffer(256)

    def statx(path: str) -> tuple | None:
        answer = libc.syscall(
            ctypes.c_long(number),
            ctypes.c_int(_AT_FDCWD),
            ctypes.c_char_p(os.fsencode(path)),
            ctypes.c_int(_STATX_DONT_SYNC),
            ctypes.c_uint(_STATX_TYPE_INO),
            buffer,
        )
        if answer == 0:
            # struct statx: stx_mode at 28, stx_ino at 32, stx_dev_major and
            # stx_dev_minor at 136.
            (mode,) = struct.unpack_from("=H", buffer, 28)
            (inode,) = struct.unpack_from("=Q", buffer, 32)
            major, minor = struct.unpack_from("=II", buffer, 136)
            return os.makedev(major, minor), inode, mode
        failure = ctypes.get_errno()
        if failure in _STATX_UNUSABLE:
            # i18n-exempt: runs on the machine; a cleanup's reason, for the logs and the row
            raise RuntimeError(
                "could not establish whether the resource has active writers: "
                f"statx: {os.strerror(failure)}"
            )
        return None

    return statx


def _bound_sockets(pid: Path, cache: dict) -> dict:
    """Socket inode -> the path it is bound at, for the network namespace this
    process is in (`/proc/<pid>/net/unix` is its namespace's list), read once
    per namespace. A name relative to where the binder then was, and an
    abstract one, lead nowhere that can be looked up and are left out."""
    try:
        namespace = os.stat(pid / "ns" / "net").st_ino
    except OSError:
        return {}
    if namespace not in cache:
        bound = {}
        try:
            with open(pid / "net" / "unix") as table:
                next(table, None)
                for line in table:
                    fields = line.split(None, 7)
                    if len(fields) == 8 and fields[7].startswith("/"):
                        bound[fields[6]] = fields[7].rstrip("\n")
        except OSError:
            pass
        cache[namespace] = bound
    return cache[namespace]


def _holds(pid: Path, index: set, statx, sockets: dict) -> bool:
    """Whether this process's working directory, root, executable, an open file,
    a mapped file or a unix socket it has bound is one of `index`. A link the
    kernel does not let this user follow (another user's process) is not this
    user's to end either.

    A bound socket is held through a descriptor that leads to the socket, not to
    the file naming it in the room, so it is found by its name instead: looked
    up from the process's own root, where that name was bound. A process whose
    only hold on the room is the socket it listens on there (gpg-agent with its
    home in the room works from `/`) is found this way. Looking that name up
    walks its directories, which on a mount that has stopped answering can
    still wait; only processes with a socket bound pay for it."""
    links = [str(pid / "cwd"), str(pid / "root"), str(pid / "exe")]
    held = set()
    try:
        for fd in os.scandir(pid / "fd"):
            try:
                target = os.readlink(fd.path)
            except OSError:
                continue
            if target.startswith("socket:["):
                held.add(target[8:-1])
            else:
                links.append(fd.path)
    except OSError:
        pass
    for link in links:
        found = statx(link)
        if found is not None and found[:2] in index:
            return True
    if held:
        for inode, path in _bound_sockets(pid, sockets).items():
            if inode not in held:
                continue
            found = statx(f"{pid}/root{path}")
            if found and stat.S_ISSOCK(found[2]) and found[:2] in index:
                return True
    try:
        with open(pid / "maps") as maps:
            for line in maps:
                fields = line.split(None, 5)
                if len(fields) < 5 or fields[4] == "0":
                    continue
                major, minor = fields[3].split(":")
                device = os.makedev(int(major, 16), int(minor, 16))
                if (device, int(fields[4])) in index:
                    return True
    except (OSError, ValueError):
        pass
    return False


def proc_holders(paths: list[Path], index: set | None = None) -> list[int]:
    """What `lsof +D` answers, read from /proc: the processes whose working
    directory, root, executable, an open file, a mapped file or a bound unix
    socket lies inside these. `index` is `proc_index(paths)`, when the caller
    already has it."""
    if index is None:
        index = proc_index(paths)
    own = {os.getpid(), os.getppid()}
    statx = _statx()
    sockets = {}
    found = []
    for entry in os.scandir(PROC):
        if not entry.name.isdigit() or int(entry.name) in own:
            continue
        if _holds(Path(entry.path), index, statx, sockets):
            found.append(int(entry.name))
    return sorted(found)


def proc_command(pid: int) -> str:
    """A process's pid and command line, as `ps -o pid=,args=` would print it."""
    try:
        args = (PROC / str(pid) / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return str(pid)
    return f"{pid} " + " ".join(a.decode(errors="replace") for a in args if a)[:150]


def run_command(
    argv: list[str], *, cwd: Path | None = None, pass_fds: tuple[int, ...] = ()
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=False, pass_fds=pass_fds
    )


def sandbox_marker(home: Path) -> Path:
    return (
        Path.home()
        / os.environ.get("CHEESE_PROFILE_ROOT", FOOTPRINT_ROOT)
        / SANDBOXES
        / home.parent.name
        / home.name
    )


def sandbox_release(home: Path) -> Path | None:
    """The release this room's sandbox was started from, or None for a room
    that does not run in one."""
    marker = sandbox_marker(home)
    if not marker.exists():
        return None
    return Path(marker.read_text())


def platform_program(home: Path, relative: str) -> Path:
    """One of the platform's programs for this room, to run here, outside any
    sandbox: out of the release a sandboxed room was started from, which the
    room cannot write, and otherwise out of the room's own installation."""
    release = sandbox_release(home)
    return (release if release else platform_dir(home)) / relative


def private_helper(home: Path, marker: Path) -> Path:
    """`private.py`, out of the installation the target was read from.

    A seat keeps its own copy of the release — the launcher writes it into the
    seat that owns the session (`SEATS_DIR`) — and the room level keeps none, so
    the room-level read raised FileNotFoundError, the release never ran, and the
    private container stayed on the machine holding its scratch for good.
    """
    release = sandbox_release(home)
    return (release if release else marker.parent) / "remote-execution/private.py"


def git_profile(rooms: list[Path]) -> str:
    """The macOS sandbox git runs in for a sandboxed room (`git`): what the
    Linux one gives it, the room's directories and none of the owner's, and
    no network, which status and pruning do not need."""

    def quoted(path: Path) -> str:
        return json.dumps(os.path.realpath(str(path)))

    owner = Path.home()
    above = sorted(
        {
            str(parent)
            for room in rooms
            for parent in Path(os.path.realpath(str(room))).parents
        }
    )
    subpaths = " ".join(f"(subpath {quoted(room)})" for room in rooms)
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny file-write*)",
        f'(allow file-write* {subpaths} (literal "/dev/null"))',
        f"(deny file-read* (subpath {quoted(owner)}))",
        "(deny lsopen)",
        "(deny signal)",
        "(allow signal (target same-sandbox))",
    ]
    # Only with rooms to name: an `allow` with no filter allows everything.
    if rooms:
        lines.append(f"(allow file-read* {subpaths})")
        lines.append(
            "(allow file-read-metadata "
            + " ".join(f"(literal {json.dumps(path)})" for path in above)
            + ")"
        )
    return "\n".join(lines)


def git(args: list[str], cwd: Path, home: Path | None) -> subprocess.CompletedProcess:
    """Git in a checkout of the room whose home is `home` (None: of no room).
    A sandboxed room wrote that checkout's config, hooks and attributes, and
    git runs the programs they name (`core.fsmonitor`, a filter's `clean`), so
    for such a room git runs in a sandbox too: the room's directories and none
    of the owner's."""
    argv = ["git", *args]
    if home is not None and sandbox_release(home) is not None:
        paths = [
            path
            for path in resource_paths(Path.home(), home.parent.name, home.name)
            if path.exists()
        ]
        if sys.platform == "darwin":
            return run_command(
                ["/usr/bin/sandbox-exec", "-p", git_profile(paths), *argv], cwd=cwd
            )
        rooms = []
        for path in paths:
            rooms += ["--bind", str(path), str(path)]
        # The git the connector placed under the owner's home where the system
        # had none good enough (`cli/internal/devenv`), shown back read-only;
        # nothing else of the home is.
        placed = (
            Path.home()
            / os.environ.get("CHEESE_PROFILE_ROOT", FOOTPRINT_ROOT)
            / "runtime/git"
        )
        if placed.is_dir():
            rooms += ["--ro-bind", str(placed), str(placed)]
        argv = [
            "bwrap",
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            str(Path.home()),
            *rooms,
            "--chdir",
            str(cwd),
            "--",
            *argv,
        ]
    return run_command(argv, cwd=cwd)


def check_no_writers(paths: list[Path]) -> None:
    """Refuse if anything holds a file or a working directory under these.

    On Linux the processes are read from /proc (`proc_holders`); elsewhere
    by lsof. ONE lsof call, never one per path. `lsof +D` spends about two seconds
    walking every process's descriptors before it so much as looks at the
    directory, so its cost is per INVOCATION and not per tree: measured
    2026-09-18, an empty directory costs the same 1.97s as a repository, five
    directories in one call cost 2.07s, and the same five in five calls cost
    10.14s. A sweep over 102 rooms was therefore 3.4 minutes of lsof alone.

    Nothing is given up by batching: the message never names which path it was,
    because for every caller the answer is the same either way — do not delete.
    It names the processes: they are what the room is waiting on.
    """
    present = [str(path) for path in paths if path.exists()]
    if not present:
        return
    if sys.platform == "win32" or PROC.is_dir():
        if sys.platform == "win32":
            pids = windows_holders([Path(path) for path in present])
            named = [str(pid) for pid in pids]
        else:
            pids = proc_holders([Path(path) for path in present])
            named = [proc_command(pid) for pid in pids]
        if pids:
            raise StillRunning(
                "resource still has processes holding files or working "
                "directories: " + ", ".join(named)
            )
        return
    result = run_command(["lsof", "-t", "+D", *present])
    if result.stdout.strip():
        pids = sorted(set(result.stdout.split()), key=int)
        listing = run_command(["ps", "-o", "pid=,args=", "-p", ",".join(pids)])
        named = [
            " ".join(line.split())[:160]
            for line in listing.stdout.splitlines()
            if line.strip()
        ]
        raise StillRunning(
            "resource still has processes holding files or working directories: "
            + ("; ".join(named) or ", ".join(pids))
        )
    if result.returncode not in {0, 1} or result.stderr.strip():
        raise RuntimeError(
            "could not establish whether the resource has active writers"
        )


def holders(paths: list[Path]) -> list[int]:
    """The processes `check_no_writers` would refuse over, as pids."""
    present = [str(path) for path in paths if path.exists()]
    if not present:
        return []
    if sys.platform == "win32":
        return windows_holders([Path(path) for path in present])
    if PROC.is_dir():
        return proc_holders([Path(path) for path in present])
    result = run_command(["lsof", "-t", "+D", *present])
    own = {os.getpid(), os.getppid()}
    return [
        int(line)
        for line in result.stdout.split()
        if line.isdigit() and int(line) not in own
    ]


def end_holders(paths: list[Path]) -> None:
    """Terminate whatever still holds the room, gently first, then not.

    `holders` names the processes; each gets SIGTERM, ten seconds to act on it,
    then SIGKILL. A tmux pane whose process dies reads as dead to the next
    `request_exit`, which closes the session the ordinary way.
    """
    index = None
    if sys.platform != "win32" and PROC.is_dir():
        # The room is walked once; only the processes are read again each poll.
        index = proc_index([path for path in paths if path.exists()])

    def current() -> list[int]:
        return holders(paths) if index is None else proc_holders(paths, index)

    pids = current()
    for signal_number, wait in ((15, 10.0), (9, 2.0)):
        helper = portable(paths) if sys.platform == "win32" else None
        if helper is not None:
            # No SIGTERM to send on Windows; both rounds end them outright.
            helper["terminate"](pids)
            pids = []
        for pid in pids:
            try:
                os.kill(pid, signal_number)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + wait
        while pids and time.monotonic() < deadline:
            time.sleep(0.1)
            pids = [pid for pid in pids if pid in set(current())]
        if not pids:
            return


def remove_task_checkouts(home: Path, tasks: list[str]) -> dict:
    """Remove the checkouts of these closed tasks.

    The platform names the tasks. A closed task's work went to its branch and
    snapshot at each turn's checkpoint, so nothing here waits on publication;
    only a checkout something still has open stays, and comes back with the
    reason. A checkout that is not there is already done, so the same list can
    be sent again and again.
    """
    root = home / FOOTPRINT_ROOT / "tasks"
    if root.is_symlink():
        raise RuntimeError("task storage is a symlink")
    kept: dict[str, str] = {}
    candidates: list[Path] = []
    for task in tasks:
        path = root / str(uuid.UUID(task))
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            kept[task] = "unrecognized entry in task storage"
            continue
        if path.exists():
            candidates.append(path)
    try:
        check_no_writers(candidates)
        free = candidates
    except RuntimeError:
        # One lsof for all of them is the cheap answer; only when it says
        # something is inside does it pay to ask which ones.
        free = []
        for path in candidates:
            try:
                check_no_writers([path])
                free.append(path)
            except RuntimeError as exc:
                kept[path.name] = str(exc)
    for path in free:
        remove_tree(path)
    if free:
        # The repositories still list the removed checkouts until pruned.
        for repo in (home / FOOTPRINT_ROOT / "repositories").glob("*.git"):
            git(["worktree", "prune"], repo, home)
    return {"removed": [path.name for path in free], "kept": kept}


# What the platform sets `CHEESE_KEEP_URL` to for a home whose room files are
# in the bucket already.
KEPT_ALREADY = "-"


def kept_room_files(home: Path, executor: dict | None) -> Path | None:
    """The room directory of a home from before rooms had an executor, when it
    holds files: such a room worked in `room/` directly, with no repository
    behind it, so those files are on no forge. None for any other home."""
    room = home / CHECKOUT_DIR
    if executor is not None or room.is_symlink() or not room.is_dir():
        return None
    if (room / ".git").exists() or not any(room.iterdir()):
        return None
    return room


def keep_room_files(home: Path, executor: dict | None, url: str, resource: str):
    """Send what `kept_room_files` finds to the bucket, as one gzipped tar, by a PUT to
    `url` (presigned by the platform). Links are stored as links. Returns the
    archive's size and MD5, which the platform holds against what the bucket
    reports, or None when the home has no such files."""

    room = kept_room_files(home, executor)
    if room is None:
        return None
    if not url:
        raise RuntimeError("the room holds files and there is no bucket to keep them")
    scratch = (
        Path.home() / os.environ.get("CHEESE_PROFILE_ROOT", FOOTPRINT_ROOT) / "cleanup"
    )
    scratch.mkdir(parents=True, exist_ok=True)
    archive = scratch / (str(uuid.UUID(resource)) + ".room.tar.gz")
    try:
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(room, arcname=CHECKOUT_DIR, recursive=True)
        return put_archive(archive, url)
    finally:
        archive.unlink(missing_ok=True)


def put_archive(archive: Path, url: str) -> dict:
    """Send `archive` by one PUT to `url` (presigned by the platform); its size
    and MD5, which the platform holds against what the bucket reports."""
    digest = hashlib.md5()  # the bucket's ETag of one PUT
    with archive.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    size = archive.stat().st_size
    with archive.open("rb") as source:
        request = urllib.request.Request(
            url,
            data=source,
            method="PUT",
            headers={
                "Content-Length": str(size),
                "Content-Type": "application/gzip",
            },
        )
        with urllib.request.urlopen(request, timeout=600) as response:
            if response.status // 100 != 2:
                raise RuntimeError(f"bucket answered {response.status}")
    return {"size": size, "md5": digest.hexdigest()}


def remove_tree(path: Path) -> None:
    def retry_unlink(function, name, exc_info):
        error = exc_info[1]
        parent = Path(name).parent
        if (
            not isinstance(error, PermissionError)
            or function not in {os.unlink, os.rmdir}
            or not parent.is_relative_to(path)
        ):
            raise error
        # Go module caches contain read-only directories owned by this user.
        parent.chmod(parent.stat().st_mode | stat.S_IWUSR)
        if sys.platform == "win32":
            # There it is the file's own read-only flag that refuses, and Git
            # sets it on every object it writes.
            Path(name).chmod(stat.S_IWRITE)
        function(name)

    shutil.rmtree(path, onerror=retry_unlink)


def request_exit(home: Path, work: Path, lock_fd: int) -> None:
    marker = home / ".cheese/environment-session.json"
    # A sandboxed room has no terminal of the platform's on this machine, and
    # wrote this marker itself if it is there: it would name a terminal server
    # the room started, whose pane pids are numbered in the room's own pid
    # namespace and name other processes out here. Its processes end with its
    # sandbox (`stop_executor`).
    if sandbox_release(home) is not None or not marker.exists():
        return
    socket, name, *_ = json.loads(marker.read_text())
    # cksum consumes stdin below; do not trust a session name from an arbitrary file.
    expected = set()
    for directory in (work, home / CHECKOUT_DIR):
        checksum = subprocess.run(
            ["cksum"], input=str(directory).encode(), capture_output=True, check=True
        )
        expected.add("cheese_" + checksum.stdout.decode().split()[0])
    if not Path(socket).exists():
        return
    if Path(socket).stat().st_uid != os.getuid():
        raise RuntimeError("session socket is owned by another user")
    alive = run_command(
        ["tmux", "-S", socket, "has-session", "-t", "=" + name], pass_fds=(lock_fd,)
    )
    if alive.returncode:
        return
    if name not in expected:
        identity = run_command(
            [
                "tmux",
                "-S",
                socket,
                "show-options",
                "-v",
                "-t",
                "=" + name + ":",
                "@cheese-screen",
            ],
            pass_fds=(lock_fd,),
        )
        screen = json.loads(identity.stdout) if identity.returncode == 0 else {}
        env = screen.get("env", {})
        if (
            screen.get("sid") != name
            or env.get("CHEESE_PROJECT") != home.parent.name
            or (env.get("CHEESE_RESOURCE_ID") or env.get("CHEESE_TOPIC")) != home.name
        ):
            raise RuntimeError("session metadata does not identify this resource")
    panes = run_command(
        [
            "tmux",
            "-S",
            socket,
            "list-panes",
            "-s",
            "-t",
            "=" + name + ":",
            "-F",
            "#{pane_id} #{pane_dead} #{pane_pid}",
        ],
        pass_fds=(lock_fd,),
    )
    if panes.returncode or not panes.stdout.strip():
        raise RuntimeError("could not inspect the resource's terminal panes")
    live = [
        line.split()[2]
        for line in panes.stdout.splitlines()
        if len(line.split()) == 3 and line.split()[1] != "1"
    ]
    if not live:
        # remain-on-exit preserves the terminal after every agent has exited.
        closed = run_command(
            ["tmux", "-S", socket, "kill-session", "-t", "=" + name],
            pass_fds=(lock_fd,),
        )
        if closed.returncode:
            raise RuntimeError("could not close the exited resource terminal")
        return
    # The pane's program is the launcher, which traps TERM, stops the session's
    # runner, and the runner stops the agent. Sent by a child that holds the
    # stop lock, like every other command here, so a retry after this process
    # dies cannot run beside it.
    sent = run_command(["kill", "-TERM", *live], pass_fds=(lock_fd,))
    if sent.returncode:
        raise RuntimeError("could not request a graceful session exit")
    raise StillRunning("waiting for the agent to exit and finish transcript writes")


def resource_paths(
    machine_home: Path, project: str, resource: str
) -> tuple[Path, Path]:
    project, resource = str(uuid.UUID(project)), str(uuid.UUID(resource))
    paths = tuple(
        machine_home
        / os.environ.get("CHEESE_PROFILE_ROOT", FOOTPRINT_ROOT)
        / kind
        / project
        / resource
        for kind in ("home", "work")
    )
    for path in paths:
        for parent in (
            path,
            path.parent,
            path.parent.parent,
            path.parent.parent.parent,
        ):
            if parent.is_symlink():
                raise RuntimeError("resource path is a symlink")
    return paths[0], paths[1]


def resource_tmp(resource: str) -> Path | None:
    """The room's TMPDIR, where the launcher pointed its sessions: under this
    account's own directory in `/var/tmp`, by resource alone, so a socket made
    inside it still fits a socket path. None on Windows, where the launcher
    leaves sessions on the account's own temporary directory and makes none
    per room (`machine_launcher`), and where there is no uid to name one by:
    asking for it there failed every removal on a Windows machine."""
    if sys.platform == "win32":
        return None
    path = Path(SESSION_TMP) / f"cheese-{os.getuid()}" / str(uuid.UUID(resource))
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("resource path is a symlink")
    return path


def retained_transcripts(machine_home: Path, project: str, room: str, resource: str):
    """Where a removed home's session transcripts are kept until they expire.

    One archive per resource generation, under the room: a room reopened and
    archived again has a new generation and so a second archive beside the
    first, and neither overwrites the other."""
    project, room = str(uuid.UUID(project)), str(uuid.UUID(room))
    return (
        machine_home
        / os.environ.get("CHEESE_PROFILE_ROOT", FOOTPRINT_ROOT)
        / TRANSCRIPTS_ROOT
        / project
        / room
        / (str(uuid.UUID(resource)) + ".tar.gz")
    )


def harness_sessions(machine_home: Path, project: str, resource: str):
    """The session files the resource's harnesses wrote, each with the name it
    goes under in the archive (its path under the resource's harness root).

    Every harness keeps a seat's state in `<harness>/<seat>/` there
    (`machine_launcher.state_dir`) and its own session files in a `sessions`
    directory at most two levels into it: Codex's under its `CODEX_HOME`, pi's
    under `--session-dir` and each pi subagent's under its own. Nothing removes
    these directories; they are archived with the room's other transcripts so
    that the bucket has them too. A seat's `workspace` and `home` are the
    session's working directory and `HOME`, the agent's own files rather than
    the harness's, and are not looked in."""
    root = (
        machine_home
        / os.environ.get("CHEESE_PROFILE_ROOT", FOOTPRINT_ROOT)
        / HARNESS_ROOT
        / str(uuid.UUID(project))
        / str(uuid.UUID(resource))
    )
    if any(path.is_symlink() for path in (root, root.parent, root.parent.parent)):
        # i18n-exempt: runs on the machine; a cleanup's reason, for the logs and the row
        raise RuntimeError("harness path is a symlink")
    found = []
    for seat in sorted(root.glob("*/*")):
        for pattern in ("sessions", "*/sessions", "*/*/sessions"):
            for path in sorted(seat.glob(pattern)):
                inside = path.relative_to(seat).parts
                if inside[0] in ("workspace", "home"):
                    continue
                # A link anywhere from the harness root down is not followed:
                # the session could have pointed it anywhere on the machine.
                chain = [
                    root / Path(*path.relative_to(root).parts[:n])
                    for n in range(1, len(path.relative_to(root).parts) + 1)
                ]
                if any(part.is_symlink() for part in chain) or not path.is_dir():
                    continue
                found.append((path, path.relative_to(root).as_posix()))
    return found


def retain_transcripts(home: Path, archive: Path, harnesses=()) -> None:
    """Compress the resource's session files before the home goes.

    `.claude/projects` holds every Claude Code session of the room — the main
    transcript and each subagent's beside it; `harnesses` are the Codex and pi
    ones (`harness_sessions`). A link is stored as a link and never followed:
    Claude Code links a resumed session's subagent files to the originals in
    the same tree, and a link out of the home is not ours to read.
    Written under a temporary name and renamed, so an archive under the final
    name is always whole. One already there is kept as it is: it was written
    before anything of the home was removed, and a retry of a removal that
    stopped partway would find less of the home than it holds.
    """
    if archive.is_file():
        return
    sources = []
    claude = home / ".claude/projects"
    if not claude.is_symlink() and claude.is_dir():
        sources.append((claude, "projects"))
    sources += list(harnesses)
    if not sources:
        return
    archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = archive.with_name(archive.name + ".part")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with (
            os.fdopen(descriptor, "wb") as output,
            tarfile.open(fileobj=output, mode="w:gz") as bundle,
        ):
            for source, name in sources:
                bundle.add(source, arcname=name, recursive=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, archive)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    sync_directory(archive.parent)


def sync_directory(directory: Path) -> None:
    """Make a rename inside `directory` durable. Windows cannot open a
    directory to fsync it, so there the rename is left to NTFS's own journal."""
    if sys.platform == "win32":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def send_transcripts(archive: Path, url: str):
    """Send the retained archive to the bucket (`put_archive`); None when this
    machine holds none for the resource."""
    if not archive.is_file():
        return None
    if not url:
        # i18n-exempt: runs on the machine; a cleanup's reason, for the logs and the row
        raise RuntimeError("there is no bucket to send the transcripts to")
    return put_archive(archive, url)


def expire_transcripts(archive: Path) -> None:
    archive.unlink(missing_ok=True)
    archive.with_name(archive.name + ".part").unlink(missing_ok=True)
    # The room and project directories go once nothing else is in them.
    for directory in (archive.parent, archive.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            break


def target_markers(home: Path) -> list[Path]:
    """Every file that says which executor this room has, in reading order.

    Room level first: that is where a room prepared before seats existed keeps
    its one target, and a room does not move. Then one per seat, because the
    launcher now writes each session's target into the seat that owns it
    (`SEATS_DIR`). Every seat of a room names the same executor — one private
    container per topic, one device executor per home — so the first one found
    answers for the room, and reading only the room level would answer "this
    room never had an executor" for a room whose every seat has one.
    """
    installed = platform_dir(home)
    seats = installed / SEATS_DIR
    return [
        installed / "remote-target.json",
        *(sorted(seats.glob("*/remote-target.json")) if seats.is_dir() else []),
    ]


def target_and_marker(home: Path, resource: str) -> tuple[dict, Path] | None:
    """The executor this room has, and the file that named it.

    The marker is half the answer: it is the installation the executor's own
    programs come from, and a seat's copy is not the room's.
    """
    for marker in target_markers(home):
        if not marker.exists():
            continue
        target = json.loads(marker.read_text())
        if target.get("kind") not in {"private", "device"}:
            continue
        generation = (
            target.get("topic")
            if target["kind"] == "private"
            else target.get("resource_id")
        )
        if generation != str(uuid.UUID(resource)):
            raise RuntimeError("executor belongs to another resource generation")
        return target, marker
    return None


def session_target(home: Path, resource: str) -> dict | None:
    found = target_and_marker(home, resource)
    return found[0] if found is not None else None


def wait_for_launcher(home: Path, state: Path) -> None:
    """On Windows the process that started the executor waits on it instead of
    becoming it, and outlives it by the moment it takes to exit. Until it has,
    it is a process in the room."""
    helper = portable([home])
    if helper is None:
        return
    name = os.path.normcase(str(state))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(
        name in os.path.normcase(row["command"]) for row in helper["processes"]()
    ):
        time.sleep(0.1)


def stop_windows_helper(home: Path, pid: int, expected: str) -> None:
    """`kill` of a helper a Git Bash script started, by the pid it recorded.

    That pid is Git Bash's own (`$!`), not Windows's, and the helper's command
    line names its script the way Git Bash passed it on — `C:/…/x.py` — so both
    are translated before anything is compared or ended. The shell that started
    it waits on it and exits with it; it is in the room until it has.
    """
    helper = portable([home])
    native = helper["windows_pid"](pid) if helper else None
    if helper is None or native is None:
        return
    if os.path.normcase(expected) not in os.path.normcase(
        helper["command_line"](native)
    ):
        return
    helper["terminate"]([native])
    for _ in range(30):
        if helper["windows_pid"](pid) is None:
            return
        time.sleep(0.1)
    raise RuntimeError(Path(expected).stem + " helper has not stopped")


def stop_sandboxed_executor(home: Path) -> None:
    """Ask a sandboxed room's executor to stop, reached through its state
    directory opened without following a link (`bootstrap.executor_state`):
    the room writes its home, and a state that is a link to another room's
    would have this stop that room's executor. A room whose state is not a
    plain directory gets no request; its sandbox is taken down regardless."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(home, flags)
    except OSError:
        return
    for part in (FOOTPRINT_ROOT, "executor"):
        try:
            following = os.open(part, flags, dir_fd=descriptor)
        except OSError:
            # Held open, the room's own home would keep its teardown waiting.
            os.close(descriptor)
            return
        os.close(descriptor)
        descriptor = following
    try:
        if sys.platform == "darwin":
            # No /proc on macOS: the directory the descriptor holds, by the
            # path the kernel resolved when it was opened, as the install
            # named it (`bootstrap.executor_state`). A `/proc/self/fd` name
            # derives a socket that never exists here, and no stop is sent.
            named = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
            state = Path(named.split(b"\0", 1)[0].decode())
        else:
            state = Path(f"/proc/self/fd/{descriptor}")
        runtime = platform_program(home, "remote-execution/runtime.py")
        helper = runpy.run_path(str(runtime))
        if Path(helper["socket_path"](state)).exists():
            # Whatever it answers, the sandbox goes next and everything in it.
            run_command(
                [sys.executable, str(runtime), "stop", "--state", str(state)],
                pass_fds=(descriptor,),
            )
    finally:
        os.close(descriptor)


def mounted_at(path) -> bool:
    """Whether anything is mounted at ``path``, live or dead.

    `os.path.ismount` answers False for a mount whose server has died — every
    stat on it fails and it swallows the error — and that is the one case this
    is asked about: such a mount still occupies the path and refuses a second
    one. The kernel's own list answers for both (`transcript_view._mounted` is
    the same rule on the executor side).
    """
    real = os.path.realpath(str(path))
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return os.path.ismount(real)
    for line in lines:
        fields = line.split()
        if len(fields) > 4 and _mount_point(fields[4]) == real:
            return True
    return False


#: A mount point with a space, tab, newline or backslash arrives octal-escaped
#: (`\040` and the like), and the bind this is asked about can be any path the
#: session host named. `transcript_view` reads the same field the same way.
_MOUNT_ESCAPES = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}


def _mount_point(field: str) -> str:
    for escaped, plain in _MOUNT_ESCAPES.items():
        field = field.replace(escaped, plain)
    return field


def stop_transcript_view(home: Path) -> None:
    """Undo a room's transcript view: its helper, then every mount it made.

    The helper runs on the executor but outside the room's sandbox
    (`bootstrap.start_transcript_view`), so nothing else here reaches it: it is
    not a sandbox process for `stop_executor` to end, and its pid is not in a
    pid file the room wrote. What it binds at the build's path is a view kept
    under the room's own home, and a mount left there is a home that cannot be
    removed at all: `remove_tree` stops at the mountpoint with the filesystem's
    error, in the middle of a delete that has already taken part of the room.

    The helper goes first. A dead mount is what an unreleased one becomes, and
    a dead FUSE mount can refuse the unmount too — so the process that made it
    is given a chance to take it down itself (`transcript_view`'s SIGTERM
    handler), and what it wrote is read rather than walked for: the bind can be
    at a path this process cannot even list.
    """
    # `.cheese`, not `platform_dir`: the helper writes this file where
    # `bootstrap.PLATFORM_DIR` puts it, which is fixed, while `platform_dir`
    # resolves a room's *installation* and can answer `.claude` for one that
    # moved — the two would then disagree and the record would never be found.
    record = home / FOOTPRINT_ROOT / "transcript-view.json"
    try:
        held = json.loads(record.read_text())
    except (OSError, ValueError):
        return
    # The file is under the room's own home, which a sandboxed room can write in
    # full, and `json.loads` accepts any JSON: `[]`, `5` and `"x"` all parse.
    # `.get` on those raises `AttributeError`, and a non-list `mounts` raises
    # `TypeError` in the loop below — either of which aborts the rest of the
    # teardown, since this is now the first step of both `stop_executor` and the
    # `remove` action. A record of the wrong shape is a record this room wrote
    # wrongly: there is nothing here to undo, and saying so is the whole answer.
    if not isinstance(held, dict):
        return
    pid = held.get("pid")
    if isinstance(pid, int) and _is_this_rooms_view(pid, home):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        else:
            for _ in range(30):
                if not PROC.is_dir() or not Path(PROC / str(pid)).exists():
                    break
                time.sleep(0.1)
    left = []
    # Same reason: a `mounts` that is not a list is not a list of paths to
    # release. Read as empty rather than iterated into a `TypeError`.
    mounts = held.get("mounts")
    if not isinstance(mounts, (list, tuple)):
        mounts = ()
    for where in mounts:
        for argv in (
            ["fusermount", "-u", str(where)],
            ["fusermount", "-uz", str(where)],
            ["sudo", "-n", "fusermount", "-u", str(where)],
            ["sudo", "-n", "fusermount", "-uz", str(where)],
        ):
            if not mounted_at(where):
                break
            run_command(argv)
        if mounted_at(where):
            left.append(where)
    if left:
        # Nothing could release them. The record stays so a later attempt —
        # the one a failed `remove_tree` leads to — finds them again; deleting
        # it here would leave the mounts nameless for good.
        #
        # Written the way the helper writes it: through a temporary beside it,
        # and an `OSError` is not raised on. This runs first in `stop_executor`
        # and in the `remove` action, so an exception here would abort the rest
        # of the teardown — `sandbox_release`, `check_no_writers`, `remove_tree`
        # — over a bookkeeping file, and an interrupted in-place write would
        # truncate the record and lose the mounts this branch keeps.
        held["mounts"] = left
        try:
            temporary = Path(str(record) + ".next")
            temporary.write_text(json.dumps(held))
            temporary.replace(record)
        except OSError:
            pass
        return
    try:
        record.unlink(missing_ok=True)
    except OSError:
        # Same reason: a record left behind names mounts that are already
        # released, which the next attempt reads as nothing to do.
        pass


def _is_this_rooms_view(pid: int, home: Path) -> bool:
    """Whether `pid` is this room's transcript-view helper.

    The record is under the room's own home, which a sandboxed room can write
    all of, so its pid is a claim and not a fact: signalling it unverified
    would let a room end another room's process — they run under the same
    account — or a stale pid the kernel has since reused. The command line is
    what says otherwise, the way `stop_windows_helper` and `request_exit` read
    it for the helpers they end: this helper is the one running the release's
    `transcript_view.py` with this room's view directory as its argument.
    """
    view = str(home / FOOTPRINT_ROOT / "transcript-view")
    try:
        argv = Path(PROC / str(pid) / "cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return any(arg.endswith(b"transcript_view.py") for arg in argv) and (
        view.encode() in argv
    )


def stop_executor(home: Path, resource: str) -> None:
    # First, because what it releases is inside the home every later step
    # walks and, at the end, deletes: a mount left at the view's own directory
    # stops `remove_tree` with the filesystem's error, partway through a room
    # that is already half gone.
    stop_transcript_view(home)
    sandboxed = sandbox_release(home) is not None
    installed = platform_dir(home)
    marker = installed / "execution-owner.json"
    if sandboxed:
        # The room wrote the marker and the state, so neither decides anything.
        stop_sandboxed_executor(home)
    elif marker.exists():
        if json.loads(marker.read_text())["resource"] != str(uuid.UUID(resource)):
            raise RuntimeError("execution marker names another resource generation")
        runtime = platform_program(home, "remote-execution/runtime.py")
        state = installed / "executor"
        # A home restored from its archive onto a fresh host keeps its links
        # into the release it last ran from, which this host never staged:
        # until the room is prepared here again, no executor of it can be
        # running here, and there is nothing to stop.
        if (
            runtime.exists()
            and Path(runpy.run_path(str(runtime))["socket_path"](state)).exists()
        ):
            result = run_command(
                [sys.executable, str(runtime), "stop", "--state", str(state)]
            )
            if result.returncode:
                raise RuntimeError("executor has not stopped: " + result.stderr)
            if sys.platform == "win32":
                wait_for_launcher(home, state)
    if sandboxed:
        # Everything the room started is in its sandbox, the preview and
        # tunnel helpers too, and goes with it; the pid files below are the
        # room's to write and number processes in its own namespace. Where
        # the helper gave the sandbox a cgroup, that is killed; a machine a
        # person enrolled has neither the helper nor the cgroup, and what the
        # room left running there outlives its executor under the sandbox's
        # first process, which the install recorded and this ends.
        if Path(SANDBOX_HOST).exists():
            result = run_command(["sudo", "-n", SANDBOX_HOST, "down", home.name])
            if result.returncode:
                raise RuntimeError("sandbox has not stopped: " + result.stderr)
        runner = runpy.run_path(str(platform_program(home, "cheese-environment.py")))
        # Not a compatibility path: rooms keep running from the release they
        # started with. A release without `end_sandbox` predates sandboxes on
        # enrolled machines and only ever started one on a cloud host, inside
        # the helper's cgroup that `down` just took down.
        end_sandbox = runner.get("end_sandbox")
        if end_sandbox is not None:
            end_sandbox(home)
        return
    # Both helpers can outlive the agent, including launches without an executor.
    # The tunnel helper's files are per SEAT — a room may seat several agents and
    # each has its own helper, because one helper carries one credential and the
    # credential names the teammate — so its markers live under each seat's
    # directory. The room-level one is the shape from before seats had
    # directories of their own, and a machine that still has one is worth
    # stopping too.
    markers = [home / ".cheese" / "cheese-preview.pid"]
    markers += sorted((home / ".cheese" / SEATS_DIR).glob("*/cheese-tunnel.pid"))
    markers.append(home / ".cheese" / "cheese-tunnel.pid")
    for marker in markers:
        name = marker.name[: -len(".pid")]
        if not marker.exists():
            continue
        pid = int(marker.read_text())
        expected = str(home / ".cheese" / (name + ".py"))
        if sys.platform == "win32":
            stop_windows_helper(home, pid, expected)
            continue
        # -ww: ps otherwise truncates each line to the terminal width, and a
        # long tmp path then fails the substring check below without a word.
        command = run_command(["ps", "-ww", "-p", str(pid), "-o", "args="])
        if expected in command.stdout:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                continue
            for _ in range(30):
                command = run_command(["ps", "-ww", "-p", str(pid), "-o", "args="])
                if expected not in command.stdout:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(name + " helper has not stopped")


def main() -> None:
    # `room` names the room whose transcripts this device keeps, or is "-" on a
    # device that does not keep them.
    action, project, resource, cleanup, room, *tasks = sys.argv[1:]
    home, work = resource_paths(Path.home(), project, resource)
    found = target_and_marker(home, resource)
    executor = found[0] if found is not None else None
    if action == "prepare":
        # A timed-out command may arrive after reopening. Once this operation
        # confirmed quiescence, all its delayed retries become read-only.
        directory = (
            Path.home()
            / os.environ.get("CHEESE_PROFILE_ROOT", FOOTPRINT_ROOT)
            / "cleanup"
            / str(uuid.UUID(cleanup))
        )
        directory.mkdir(parents=True, exist_ok=True)
        receipt = directory / (str(uuid.UUID(resource)) + ".ready")
        with receipt.with_suffix(".lock").open("a") as receipt_lock:
            lock(receipt_lock)
            if not receipt.exists() or receipt.read_text() != "ready\n":
                # The first attempt stamps when the room was asked to leave;
                # every later attempt measures the grace from that stamp, not
                # from itself, so a room is never ended on its first refusal.
                requested = receipt.with_suffix(".requested")
                if not requested.exists():
                    requested.write_text(str(time.time()))
                try:
                    request_exit(home, work, receipt_lock.fileno())
                    stop_executor(home, resource)
                    check_no_writers([home, work])
                except StillRunning:
                    if time.time() - float(requested.read_text()) < FORCE_AFTER_S:
                        raise
                    end_holders([home, work])
                    request_exit(home, work, receipt_lock.fileno())
                    stop_executor(home, resource)
                    check_no_writers([home, work])
                temporary = receipt.with_suffix(".tmp")
                with temporary.open("w") as output:
                    output.write("ready\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, receipt)
                sync_directory(directory)
        print(json.dumps({"ready": True}))
    elif action == "keep-room":
        # `cleanup` and `room` are unused; the URL to send to is in the
        # environment, which no other user on the machine can read.
        kept = keep_room_files(
            home, executor, os.environ.get("CHEESE_KEEP_URL", ""), resource
        )
        print(json.dumps({"kept": kept}))
    elif action == "remove":
        # The caller recorded this generation before granting deletion permission.
        # A reopened room uses another UUID, even on the same physical device.
        #
        # The view goes first, and for its own reason: `prepare` normally
        # stopped it already, but a room deleted without one still has the
        # helper and its mount, and both are inside what is about to be
        # walked and removed.
        stop_transcript_view(home)
        check_no_writers([home, work])
        # A room from before rooms had an executor sends its files to the
        # bucket first, unless they were sent already (`KEPT_ALREADY`).
        url = os.environ.get("CHEESE_KEEP_URL", "")
        kept = (
            None
            if url == KEPT_ALREADY
            else keep_room_files(home, executor, url, resource)
        )
        if found is not None and found[0]["kind"] == "private":
            helper = runpy.run_path(str(private_helper(home, found[1])))
            helper["release"](found[0])
        # Whether or not the room has a home here: a Codex or pi room's seats
        # leave only their harness state on the session host.
        if room != "-":
            retain_transcripts(
                home,
                retained_transcripts(Path.home(), project, room, resource),
                harness_sessions(Path.home(), project, resource),
            )
        for path in (work, home, resource_tmp(resource)):
            if path is not None and path.exists():
                remove_tree(path)
        # Last: until the room's directories are gone, they are a sandboxed
        # room's to have written.
        sandbox_marker(home).unlink(missing_ok=True)
        print(json.dumps({"removed": True, "kept": kept}))
    elif action == "tasks":
        # `tasks` are closed tasks of this room; `cleanup` and `room` are unused.
        print(json.dumps(remove_task_checkouts(home, tasks)))
    elif action == "send-transcripts":
        # The URL to send to is in the environment, as for `keep-room`; the
        # platform checks the bucket against the answer before anything here
        # may expire.
        sent = send_transcripts(
            retained_transcripts(Path.home(), project, room, resource),
            os.environ.get("CHEESE_KEEP_URL", ""),
        )
        print(json.dumps({"sent": sent}))
    elif action == "expire":
        # Run only once the platform found the archive in the bucket as this
        # machine sent it (`retire._expire_transcripts`).
        expire_transcripts(retained_transcripts(Path.home(), project, room, resource))
        print(json.dumps({"expired": True}))
    else:
        raise ValueError("unknown cleanup action")


if __name__ == "__main__":
    try:
        main()
    except StillRunning as waiting:
        print(waiting, file=sys.stderr)
        sys.exit(STILL_RUNNING_EXIT)
