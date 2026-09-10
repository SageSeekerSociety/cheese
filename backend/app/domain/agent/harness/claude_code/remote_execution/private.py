"""A bounded, disposable execution container for one private chat.

Shipped with client.py; only the central process can reach the Docker daemon.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import uuid
from pathlib import Path

IMAGE = "cheese-private-executor:2.1.265"
LABEL = "com.cheese.private-chat"
SCRATCH_BYTES = 64 * 1024 * 1024
RUNTIME = "/opt/cheese/runtime.py"
STATE = "/work/.runtime"


def container_name(topic):
    return "cheese-private-" + uuid.UUID(str(topic)).hex


def target(topic, image=IMAGE):
    return {
        "kind": "private",
        "topic": str(uuid.UUID(str(topic))),
        "image": image,
        "command": ["docker", "exec", "-i", container_name(topic), "python3", RUNTIME],
        "state": STATE,
        "workspace": "/work",
        "mcp_servers": [],
    }


def run_command(config):
    return [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--init",
        "--name",
        container_name(config["topic"]),
        "--label",
        f"{LABEL}={config['topic']}",
        "--read-only",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "128",
        "--ipc",
        "none",
        "--log-driver",
        "none",
        "--tmpfs",
        f"/work:rw,nosuid,nodev,size={SCRATCH_BYTES},uid=1000,gid=1000,mode=0700",
        "--workdir",
        "/work",
        config["image"],
    ]


def inspect(config):
    result = subprocess.run(
        ["docker", "container", "inspect", container_name(config["topic"])],
        text=True,
        capture_output=True,
        timeout=15,
    )
    if result.returncode:
        if "No such container" in result.stderr or "No such object" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    info = json.loads(result.stdout)[0]
    if info["Config"].get("Labels", {}).get(LABEL) != config["topic"]:
        raise RuntimeError("Refusing a container not owned by this private chat")
    return info


def ensure(config, directory, environ):
    """Reuse scratch across turns; never inherit the central model environment."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "private-executor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        info = inspect(config)
        if info is None:
            subprocess.run(
                run_command(config), check=True, capture_output=True, timeout=60
            )
        elif not info["State"]["Running"]:
            raise RuntimeError(
                "Private executor stopped; release it before recreating scratch"
            )
        # These are platform-scoped credentials, never the model or host credentials.
        env = {
            key: environ[key]
            for key in (
                "CHEESE_API",
                "CHEESE_TOKEN",
                "CHEESE_PROJECT",
                "CHEESE_TOPIC",
                "CHEESE_AUTHOR",
                "CHEESE_MEMORY_SCOPE",
                "CHEESE_OWNER",
            )
            if key in environ
        }
        env.update(
            HOME="/work/.home", TMPDIR="/work/.tmp", CHEESE_WORKTREE_ROOT="/work"
        )
        configuration = {
            "workspace": "/work",
            "claude": "/usr/local/bin/claude",
            "env": env,
            "private": True,
            "mcp_servers": {},
        }
        mode = "request" if info else "start"
        payload = (
            {"method": "configure_private", "params": {"env": env}}
            if info
            else configuration
        )
        result = subprocess.run(
            [*config["command"], mode, "--state", config["state"]],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError(
                "Private executor startup failed: " + result.stderr.strip()
            )
        return json.loads(result.stdout)


def release(config):
    if config.get("kind") != "private":
        return
    if inspect(config) is not None:
        subprocess.run(
            ["docker", "rm", "--force", container_name(config["topic"])],
            check=True,
            capture_output=True,
            timeout=30,
        )


def entrypoint():
    # /tmp and the home directory resolve into the same bounded tmpfs.
    for name in (".tmp", ".home"):
        Path("/work", name).mkdir(mode=0o700, exist_ok=True)
    os.execvp("sleep", ["sleep", "infinity"])


if __name__ == "__main__":
    entrypoint()
