"""Standard-library program shipped to the selected machine before agent launch.

The launcher invokes this inside the selected execution boundary. A lock prevents
concurrent installers. At exec the lock is released; tmux owns agent reuse.
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    # This shipped helper also runs with macOS's system Python 3.9.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def process_identity(pid, *, reference=None):
    # Keep recognizing status files written by older helpers, including during
    # reset. New Linux records avoid spawning ps on every readiness poll.
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


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def read_status(directory: Path) -> dict:
    path = directory / "status.json"
    if not path.exists():
        return {"state": "pending"}
    data = json.loads(path.read_text())
    if data["state"] in ("preparing", "ready"):
        if (
            process_identity(data["pid"], reference=data["process_identity"])
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
    if "log_file" not in data:
        return data
    log_path = directory / data["log_file"]
    if log_path.exists():
        with log_path.open("rb") as stream:
            stream.seek(max(0, log_path.stat().st_size - 32768))
            data["log"] = stream.read().decode(errors="replace")
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


def run(config, directory, command, *, adopt=None):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (directory / "lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
        "process_identity": process_identity(os.getpid()),
        "started_at": now(),
        "finished_at": None,
        "exit_code": None,
        "log_file": log_name,
    }
    child = None

    def cancel(signum, _frame):
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
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
    environment = dict(os.environ)
    environment.update(config["variables"])
    environment.pop("CHEESE_ENVIRONMENT", None)
    environment["PATH"] = (
        str(Path.home() / ".local/bin") + os.pathsep + environment["PATH"]
    )
    model_proxy = environment.pop("CHEESE_MODEL_PROXY", "")
    script_environment = dict(environment)
    if model_proxy:
        # This proxy admits only model service hosts. Package downloads use
        # ordinary machine egress; the agent retains its metered model route.
        script_environment.pop("HTTPS_PROXY", None)
    receipt = directory / "initialized.json"
    initialized = json.loads(receipt.read_text()) if receipt.exists() else None
    with (directory / log_name).open("a", buffering=1) as log:
        write_json(directory / f"{attempt}.json", config)
        write_json(directory / "status.json", state)
        try:
            # Installs must never land in another cwd, so the room directory has
            # to be there before any script runs. It is NOT a repository: a room
            # holds the conversation and nothing else, and a task's checkout is
            # made next to it by `cheese worktree`. Asking git about this
            # directory therefore answers no useful question — and while it was
            # still asked, every room on a deployment failed preparation, which
            # means the agent was never exec'd below and the room went silent.
            work = Path(os.environ["CHEESE_WORK"]).resolve()
            if not work.is_dir():
                raise RuntimeError("work directory is missing")
            for stage, key in (
                ("setup", "setup_script"),
                ("startup", "startup_script"),
            ):
                state["stage"] = stage
                write_json(directory / "status.json", state)
                if stage == "setup" and initialized == config["revision"]:
                    log.write(f"{now()} setup: reused successful initialization\n")
                    continue
                script = directory / f"{attempt}-{stage}.sh"
                script.write_text(config[key])
                log.write(f"{now()} {stage}: started\n")
                if config[key].strip():
                    child = subprocess.Popen(
                        ["bash", "-e", str(script)],
                        cwd=work,
                        env=script_environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    try:
                        code = child.wait(timeout=1800)
                    except subprocess.TimeoutExpired:
                        cancel(signal.SIGTERM, None)
                    if code:
                        # Failed scripts must not leave background installers.
                        try:
                            os.killpg(child.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        state["exit_code"] = code
                        raise RuntimeError(f"{stage} script exited with status {code}")
                    child = None
                log.write(f"{now()} {stage}: completed\n")
                if stage == "setup":
                    write_json(receipt, config["revision"])
            if adopt is not None:
                # Bind only after both scripts succeed. Status must follow the
                # existing agent, not this short-lived preparation process.
                pid = adopt(environment, work)
                identity = process_identity(pid)
                if not identity:
                    raise RuntimeError("prepared agent exited during adoption")
                state.update(pid=pid, process_identity=identity)
            state.update(
                state="ready", stage="complete", exit_code=0, finished_at=now()
            )
            write_json(directory / "status.json", state)
            log.write(f"{now()} ready\n")
            if adopt is not None:
                return 0
            # Exec keeps the PID stable for status inspection. The project
            # variables are applied to the agent as well as both scripts.
            os.chdir(work)
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
            lock.close()
    return 0


if __name__ == "__main__":
    root = Path.home() / ".cheese-environment"
    if sys.argv[1:] == ["reset"]:
        status = read_status(root)
        if status["state"] == "preparing":
            raise SystemExit("environment is still preparing")
        session_file = Path.home() / ".claude/environment-session.json"
        if session_file.exists():
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
        if status["state"] == "ready":
            os.kill(status["pid"], signal.SIGTERM)
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
        if status["state"] == "preparing":
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
