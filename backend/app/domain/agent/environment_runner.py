"""Standard-library program shipped to the selected machine before agent launch.

The launcher invokes this inside the selected execution boundary. A lock prevents
concurrent installers. At exec the lock is released; tmux owns agent reuse.
"""

import functools
import json
import os
import runpy
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

if sys.platform != "win32":
    import fcntl


@functools.cache
def portable():
    """The Windows primitives; this file sits at the release's root, beside
    the executor's own directory."""
    return runpy.run_path(
        str(Path(__file__).resolve().parent / "remote-execution/portable.py")
    )


def lock(file, blocking=True):
    if sys.platform == "win32":
        portable()["lock"](file, blocking)
    else:
        fcntl.flock(file, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)


def terminate(pid, started=None):
    """End a process and what it started: SIGKILL to its group, or on Windows
    its tree. `started` finds the tree of one that has already exited."""
    if sys.platform == "win32":
        portable()["terminate_tree"](pid, started)
    else:
        os.killpg(pid, signal.SIGKILL)


def now():
    # This shipped helper also runs with macOS's system Python 3.9.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def pid_namespace():
    """This process's pid namespace, on Linux; None elsewhere."""
    if sys.platform != "linux":
        return None
    return os.readlink("/proc/self/ns/pid")


def visible_pid(data):
    """The pid a status names, numbered as this process's namespace numbers it,
    or None when nothing alive answers to it.

    A sandboxed room's runner (`bootstrap.sandbox_argv`) has a pid namespace
    of its own and records the pid it has there. Outside, that number is some
    other process's, so the process is found by its namespace and the pid it
    has in it."""
    pid, namespace = data["pid"], data.get("pid_namespace")
    if namespace is None or namespace == pid_namespace():
        return pid
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(entry / "ns/pid") != namespace:
                continue
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("NSpid:"):
                    if int(line.split()[-1]) == pid:
                        return int(entry.name)
                    break
        except OSError:
            continue
    return None


# Where a sandboxed room's processes are, under the machine's cgroup root —
# a copy of `sandbox_host.CGROUP`'s name, held to it by test_footprint_root.py.
SANDBOX_CGROUP = "cheese-sandboxes"


def ours_to_signal(pid):
    """Whether the reset or cancel may signal `pid`, a status file's.

    A sandboxed room writes its status itself (`CHEESE_SANDBOXED`, set by
    `device_provider.environment_status`), so the pid it names could be any
    process of the machine's connector user; only one in the room's own
    sandbox, by its cgroup (`sandbox_host.py`), is the room's to signal."""
    if os.environ.get("CHEESE_SANDBOXED") != "1":
        return True
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    except OSError:
        return False
    own = f"/{SANDBOX_CGROUP}/{Path.home().name}"
    return any(
        line.startswith("0::") and (line[3:] == own or line[3:].startswith(own + "/"))
        for line in lines
    )


def process_identity(pid, *, reference=None):
    # Keep recognizing status files written by older helpers, including during
    # reset. New Linux records avoid spawning ps on every readiness poll.
    if sys.platform == "win32":
        return portable()["identity"](pid)
    if sys.platform == "linux" and (
        reference is None or reference.startswith("linux:")
    ):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except (FileNotFoundError, ProcessLookupError):
            return ""
        # stat field 22 is starttime; fields here begin at field 3 (state).
        return f"linux:{boot_id}:{fields[19]}"
    return subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def tool_prefix():
    """Where this project's tools are installed, shared by every room of it on
    this machine — or None when the machine has no store.

    The setup script's only addressable output is `$HOME`, and a room's `$HOME`
    has to be its own (the session's config, transcripts and credentials live
    under it). Those two facts together are why every room of a project
    downloaded and kept its own copy of the same toolchain: measured on dev
    2026-09-17, one Node 22 per room, ~254MB each across 228 rooms, and five
    rooms fetching the same 54MB tarball within three hours.

    So the script gets a HOME of its own instead. It is the platform's to give:
    `HOME` is already a reserved variable a project may not set
    (`project/environment.py`), and the product already calls this script
    「安装工具」 over a configuration it says the rooms share.

    The agent keeps the room's HOME. Only the installer's moves.
    """
    store = os.environ.get("CHEESE_STORE", "")
    if not store or not os.path.isabs(store):
        # No store means no shared prefix, and every tool falls back to the
        # room's own HOME — exactly where it was before this existed.
        return None
    return Path(store) / "env"


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


