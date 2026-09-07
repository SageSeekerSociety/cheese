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
import uuid
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def process_identity(pid):
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
        if process_identity(data["pid"]) != data["process_identity"]:
            data = {**data, "state": "failed", "error": "environment process exited"}
    if "log_file" not in data:
        return data
    log_path = directory / data["log_file"]
    if log_path.exists():
        with log_path.open("rb") as stream:
            stream.seek(max(0, log_path.stat().st_size - 32768))
            data["log"] = stream.read().decode(errors="replace")
    return data


def run(config, directory, command):
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

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    environment = dict(os.environ)
    environment.update(config["variables"])
    environment.pop("CHEESE_ENVIRONMENT", None)
    environment["PATH"] = (
        str(Path.home() / ".local/bin") + os.pathsep + environment["PATH"]
    )
    receipt = directory / "initialized.json"
    initialized = json.loads(receipt.read_text()) if receipt.exists() else None
    with (directory / log_name).open("a", buffering=1) as log:
        write_json(directory / f"{attempt}.json", config)
        write_json(directory / "status.json", state)
        try:
            # An empty/failed checkout must never cause installs in another cwd.
            work = Path(os.environ["CHEESE_WORK"]).resolve()
            checkout = subprocess.run(
                ["git", "-C", str(work), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
            )
            if checkout.returncode or checkout.stdout.strip() != "true":
                raise RuntimeError("repository checkout is not ready")
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
                        env=environment,
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
            state.update(
                state="ready", stage="complete", exit_code=0, finished_at=now()
            )
            write_json(directory / "status.json", state)
            log.write(f"{now()} ready\n")
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
                if process_identity(status["pid"]) != status["process_identity"]:
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
        print(json.dumps(read_status(root)))
    else:
        config = json.loads(os.environ["CHEESE_ENVIRONMENT"])
        raise SystemExit(run(config, root, sys.argv[1:]))
