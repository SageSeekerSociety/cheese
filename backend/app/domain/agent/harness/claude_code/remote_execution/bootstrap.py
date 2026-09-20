"""Prepare a room executor using platform helpers, without starting a model."""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

VERSION = "2.1.277"
# The platform's own directory inside a room's home, and the one it used before.
# A harness's config dir is the harness's; everything the platform installs —
# the executor, its helpers, the CLI, the environment runner — lives here.
PLATFORM_DIR = ".cheese"
PREVIOUS_PLATFORM_DIR = ".claude"


class UpgradeDeferred(Exception):
    def __init__(self, info):
        self.info = info


def stage_release(platform_dir, payload):
    contents = {
        name: (
            base64.b64decode(payload["files"][name])
            if name in payload["files"]
            else (platform_dir / name).read_bytes()
        )
        for name in payload.get("file_names", payload["files"])
    }
    digest = hashlib.sha256(
        json.dumps(
            {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    releases = platform_dir / "executor-releases"
    releases.mkdir(exist_ok=True)
    release = releases / digest
    if not release.exists():
        staged = releases / (digest + "." + uuid.uuid4().hex)
        staged.mkdir(mode=0o700)
        for name, data in contents.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Executor file must be inside its release")
            path = staged / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o700)
        (staged / "executor-files.json").write_text(json.dumps(list(contents)))
        staged.rename(release)
    return release, contents


def activate_release(platform_dir, release, names):
    # Stable entrypoints select a release; running executors use the recorded path.
    for name in [*names, "executor-files.json"]:
        destination = platform_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".next")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(release / name)
        temporary.replace(destination)


def binary(owner, api, verified=None):
    destination = owner / ".cheese/claude/versions" / VERSION
    candidates = [destination, owner / ".local/bin/claude"]
    installed = shutil.which("claude")
    if installed:
        candidates.append(Path(installed))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            stat = candidate.stat()
            identity = (
                VERSION,
                stat.st_dev,
                stat.st_ino,
                stat.st_mode,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
            if verified is not None and verified.get(str(candidate)) == identity:
                return str(candidate)
            result = subprocess.run(
                [str(candidate), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.split()[0] == VERSION:
                if candidate != destination:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(
                        destination.name + "." + uuid.uuid4().hex
                    )
                    shutil.copyfile(candidate, temporary)
                    temporary.chmod(0o700)
                    temporary.replace(destination)
                    return str(destination)
                if verified is not None:
                    verified[str(candidate)] = identity
                return str(candidate)
    # Warm room preparation needs neither download handling nor TLS setup.
    import platform
    from urllib.request import urlopen

    architecture = {"x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}[
        platform.machine()
    ]
    system = {"Linux": "linux", "Darwin": "darwin"}[platform.system()]
    target = f"{system}-{architecture}"
    if system == "linux" and platform.libc_ver()[0] == "musl":
        target += "-musl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex)
    with urlopen(
        f"{api}/connector/claude/{VERSION}/{target}/claude", timeout=300
    ) as source:
        with temporary.open("xb") as output:
            shutil.copyfileobj(source, output)
    temporary.chmod(0o700)
    result = subprocess.run(
        [str(temporary), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    if result.stdout.split()[0] != VERSION:
        raise RuntimeError("The executor binary does not match the verified version")
    temporary.replace(destination)
    return str(destination)


def stop_previous_root(home):
    """Take down an executor the platform started under its previous directory.

    The executor is a detached daemon: it outlives the screen that asked for it,
    and nothing that closes a session closes it. So a room's executor survives a
    change of install root, and the code that would otherwise stop it — the
    "already running" branch below — looks for its state under the new root and
    finds nothing. Two daemons then share one HOME and one environment status
    file. Whoever moves the root has to come back for what the old one started.
    """
    previous = home / PREVIOUS_PLATFORM_DIR
    state = previous / "executor"
    runner = previous / "remote-execution/runtime.py"
    if not (state / "config.json").exists() or not runner.exists():
        return
    # Asked of the runtime that started it, not the one being installed now: the
    # protocol it answers is the one it was built with. Ask whether it is there
    # before telling it to go — a room whose machine rebooted has this state on
    # disk with nothing behind it, and that is not a reason to refuse the room.
    # A stop that is asked for and fails IS a reason: the whole point is that
    # two of these must not run over one home.
    runtime = runpy.run_path(str(runner))
    try:
        alive = runtime["request"](state, "ping")
    except (OSError, RuntimeError):
        alive = None
    if alive:
        subprocess.run(
            [sys.executable, str(runner), "stop", "--state", str(state)],
            check=True,
            timeout=30,
        )
    shutil.rmtree(state, ignore_errors=True)
    # Every marker that says a room is installed here goes with it. What stays
    # under the previous root is inert copies of programs; anything that still
    # ANSWERS "the executor is over here" would go on being believed, by the
    # teardown path most of all — it reads these to find what to stop.
    (previous / "execution-owner.json").unlink(missing_ok=True)
    (previous / "remote-target.json").unlink(missing_ok=True)


@contextlib.contextmanager
def prepared(payload, owner, verified=None, *, refresh_runtime=False):
    project, resource = (
        str(uuid.UUID(payload["project"])),
        str(uuid.UUID(payload["resource"])),
    )
    home = owner / ".cheese/home" / project / resource
    work = home / "room"
    platform_dir = home / PLATFORM_DIR
    config_dir = home / ".claude"
    platform_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    work.mkdir(parents=True, exist_ok=True)
    with (platform_dir / "executor-bootstrap.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stop_previous_root(home)
        release, contents = stage_release(platform_dir, payload)
        env = dict(os.environ)
        for name in list(env):
            if (
                name.startswith(("ANTHROPIC_", "CLAUDE_"))
                or name == "CHEESE_MODEL_PROXY"
            ):
                env.pop(name)
        env.update(payload["env"])
        env.update(
            HOME=str(home),
            CLAUDE_CONFIG_DIR=str(config_dir),
            CHEESE_WORK=str(work),
            CHEESE_WORKTREE_ROOT=str(work),
            CHEESE_PREVIEW_UP=str(release / "cheese-preview-up"),
            PATH=os.pathsep.join(
                (
                    str(release / "remote-execution/bin"),
                    str(release),
                    env.get("PATH", ""),
                )
            ),
        )
        scoped_env = {
            name: value
            for name, value in env.items()
            if name in payload["env"]
            or name
            in {
                "HOME",
                "CLAUDE_CONFIG_DIR",
                "CHEESE_WORK",
                "CHEESE_WORKTREE_ROOT",
                "CHEESE_PREVIEW_UP",
            }
        }
        config = {
            "workspace": str(work),
            "claude": binary(owner, env["CHEESE_API"], verified),
            "env": scoped_env,
            "mcp_servers": {},
            "release": str(release),
        }
        mcp = work / ".mcp.json"
        if mcp.exists():
            config["mcp_servers"] = json.loads(mcp.read_text()).get("mcpServers", {})
        state = platform_dir / "executor"
        state.mkdir(exist_ok=True, mode=0o700)
        (platform_dir / "cheese-preview.token").write_text(env["CHEESE_TOKEN"])
        (platform_dir / "cheese-preview.token").chmod(0o600)
        if (state / "config.json").exists():
            previous = json.loads((state / "config.json").read_text())
            source = (
                Path(previous.get("release", platform_dir))
                / "remote-execution/runtime.py"
            )
            runtime = runpy.run_path(str(source))
            try:
                info = runtime["request"](state, "ping")
            except (ConnectionError, FileNotFoundError):
                info = None
            if info:
                unchanged = {"env", "claude", "release"}
                if {k: v for k, v in previous.items() if k not in unchanged} != {
                    k: v for k, v in config.items() if k not in unchanged
                }:
                    raise RuntimeError(
                        "Executor configuration changed; restart the room environment"
                    )
                environment_file = state / "environment.json"
                if environment_file.exists() and json.loads(
                    environment_file.read_text()
                ) != payload.get("environment"):
                    raise RuntimeError(
                        "Environment dependencies changed; "
                        "create a new execution environment"
                    )
                changed = (
                    previous.get("release") != str(release)
                    or previous.get("claude") != config["claude"]
                    or info.get("upgrading", False)
                )
                if changed:
                    if not refresh_runtime:
                        raise RuntimeError(
                            "Executor release changed; prepare an idle upgrade"
                        )
                    if "idle_upgrade" in info.get("capabilities", []):
                        ready = runtime["request"](
                            state,
                            "begin_upgrade",
                            {"release": str(release), "claude": config["claude"]},
                        )["ready"]
                    else:
                        # Existing installations are admitted under the backend's
                        # exclusive release lock until they gain local admission.
                        tasks = runtime["request"](
                            state, "control", {"subtype": "background_tasks"}
                        )["tasks"]
                        ready = not any(task["status"] == "running" for task in tasks)
                    if not ready:
                        if info.get("protocol_version", 1) != payload.get(
                            "protocol_version", 1
                        ):
                            raise RuntimeError(
                                "Executor protocol upgrade is waiting for running work"
                            )
                        refreshed = {**previous.get("env", {}), **payload["env"]}
                        runtime["request"](state, "configure", {"env": refreshed})
                        info.update(
                            state=str(state),
                            mcp_servers=list(previous.get("mcp_servers", {})),
                            upgrade_pending=True,
                            desired_release=str(release),
                        )
                        if payload.get("environment"):
                            runner = runpy.run_path(
                                str(
                                    Path(previous.get("release", platform_dir))
                                    / "cheese-environment.py"
                                )
                            )
                            info["environment_status"] = runner["read_status"](
                                home / ".cheese-environment"
                            )["state"]
                        raise UpgradeDeferred(info)
                    subprocess.run(
                        [sys.executable, str(source), "stop", "--state", str(state)],
                        check=True,
                        timeout=30,
                    )
        activate_release(platform_dir, release, contents)
        if payload.get("environment"):
            directory = home / ".cheese-environment"
            directory.mkdir(exist_ok=True, mode=0o700)
            runner = runpy.run_path(str(release / "cheese-environment.py"))
            runner["write_json"](directory / "config.json", payload["environment"])
        yield home, config, state, env


def configure(payload):
    try:
        configure_idle(payload)
    except UpgradeDeferred as deferred:
        print(json.dumps(deferred.info))


def configure_idle(payload):
    with prepared(payload, Path.home(), refresh_runtime=True) as (
        home,
        config,
        state,
        env,
    ):
        platform_dir = home / PLATFORM_DIR
        work = Path(config["workspace"])
        scoped_env = config["env"]
        log = platform_dir / "executor-bootstrap.log"
        release = Path(config["release"])
        runtime = runpy.run_path(str(release / "remote-execution/runtime.py"))

        if (state / "config.json").exists():
            try:
                info = runtime["request"](state, "ping")
            except (ConnectionError, FileNotFoundError):
                info = None
            if info:
                runtime["request"](state, "configure", {"env": scoped_env})
                if payload.get("environment"):
                    runner = runpy.run_path(str(release / "cheese-environment.py"))
                    info["environment_status"] = runner["read_status"](
                        home / ".cheese-environment"
                    )["state"]
                print(
                    json.dumps(
                        {
                            **info,
                            "mcp_servers": list(config["mcp_servers"]),
                            "state": str(state),
                        }
                    )
                )
                return
        runtime["write_json"](state / "config.json", config)
        runtime["write_json"](state / "environment.json", payload.get("environment"))
        runtime["write_json"](
            platform_dir / "execution-owner.json", {"resource": home.name}
        )
        with log.open("a") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(release / "remote-execution/bootstrap.py"),
                    str(state),
                ],
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        deadline = time.monotonic() + 600
        while True:
            if process.poll() is not None:
                if payload.get("environment"):
                    runner = runpy.run_path(str(release / "cheese-environment.py"))
                    status = runner["read_status"](home / ".cheese-environment")
                    if status["state"] == "failed":
                        info = {"environment_status": "failed"}
                        break
                raise RuntimeError(
                    "Executor startup failed; inspect executor-bootstrap.log"
                )
            try:
                info = runtime["request"](state, "ping")
                break
            except (ConnectionError, FileNotFoundError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Executor readiness timed out") from None
                time.sleep(0.1)
        # The caller records this rather than deriving it: the process that
        # reaches the executor later is released separately from the one that
        # installs it, so a directory named in both is a directory two builds
        # can disagree about. See `agent.execution.executor_state`.
        print(
            json.dumps(
                {
                    **info,
                    "workspace": str(work),
                    "mcp_servers": list(config["mcp_servers"]),
                    "state": str(state),
                }
            )
        )


def run(state):
    configuration = json.loads((state / "environment.json").read_text())
    command = [
        sys.executable,
        str(Path(__file__).with_name("runtime.py")),
        "serve",
        "--state",
        str(state),
    ]
    if configuration:
        runner = runpy.run_path(
            str(Path(__file__).resolve().parent.parent / "cheese-environment.py")
        )
        raise SystemExit(
            runner["run"](configuration, Path.home() / ".cheese-environment", command)
        )
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__" and len(sys.argv) == 2:
    run(Path(sys.argv[1]))