# The platform's directories inside this room's home, the current one first. A
# copy of `place.session_platform_dirs()`, not a second answer:
# this file is shipped to the machine as a standalone stdlib-only program, with
# nothing of ours importable beside it, so it carries the names; the copy is
# held to the original by test_footprint_root.py.
#
# Read rather than assumed, and not read off our own path either: a relaunch
# writes this file under the current root at the top of the launch, while the
# executor it is about to replace goes on running under the previous one until
# the bootstrap stops it. A reset in that window has to stop the executor that
# is actually running — looking only where this copy happens to sit finds
# nothing, skips the stop, and then kills the terminal out from under a daemon
# that goes on writing the status file this reset is about to read.
PLATFORM_DIRS = (".cheese", ".claude")


def platform_dir(home):
    """Where this room's platform files actually are."""
    for name in PLATFORM_DIRS:
        directory = home / name
        if (directory / "executor").exists():
            return directory
    return home / PLATFORM_DIRS[0]


def read_beneath(directory, name, offset=None):
    """The bytes of `directory / name`, opening neither through a link; from
    `offset` bytes before its end when given. A sandboxed room writes its
    environment directory and this reads it from outside the sandbox too,
    where a link would reach the owner's files or another room's."""
    if sys.platform == "win32":
        with (directory / name).open("rb") as stream:
            if offset is not None:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - offset))
            return stream.read()
    folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
    finally:
        os.close(folder)
    with os.fdopen(file, "rb") as stream:
        if offset is not None:
            stream.seek(max(0, os.fstat(file).st_size - offset))
        return stream.read()


def read_status(directory: Path) -> dict:
    try:
        data = json.loads(read_beneath(directory, "status.json"))
    except FileNotFoundError:
        return {"state": "pending"}
    if data["state"] in ("preparing", "ready"):
        pid = visible_pid(data)
        if pid is not None and pid != data["pid"]:
            data = {**data, "pid": pid, "pid_namespace": pid_namespace()}
        if (
            pid is None
            or process_identity(pid, reference=data["process_identity"])
            != data["process_identity"]
        ):
            if data["state"] == "ready":
                data = {**data, "state": "stopped"}
            else:
                data = {
                    **data,
                    "state": "failed",
                    "error": "preparation process exited",
                }
    name = data.get("log_file")
    if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
        return data
    try:
        data["log"] = read_beneath(directory, name, offset=32768).decode(
            errors="replace"
        )
    except OSError:
        pass
    return data


def wait_status(directory: Path) -> dict:
    """Observe short preparations locally instead of waiting for another RPC."""
    # Cover short launches in one RPC; returning at 0.5s adds a backend sleep
    # and another interpreter startup when readiness lands just after it.
    deadline = time.monotonic() + 2
    while True:
        status = read_status(directory)
        if status["state"] not in {"pending", "preparing"}:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return status
        time.sleep(min(0.05, remaining))


