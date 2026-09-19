"""Device-side checks and deletion, restricted to a recorded resource directory."""

import fcntl
import hashlib
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Every directory the platform has installed a room's own files into, the
# current one first. Tearing a room down reads what preparing it wrote, and a
# room prepared under an earlier root still has all of it where that launcher
# put it — it does not move until something prepares the room again, and a room
# being deleted never will. Reading only where we would install today answers
# "this room never had an executor" for a room that has one running, and the
# answer is acted on: the detached daemon is left alive under a home that is
# then removed from under it, the private seat it holds is never released, and
# publication is checked on a branch meant for rooms without an executor.
PLATFORM_DIRS = (".cheese", ".claude")

# How long an archived room's processes get to leave on their own before the
# cleanup ends them. The graceful path below — `/exit` typed into the agent's
# terminal, the executor asked to stop — is what the first attempts do, and a
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
    user's socket, a session that does not identify this resource, unpublished
    work), and no amount of waiting makes those go away, so nothing escalates
    on them.
    """


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


def run_command(
    argv: list[str], *, cwd: Path | None = None, pass_fds: tuple[int, ...] = ()
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, check=False, pass_fds=pass_fds
    )


def check_published(work: Path, *, canonical: bool = False) -> None:
    if not work.exists():
        return
    if not (work / ".git").exists():
        if any(work.iterdir()):
            raise RuntimeError("nonempty checkout has no Git publication record")
        return
    dirty = run_command(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=work
    )
    if dirty.returncode or dirty.stdout.strip():
        raise RuntimeError("checkout has unpublished working-tree changes")
    if not canonical:
        check_published_commits(work, include_head=True)


def check_published_commits(repo: Path, *, include_head: bool = False) -> None:
    unpublished = run_command(
        [
            "git",
            "rev-list",
            "--branches",
            *(["HEAD"] if include_head else []),
            "--not",
            "--remotes=origin",
            "--glob=refs/cheese/published/*",
        ],
        cwd=repo,
    )
    if unpublished.returncode or unpublished.stdout.strip():
        raise RuntimeError("checkout has unpublished commits")


def check_no_writers(paths: list[Path]) -> None:
    """Refuse if anything holds a file or a working directory under these.

    ONE lsof call, never one per path. `lsof +D` spends about two seconds
    walking every process's descriptors before it so much as looks at the
    directory, so its cost is per INVOCATION and not per tree: measured
    2026-09-18, an empty directory costs the same 1.97s as a repository, five
    directories in one call cost 2.07s, and the same five in five calls cost
    10.14s. A sweep over 102 rooms was therefore 3.4 minutes of lsof alone.

    Nothing is given up by batching: the message never named which path it was,
    because for every caller the answer is the same either way — do not delete.
    """
    present = [str(path) for path in paths if path.exists()]
    if not present:
        return
    result = run_command(["lsof", "-t", "+D", *present])
    if result.stdout.strip():
        raise StillRunning(
            "resource still has processes holding files or working directories"
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


def check_resource_publication(home: Path, work: Path) -> None:
    """Check both legacy checkouts and task worktrees before deleting a home."""
    check_published(work)
    check_published(home / "room")
    tasks = home / ".cheese/tasks"
    if tasks.is_symlink():
        raise RuntimeError("task storage is a symlink")
    if tasks.exists():
        for task in tasks.iterdir():
            if task.is_symlink() or not task.is_dir():
                raise RuntimeError("unrecognized entry in task storage")
            check_published(task)
    # A removed checkout can leave the only copy of a branch in the bare cache.
    repositories = home / ".cheese/repositories"
    for repo in repositories.glob("*.git"):
        check_published_commits(repo)


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
        function(name)

    shutil.rmtree(path, onerror=retry_unlink)


def request_exit(home: Path, work: Path, lock_fd: int) -> None:
    marker = home / ".cheese/environment-session.json"
    if not marker.exists():
        return
    socket, name, *_ = json.loads(marker.read_text())
    # cksum consumes stdin below; do not trust a session name from an arbitrary file.
    expected = set()
    for directory in (work, home / "room"):
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
            "#{pane_id} #{pane_dead}",
        ],
        pass_fds=(lock_fd,),
    )
    if panes.returncode or not panes.stdout.strip():
        raise RuntimeError("could not inspect the resource's terminal panes")
    live = [
        line.split()[0] for line in panes.stdout.splitlines() if line.split()[1] != "1"
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
    for pane in live:
        for keys in (["-l", "/exit"], ["Enter"]):
            sent = run_command(
                ["tmux", "-S", socket, "send-keys", "-t", pane, *keys],
                pass_fds=(lock_fd,),
            )
            if sent.returncode:
                raise RuntimeError("could not request a graceful session exit")
    raise StillRunning("waiting for the agent to exit and finish transcript writes")


def resource_paths(
    machine_home: Path, project: str, resource: str
) -> tuple[Path, Path]:
    project, resource = str(uuid.UUID(project)), str(uuid.UUID(resource))
    paths = tuple(
        machine_home / ".cheese" / kind / project / resource
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


def check_transcripts(home: Path, receipts: list[dict]) -> None:
    expected = {receipt["source"]: receipt for receipt in receipts}
    found = {}
    for path in (home / ".claude/projects").rglob("*.jsonl"):
        # The same two cases the uploader skips (event_drain.collect_transcripts),
        # skipped for the same reason: what was uploaded is the set this is
        # checked against. Claude Code links a resumed session's subagent
        # transcripts to the original session's files — a link inside the same
        # home, whose target is uploaded under its own path — and refusing it
        # here left a room's cleanup failing every minute for five days
        # (operation a12198c1, 2026-09-13 to 09-19) over a file that was never
        # in the receipts to begin with.
        if path.is_symlink() or not path.resolve().is_relative_to(home.resolve()):
            continue
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as original:
            while part := original.read(1024 * 1024):
                digest.update(part)
                size += len(part)
        found[str(path.relative_to(home))] = (size, digest.hexdigest())
    if found != {
        source: (item["size"], item["sha256"]) for source, item in expected.items()
    }:
        raise RuntimeError("transcripts changed after durable confirmation")
    if any((home / ".cheese/cheese-spool").glob("[0-9]*")):
        raise RuntimeError("hook events still await backend acknowledgement")


def session_target(home: Path, resource: str) -> dict | None:
    marker = platform_dir(home) / "remote-target.json"
    if not marker.exists():
        return None
    target = json.loads(marker.read_text())
    if target.get("kind") not in {"private", "device"}:
        return None
    generation = (
        target.get("topic")
        if target["kind"] == "private"
        else target.get("resource_id")
    )
    if generation != str(uuid.UUID(resource)):
        raise RuntimeError("executor belongs to another resource generation")
    return target


def stop_executor(home: Path, resource: str) -> None:
    installed = platform_dir(home)
    marker = installed / "execution-owner.json"
    if marker.exists():
        if json.loads(marker.read_text())["resource"] != str(uuid.UUID(resource)):
            raise RuntimeError("execution marker names another resource generation")
        runtime = installed / "remote-execution/runtime.py"
        state = installed / "executor"
        helper = runpy.run_path(str(runtime))
        if Path(helper["socket_path"](state)).exists():
            result = run_command(
                [sys.executable, str(runtime), "stop", "--state", str(state)]
            )
            if result.returncode:
                raise RuntimeError("executor has not stopped: " + result.stderr)
    # Both helpers can outlive the agent, including launches without an executor.
    for name in ("cheese-preview", "cheese-tunnel"):
        marker = home / ".cheese" / (name + ".pid")
        if not marker.exists():
            continue
        pid = int(marker.read_text())
        command = run_command(["ps", "-p", str(pid), "-o", "args="])
        expected = str(home / ".cheese" / (name + ".py"))
        if expected in command.stdout:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                continue
            for _ in range(30):
                command = run_command(["ps", "-p", str(pid), "-o", "args="])
                if expected not in command.stdout:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError(name + " helper has not stopped")


def main() -> None:
    action, project, resource, cleanup = sys.argv[1:]
    home, work = resource_paths(Path.home(), project, resource)
    executor = session_target(home, resource)
    if action == "prepare":
        # A timed-out command may arrive after reopening. Once this operation
        # confirmed quiescence, all its delayed retries become read-only.
        directory = Path.home() / ".cheese/cleanup" / str(uuid.UUID(cleanup))
        directory.mkdir(parents=True, exist_ok=True)
        receipt = directory / (str(uuid.UUID(resource)) + ".ready")
        with receipt.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not receipt.exists() or receipt.read_text() != "ready\n":
                # The first attempt stamps when the room was asked to leave;
                # every later attempt measures the grace from that stamp, not
                # from itself, so a room is never ended on its first refusal.
                requested = receipt.with_suffix(".requested")
                if not requested.exists():
                    requested.write_text(str(time.time()))
                try:
                    request_exit(home, work, lock.fileno())
                    stop_executor(home, resource)
                    check_no_writers([home, work])
                except StillRunning:
                    if time.time() - float(requested.read_text()) < FORCE_AFTER_S:
                        raise
                    end_holders([home, work])
                    request_exit(home, work, lock.fileno())
                    stop_executor(home, resource)
                    check_no_writers([home, work])
                temporary = receipt.with_suffix(".tmp")
                with temporary.open("w") as output:
                    output.write("ready\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, receipt)
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        print(json.dumps({"ready": True}))
    elif action == "publication":
        # Central workspaces hold generated context. Project publication is
        # checked on the separately inventoried execution device.
        if executor is None:
            check_resource_publication(home, work)
        print(json.dumps({"published": True}))
    elif action == "remove":
        # The caller recorded this generation before granting deletion permission.
        # A reopened room uses another UUID, even on the same physical device.
        check_no_writers([home, work])
        if executor is None:
            check_resource_publication(home, work)
        if home.exists():
            check_transcripts(
                home, json.loads(os.environ["CHEESE_TRANSCRIPT_RECEIPTS"])
            )
        if executor is not None and executor["kind"] == "private":
            helper = runpy.run_path(
                str(platform_dir(home) / "remote-execution/private.py")
            )
            helper["release"](executor)
        for path in (work, home):
            if path.exists():
                remove_tree(path)
        print(json.dumps({"removed": True}))
    else:
        raise ValueError("unknown cleanup action")


if __name__ == "__main__":
    main()
