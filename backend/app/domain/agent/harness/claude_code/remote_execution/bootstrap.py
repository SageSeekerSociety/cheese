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
import uuid
from pathlib import Path

VERSION = "2.1.265"


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


@contextlib.contextmanager
def prepared(payload, owner, verified=None, *, refresh_runtime=False):
    project, resource = (
        str(uuid.UUID(payload["project"])),
        str(uuid.UUID(payload["resource"])),
    )
    home = owner / ".cheese/home" / project / resource
    work = home / "room"
    config_dir = home / ".claude"
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    work.mkdir(parents=True, exist_ok=True)
    with (config_dir / "executor-bootstrap.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if refresh_runtime:
            source = config_dir / "remote-execution/runtime.py"
            state = config_dir / "executor"
            if source.exists() and (state / "config.json").exists():
                runtime = runpy.run_path(str(source))
                try:
                    info = runtime["request"](state, "ping")
                except (ConnectionError, FileNotFoundError):
                    info = None
                expected = hashlib.sha256(
                    base64.b64decode(payload["files"]["remote-execution/runtime.py"])
                ).hexdigest()
                if info and info.get("runtime_sha256") != expected:
                    tasks = runtime["request"](
                        state, "control", {"subtype": "background_tasks"}
                    )["tasks"]
                    if any(task["status"] == "running" for task in tasks):
                        raise RuntimeError(
                            "Executor update is waiting for running commands to finish"
                        )
        for name, content in payload["files"].items():
            destination = config_dir / name
            destination.relative_to(config_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            decoded = base64.b64decode(content)
            # Keep unchanged CLI sources from invalidating the worker's preload.
            if not destination.exists() or destination.read_bytes() != decoded:
                destination.write_bytes(decoded)
            destination.chmod(0o700)
        if "file_names" in payload:
            manifest = config_dir / "executor-files.json"
            contents = json.dumps(payload["file_names"])
            if not manifest.exists() or manifest.read_text() != contents:
                manifest.write_text(contents)
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
            CHEESE_PREVIEW_UP=str(config_dir / "cheese-preview-up"),
            PATH=str(config_dir) + os.pathsep + env.get("PATH", ""),
        )
        (config_dir / "cheese-preview.token").write_text(env["CHEESE_TOKEN"])
        (config_dir / "cheese-preview.token").chmod(0o600)
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
        }
        mcp = work / ".mcp.json"
        if mcp.exists():
            config["mcp_servers"] = json.loads(mcp.read_text()).get("mcpServers", {})
        state = config_dir / "executor"
        state.mkdir(exist_ok=True, mode=0o700)
        yield home, config, state, env


def configure(payload):
    with prepared(payload, Path.home(), refresh_runtime=True) as (
        home,
        config,
        state,
        env,
    ):
        config_dir = home / ".claude"
        work = Path(config["workspace"])
        scoped_env = config["env"]
        log = config_dir / "executor-bootstrap.log"
        sys.path.insert(0, str(config_dir / "remote-execution"))
        runtime = runpy.run_path(str(config_dir / "remote-execution/runtime.py"))

        if (state / "config.json").exists():
            previous = json.loads((state / "config.json").read_text())
            try:
                info = runtime["request"](state, "ping")
            except (ConnectionError, FileNotFoundError):
                info = None
            if info:
                if {k: v for k, v in previous.items() if k != "env"} != {
                    k: v for k, v in config.items() if k != "env"
                }:
                    raise RuntimeError(
                        "Executor configuration changed; restart the room environment"
                    )
                if info.get("runtime_sha256") == runtime["SOURCE_SHA256"]:
                    runtime["request"](state, "configure", {"env": scoped_env})
                    if payload.get("environment"):
                        runner = runpy.run_path(
                            str(config_dir / "cheese-environment.py")
                        )
                        info["environment_status"] = runner["read_status"](
                            home / ".cheese-environment"
                        )["state"]
                    print(
                        json.dumps({**info, "mcp_servers": list(config["mcp_servers"])})
                    )
                    return
                subprocess.run(
                    [
                        sys.executable,
                        str(config_dir / "remote-execution/runtime.py"),
                        "stop",
                        "--state",
                        str(state),
                    ],
                    check=True,
                    timeout=15,
                )
        runtime["write_json"](state / "config.json", config)
        runtime["write_json"](state / "environment.json", payload.get("environment"))
        runtime["write_json"](
            config_dir / "execution-owner.json", {"resource": home.name}
        )
        with log.open("a") as output:
            subprocess.Popen(
                [
                    sys.executable,
                    str(config_dir / "remote-execution/bootstrap.py"),
                    str(state),
                ],
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        print(
            json.dumps(
                {"workspace": str(work), "mcp_servers": list(config["mcp_servers"])}
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
        runner = runpy.run_path(str(state.parent / "cheese-environment.py"))
        raise SystemExit(
            runner["run"](configuration, Path.home() / ".cheese-environment", command)
        )
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__" and len(sys.argv) == 2:
    run(Path(sys.argv[1]))
