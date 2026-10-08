"""Device-side checks and deletion, restricted to a recorded resource directory."""

# Annotations stay unevaluated: this runs on whatever Python 3 the machine has,
# macOS's own 3.9 among them, which cannot evaluate `dict | None`.
from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import stat
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
# the cleanup that retained them deletes them (topic/retire.py sets how long).
TRANSCRIPTS_ROOT = "transcripts"

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


def run_command(
    argv: list[str], *, cwd: Path | None = None, pass_fds: tuple[int, ...] = ()
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=False, pass_fds=pass_fds
    )


def sandbox_marker(home: Path) -> Path:
    return Path.home() / FOOTPRINT_ROOT / SANDBOXES / home.parent.name / home.name


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

    ONE lsof call, never one per path. `lsof +D` spends about two seconds
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
    if sys.platform == "win32":
        pids = windows_holders([Path(path) for path in present])
        if pids:
            raise StillRunning(
                "resource still has processes holding files or working "
                "directories: " + ", ".join(str(pid) for pid in pids)
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
    result = run_command(["lsof", "-t", "+D", *present])
    own = {os.getpid(), os.getppid()}
    return [
        int(line)
        for line in result.stdout.split()
        if line.isdigit() and int(line) not in own
    ]


def end_holders(paths: list[Path]) -> None:
    """Terminate whatever still holds the room, gently first, then not.

    lsof names the processes; each gets SIGTERM, ten seconds to act on it,
    then SIGKILL. A tmux pane whose process dies reads as dead to the next
    `request_exit`, which closes the session the ordinary way.
    """
    pids = holders(paths)
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
            pids = [pid for pid in pids if pid in set(holders(paths))]
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
    scratch = Path.home() / FOOTPRINT_ROOT / "cleanup"
    scratch.mkdir(parents=True, exist_ok=True)
    archive = scratch / (str(uuid.UUID(resource)) + ".room.tar.gz")
    try:
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(room, arcname=CHECKOUT_DIR, recursive=True)
        digest = hashlib.md5()  # noqa: S324 — the bucket's ETag of one PUT
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
    finally:
        archive.unlink(missing_ok=True)


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
        machine_home / FOOTPRINT_ROOT / kind / project / resource
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


def resource_tmp(resource: str) -> Path:
    """The room's TMPDIR, where the launcher pointed its sessions: under this
    account's own directory in `/var/tmp`, by resource alone, so a socket made
    inside it still fits a socket path."""
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
        / FOOTPRINT_ROOT
        / TRANSCRIPTS_ROOT
        / project
        / room
        / (str(uuid.UUID(resource)) + ".tar.gz")
    )


def retain_transcripts(home: Path, archive: Path) -> None:
    """Compress the home's Claude Code session files before the home goes.

    `.claude/projects` holds every session of the room — the main transcript
    and each subagent's beside it. A link is stored as a link and never
    followed: Claude Code links a resumed session's subagent files to the
    originals in the same tree, and a link out of the home is not ours to read.
    Written under a temporary name and renamed, so an archive under the final
    name is always whole; a retry after a crash rewrites it from the same home.
    """
    source = home / ".claude/projects"
    if source.is_symlink() or not source.is_dir():
        return
    archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = archive.with_name(archive.name + ".part")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with (
            os.fdopen(descriptor, "wb") as output,
            tarfile.open(fileobj=output, mode="w:gz") as bundle,
        ):
            bundle.add(source, arcname="projects", recursive=True)
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


def stop_executor(home: Path, resource: str) -> None:
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
        directory = Path.home() / FOOTPRINT_ROOT / "cleanup" / str(uuid.UUID(cleanup))
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
        if room != "-" and home.exists():
            retain_transcripts(
                home, retained_transcripts(Path.home(), project, room, resource)
            )
        for path in (work, home, resource_tmp(resource)):
            if path.exists():
                remove_tree(path)
        # Last: until the room's directories are gone, they are a sandboxed
        # room's to have written.
        sandbox_marker(home).unlink(missing_ok=True)
        print(json.dumps({"removed": True, "kept": kept}))
    elif action == "tasks":
        # `tasks` are closed tasks of this room; `cleanup` and `room` are unused.
        print(json.dumps(remove_task_checkouts(home, tasks)))
    elif action == "expire":
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