def run(config, directory, command, *, adopt=None, task_work=None):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    room_lock = (directory / "lock").open("a")
    try:
        lock(room_lock, blocking=False)
    except BlockingIOError:
        # The caller reconciles the existing attempt rather than queuing a
        # second installer that could mutate an environment already in use.
        return 75
    attempt = uuid.uuid4().hex
    log_name = f"{attempt}.log"
    state = {
        "state": "preparing",
        "stage": "setup",
        "revision": config["revision"],
        "attempt": attempt,
        "pid": os.getpid(),
        "pid_namespace": pid_namespace(),
        "process_identity": process_identity(os.getpid()),
        "started_at": now(),
        "finished_at": None,
        "exit_code": None,
        "log_file": log_name,
    }
    child = None
    child_started = None

    def cancel(signum, _frame):
        if child is not None and sys.platform == "win32":
            terminate(child.pid, child_started)
            child.wait()
        elif child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                terminate(child.pid)
                child.wait()
            except ProcessLookupError:
                pass
        state.update(
            state="failed",
            error="preparation cancelled",
            exit_code=128 + signum,
            finished_at=now(),
        )
        write_json(directory / "status.json", state)
        raise SystemExit(128 + signum)

    previous_term = signal.signal(signal.SIGTERM, cancel)
    previous_int = signal.signal(signal.SIGINT, cancel)
    prefix = tool_prefix()
    environment = dict(os.environ)
    environment.update(config["variables"])
    environment.pop("CHEESE_ENVIRONMENT", None)
    # The prefix first: a room that installed its own copy before the prefix
    # existed still has one, and the shared one is the answer from now on.
    bins = [str(Path.home() / ".local/bin")]
    if prefix is not None:
        bins.insert(0, str(prefix / ".local/bin"))
    environment["PATH"] = os.pathsep.join([*bins, environment["PATH"]])
    model_proxy = environment.pop("CHEESE_MODEL_PROXY", "")
    script_environment = dict(environment)
    if model_proxy:
        # This proxy admits only model service hosts. Package downloads use
        # ordinary machine egress; the agent retains its metered model route.
        script_environment.pop("HTTPS_PROXY", None)
    # `setup` installs tools and is the project's, so it runs in the project's
    # prefix. `startup` prepares one task's checkout and writes into that
    # checkout; it keeps the room's HOME, because giving it a shared one would
    # let two tasks of one project race in a directory neither of them locks.
    shared_lock = None
    receipt = directory / "initialized.json"
    if prefix is not None and task_work is None:
        script_environment["HOME"] = str(prefix)
        if sys.platform == "win32":
            script_environment["USERPROFILE"] = str(prefix)
        prefix_state = prefix / ".cheese-environment"
        prefix_state.mkdir(parents=True, exist_ok=True, mode=0o700)
        shared_lock = prefix_state / "lock"
        receipt = prefix_state / "initialized.json"
    initialized = json.loads(receipt.read_text()) if receipt.exists() else None
    with (directory / log_name).open("a", buffering=1) as log:
        write_json(directory / f"{attempt}.json", config)
        if task_work is None:
            # The agent receives no CHEESE_ENVIRONMENT. Task preparation reads
            # the room's pinned configuration after the agent has started.
            write_json(directory / "config.json", config)
        write_json(directory / "status.json", state)
        try:
            # Installs must never land in another cwd, so the room directory has
            # to be there before any script runs. It is NOT a repository: a room
            # holds the conversation and nothing else, and a task's checkout is
            # made next to it by `cheese worktree`. Asking git about this
            # directory therefore answers no useful question — and while it was
            # still asked, every room on a deployment failed preparation, which
            # means the agent was never exec'd below and the room went silent.
            work = Path(task_work or os.environ["CHEESE_WORK"]).resolve()
            if not work.is_dir():
                raise RuntimeError("work directory is missing")
            stages = (
                (("setup", "setup_script"),)
                if task_work is None
                else (("startup", "startup_script"),)
            )
            for stage, key in stages:
                state["stage"] = stage
                write_json(directory / "status.json", state)
                if stage == "setup" and initialized == config["revision"]:
                    log.write(f"{now()} setup: reused successful initialization\n")
                    continue
                installing = None
                try:
                    if stage == "setup" and shared_lock is not None:
                        # One prefix, many rooms. The room lock above says "this
                        # room is not preparing twice"; this one says "this
                        # project's tools are not being installed twice into the
                        # same directory", which is a different claim and the
                        # one that matters once the directory is shared.
                        installing = shared_lock.open("a")
                        try:
                            lock(installing, blocking=False)
                        except BlockingIOError:
                            # WAIT, never fail: the other room is doing this
                            # room's work. `status.json` already says preparing
                            # / setup, so the room reads as 「正在安装工具」
                            # throughout — which is true, just not by us.
                            log.write(
                                f"{now()} setup: another room of this project is"
                                " installing its tools; waiting\n"
                            )
                            lock(installing)
                        # It may have just finished the very revision we want.
                        current = (
                            json.loads(receipt.read_text())
                            if receipt.exists()
                            else None
                        )
                        if current == config["revision"]:
                            log.write(
                                f"{now()} setup: installed by another room of"
                                " this project\n"
                            )
                            continue
                    script = directory / f"{attempt}-{stage}.sh"
                    script.write_text(config[key])
                    log.write(f"{now()} {stage}: started\n")
                    if config[key].strip():
                        child = subprocess.Popen(
                            (
                                portable()["which"](["bash", "-e", str(script)])
                                if sys.platform == "win32"
                                else ["bash", "-e", str(script)]
                            ),
                            cwd=work,
                            env=script_environment,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        if sys.platform == "win32":
                            child_started = portable()["created"](child.pid)
                        try:
                            code = child.wait(timeout=1800)
                        except subprocess.TimeoutExpired:
                            cancel(signal.SIGTERM, None)
                        if code:
                            # Failed scripts must not leave background installers.
                            if sys.platform == "win32":
                                terminate(child.pid, child_started)
                            else:
                                try:
                                    os.killpg(child.pid, signal.SIGTERM)
                                except ProcessLookupError:
                                    pass
                            state["exit_code"] = code
                            raise RuntimeError(
                                f"{stage} script exited with status {code}"
                            )
                        child = None
                    log.write(f"{now()} {stage}: completed\n")
                    if stage == "setup":
                        write_json(receipt, config["revision"])
                finally:
                    if installing is not None:
                        installing.close()
            if adopt is not None:
                # Bind only after tool setup succeeds. Status must follow the
                # existing agent, not this short-lived preparation process.
                pid = adopt(environment, work)
                identity = process_identity(pid)
                if not identity:
                    raise RuntimeError("prepared agent exited during adoption")
                state.update(pid=pid, process_identity=identity)
            state.update(
                state="ready" if task_work is None else "complete",
                stage="complete",
                exit_code=0,
                finished_at=now(),
            )
            write_json(directory / "status.json", state)
            log.write(f"{now()} ready\n")
            if adopt is not None or task_work is not None:
                return 0
            # Exec keeps the PID stable for status inspection. The project
            # variables are applied to the agent as well as both scripts.
            os.chdir(work)
            if sys.platform == "win32":
                # Windows has no exec that keeps the pid, so this process stays
                # as the one the status names, and lives exactly as long as the
                # command. The lock goes now, as an exec would have closed it.
                room_lock.close()
                signal.signal(signal.SIGTERM, previous_term)
                signal.signal(signal.SIGINT, previous_int)
                return subprocess.call(command, env=environment)
            os.execvpe(command[0], command, environment)
        except Exception as exc:
            state.update(state="failed", error=str(exc), finished_at=now())
            if state["exit_code"] is None:
                state["exit_code"] = 1
            log.write(f"{now()} failed: {exc}\n")
            write_json(directory / "status.json", state)
            return state["exit_code"]
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
            room_lock.close()
    return 0


if __name__ == "__main__":
    root = Path.home() / ".cheese-environment"
    if len(sys.argv) == 3 and sys.argv[1] == "task":
        work = Path(sys.argv[2]).resolve()
        config = json.loads((root / "config.json").read_text())
        directory = root / "tasks" / work.name
        code = run(config, directory, [], task_work=work)
        if code:
            status = read_status(directory)
            print(
                status.get("log", status.get("error", "Task preparation is busy")),
                file=sys.stderr,
            )
            print(f"Task environment log: {directory}", file=sys.stderr)
        raise SystemExit(code)
    elif sys.argv[1:] == ["reset"]:
        status = read_status(root)
        if status["state"] == "preparing":
            raise SystemExit("environment is still preparing")
        installed = platform_dir(Path.home())
        executor = installed / "execution-owner.json"
        if executor.exists():
            if json.loads(executor.read_text())["resource"] != Path.home().name:
                raise SystemExit("executor belongs to another room resource")
            # The runtime of the release this runner came from, which is the
            # one a sandboxed room cannot write (`device_provider.
            # environment_status` picks it), not the room's own copy.
            subprocess.run(
                [
                    sys.executable,
                    str(
                        Path(__file__).resolve().parent / "remote-execution/runtime.py"
                    ),
                    "stop",
                    "--state",
                    str(installed / "executor"),
                ],
                check=True,
                timeout=30,
            )
            status = read_status(root)
        session_file = Path.home() / ".cheese/environment-session.json"
        # A sandboxed room wrote this file itself if it is there; it has no
        # terminal of the platform's (`resource_cleanup.request_exit`).
        if os.environ.get("CHEESE_SANDBOXED") != "1" and session_file.exists():
            socket, session, _ = json.loads(session_file.read_text())
            probe = subprocess.run(
                ["tmux", "-S", socket, "has-session", "-t", session],
                capture_output=True,
            )
            if probe.returncode == 0:
                subprocess.run(
                    ["tmux", "-S", socket, "kill-session", "-t", session],
                    check=True,
                )
                status = read_status(root)
        if status["state"] == "ready" and ours_to_signal(status["pid"]):
            if sys.platform == "win32":
                # There is no SIGTERM to send; the executor it waits on was asked
                # to stop above, and what is left is ended with its children.
                terminate(status["pid"])
            else:
                try:
                    os.kill(status["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    # Closing its terminal can finish after the status read.
                    pass
                # Confirm termination before another revision can touch this HOME.
                import time

                for _ in range(50):
                    if (
                        process_identity(
                            status["pid"], reference=status["process_identity"]
                        )
                        != status["process_identity"]
                    ):
                        break
                    time.sleep(0.1)
                else:
                    raise SystemExit("agent did not stop; environment was not reset")
        if root.exists():
            write_json(root / "status.json", {"state": "pending"})
        print(json.dumps({"state": "pending"}))
    elif sys.argv[1:] == ["cancel"]:
        status = read_status(root)
        if status["state"] == "preparing" and ours_to_signal(status["pid"]):
            if sys.platform == "win32":
                terminate(status["pid"])
            else:
                os.kill(status["pid"], signal.SIGTERM)
        print(json.dumps(status))
    elif sys.argv[1:] == ["status"]:
        # Old shipped helpers ignore this opt-in and retain ordinary status reads.
        reader = (
            wait_status if os.environ.get("CHEESE_STATUS_WAIT") == "1" else read_status
        )
        print(json.dumps(reader(root)))
    else:
        config = json.loads(os.environ["CHEESE_ENVIRONMENT"])
        raise SystemExit(run(config, root, sys.argv[1:]))
