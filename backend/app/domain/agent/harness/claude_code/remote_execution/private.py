"""A bounded, disposable execution container for one private chat.

Shipped with client.py; only the central process can reach the Docker daemon.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
import urllib.parse
import uuid
from pathlib import Path

IMAGE = "cheese-private-executor:2.1.282"
LABEL = "com.cheese.private-chat"
GATE_LABEL = "com.cheese.private-chat-network"
#: Where the gate asks for a name's real addresses (settings.fetch_dns_over_https).
RESOLVER = "https://223.5.5.5/resolve"
SCRATCH_BYTES = 64 * 1024 * 1024
RUNTIME = "/opt/cheese/runtime.py"
STATE = "/work/.runtime"


def container_name(topic):
    return "cheese-private-" + uuid.UUID(str(topic)).hex


def gate_name(topic):
    """The container that owns the executor's network (private_network.py)."""
    return "cheese-private-net-" + uuid.UUID(str(topic)).hex


def target(topic, image=IMAGE, resolver=RESOLVER):
    return {
        "kind": "private",
        "topic": str(uuid.UUID(str(topic))),
        "image": image,
        "resolver": resolver,
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
        "--network",
        f"container:{gate_name(config['topic'])}",
        "--tmpfs",
        f"/work:rw,nosuid,nodev,size={SCRATCH_BYTES},uid=1000,gid=1000,mode=0700",
        "--workdir",
        "/work",
        config["image"],
    ]


def gate_command(config, environ):
    """The gate: allowed ``NET_ADMIN`` to write its namespace's rules, and told
    the platform endpoint the container is sent to (``CHEESE_API``), the one
    internal address those rules let through."""
    command = [
        "docker",
        "run",
        "--detach",
        "--init",
        "--name",
        gate_name(config["topic"]),
        "--label",
        f"{GATE_LABEL}={config['topic']}",
        "--read-only",
        "--user",
        "0:0",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "NET_ADMIN",
        "--cap-add",
        "SETUID",
        "--cap-add",
        "SETGID",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--cpus",
        "0.25",
        "--pids-limit",
        "64",
        "--ipc",
        "none",
        "--log-driver",
        "json-file",
        "--log-opt",
        "max-size=1m",
        "--tmpfs",
        "/run:rw,nosuid,nodev,noexec,size=1m",
        "--entrypoint",
        "python3",
        config["image"],
        "/opt/cheese/private_network.py",
        "--resolver",
        config.get("resolver", RESOLVER),
    ]
    api = urllib.parse.urlsplit(environ.get("CHEESE_API", ""))
    if api.hostname:
        port = api.port or (443 if api.scheme == "https" else 80)
        command += ["--api-host", api.hostname, "--api-port", str(port)]
    return command


def _inspect(name, label, topic):
    result = subprocess.run(
        ["docker", "container", "inspect", name],
        text=True,
        capture_output=True,
        timeout=15,
    )
    if result.returncode:
        if "No such container" in result.stderr or "No such object" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    info = json.loads(result.stdout)[0]
    if info["Config"].get("Labels", {}).get(label) != topic:
        raise RuntimeError("Refusing a container not owned by this private chat")
    return info


def inspect(config):
    return _inspect(container_name(config["topic"]), LABEL, config["topic"])


def inspect_gate(config):
    return _inspect(gate_name(config["topic"]), GATE_LABEL, config["topic"])


def _remove(name):
    subprocess.run(
        ["docker", "rm", "--force", name], capture_output=True, text=True, timeout=30
    )


def start_gate(config, environ):
    """A fresh gate, returned only once its rules are in place."""
    name = gate_name(config["topic"])
    if inspect_gate(config) is not None:
        _remove(name)
    created = subprocess.run(
        gate_command(config, environ), capture_output=True, text=True, timeout=60
    )
    if created.returncode:
        raise RuntimeError(
            f"docker run {config['image']} exited with status "
            f"{created.returncode}: {created.stderr.strip()}"
        )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ready = subprocess.run(
            ["docker", "exec", name, "test", "-e", "/run/cheese-network-ready"],
            capture_output=True,
            timeout=15,
        )
        if ready.returncode == 0:
            return
        info = inspect_gate(config)
        if info is None or not info["State"]["Running"]:
            break
        time.sleep(0.2)
    logs = subprocess.run(
        ["docker", "logs", "--tail", "20", name],
        capture_output=True,
        text=True,
        timeout=15,
    )
    _remove(name)
    raise RuntimeError(
        "Private executor network gate did not start: "
        + (logs.stderr + logs.stdout).strip()
    )


def ensure(config, directory, environ):
    """Reuse scratch across turns; never inherit the central model environment."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "private-executor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        info = inspect(config)
        if info is not None and info["State"]["Running"]:
            gate = inspect_gate(config)
            if gate is None or not gate["State"]["Running"]:
                # Its network went with the gate, and with it the rules that
                # keep it off internal addresses: no command runs there again.
                _remove(container_name(config["topic"]))
                info = None
        if info is None:
            start_gate(config, environ)
            created = subprocess.run(
                run_command(config), capture_output=True, text=True, timeout=60
            )
            if created.returncode:
                # Docker's stderr is the only place the reason is (a missing
                # image, a name still taken, no daemon): it goes into the
                # failure, which is what the session's startup log records.
                raise RuntimeError(
                    f"docker run {config['image']} exited with status "
                    f"{created.returncode}: {created.stderr.strip()}"
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
    # The executor first: the gate owns the network namespace it runs in.
    for name, found in (
        (container_name(config["topic"]), inspect),
        (gate_name(config["topic"]), inspect_gate),
    ):
        if found(config) is not None:
            subprocess.run(
                ["docker", "rm", "--force", name],
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
