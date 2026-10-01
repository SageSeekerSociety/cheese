"""A bounded, disposable execution container for one private chat.

Shipped with client.py; only the central process can reach the Docker daemon.

The container runs on a Docker network with no route out (``NETWORK``): it is
internal, and the host has no address on it. The one thing it reaches there is
the egress proxy (``private_egress.py``), one per session host, which is also
attached to the default bridge and decides every connection: the public
internet, and the platform endpoint in ``CHEESE_API``, nothing else internal.
The container is told about the proxy (``HTTP_PROXY`` and the rest) so ordinary
tools use it, but nothing depends on them using it: there is no other exit.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path

IMAGE = "cheese-private-executor:2.1.282"
LABEL = "com.cheese.private-chat"
SCRATCH_BYTES = 64 * 1024 * 1024
RUNTIME = "/opt/cheese/runtime.py"
STATE = "/work/.runtime"

NETWORK = "cheese-private-chats"
EGRESS = "cheese-private-egress"
EGRESS_LABEL = "com.cheese.private-egress"
EGRESS_PORT = 3128
#: Where the proxy asks for a name's real addresses (settings.fetch_dns_over_https).
RESOLVER = "https://223.5.5.5/resolve"
#: The network the proxy leaves by, where the platform endpoint is reachable.
OUTSIDE = "bridge"


def container_name(topic):
    return "cheese-private-" + uuid.UUID(str(topic)).hex


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
        NETWORK,
        "--tmpfs",
        f"/work:rw,nosuid,nodev,size={SCRATCH_BYTES},uid=1000,gid=1000,mode=0700",
        "--workdir",
        "/work",
        config["image"],
    ]


def proxy_env():
    """What tells the container's tools to go through the proxy."""
    http = f"http://{EGRESS}:{EGRESS_PORT}"
    socks = f"socks5h://{EGRESS}:{EGRESS_PORT}"
    env = {}
    for name, value in (
        ("HTTP_PROXY", http),
        ("HTTPS_PROXY", http),
        ("ALL_PROXY", socks),
        ("NO_PROXY", "localhost,127.0.0.1,::1"),
    ):
        env[name] = env[name.lower()] = value
    return env


def _docker(*args, timeout=30):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )


def _failed(what, result):
    # Docker's stderr is the only place the reason is (a missing image, a name
    # still taken, no daemon): it goes into the failure, which is what the
    # session's startup log records.
    return RuntimeError(
        f"docker {what} exited with status {result.returncode}: {result.stderr.strip()}"
    )


def ensure_network():
    """The private chats' network, created once per host; its subnets."""
    found = _docker("network", "inspect", NETWORK)
    if found.returncode:
        created = _docker(
            "network",
            "create",
            "--internal",
            "--driver",
            "bridge",
            # No address of the host's on the bridge, so nothing it listens on
            # is reachable from the network either.
            "--opt",
            "com.docker.network.bridge.inhibit_ipv4=true",
            "--label",
            f"{EGRESS_LABEL}=network",
            NETWORK,
        )
        if created.returncode and "already exists" not in created.stderr:
            raise _failed(f"network create {NETWORK}", created)
        found = _docker("network", "inspect", NETWORK)
        if found.returncode:
            raise _failed(f"network inspect {NETWORK}", found)
    info = json.loads(found.stdout)[0]
    options = info.get("Options") or {}
    if (
        not info.get("Internal")
        or options.get("com.docker.network.bridge.inhibit_ipv4") != "true"
    ):
        raise RuntimeError(
            f"Docker network {NETWORK} has a route out or an address of the "
            "host; private chats do not start on it"
        )
    return [entry["Subnet"] for entry in (info.get("IPAM") or {}).get("Config") or []]


def _platform(environ):
    api = urllib.parse.urlsplit(environ.get("CHEESE_API", ""))
    if not api.hostname:
        return None
    port = api.port or (443 if api.scheme == "https" else 80)
    host = f"[{api.hostname}]" if ":" in api.hostname else api.hostname
    return f"{host}:{port}"


def egress_command(config, environ, subnets, identity):
    command = [
        "run",
        "--detach",
        "--init",
        "--restart",
        "unless-stopped",
        "--name",
        EGRESS,
        "--label",
        f"{EGRESS_LABEL}={identity}",
        "--network",
        NETWORK,
        "--read-only",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--cpus",
        "1",
        "--pids-limit",
        "512",
        "--ipc",
        "none",
        "--log-driver",
        "json-file",
        "--log-opt",
        "max-size=1m",
        "--entrypoint",
        "python3",
        config["image"],
        "/opt/cheese/private_egress.py",
        "--resolver",
        config.get("resolver", RESOLVER),
    ]
    platform = _platform(environ)
    if platform:
        command += ["--platform", platform]
    for subnet in subnets:
        command += ["--client", subnet]
    return command


def _egress_ready():
    probe = (
        "import socket; "
        f"socket.create_connection(('127.0.0.1', {EGRESS_PORT}), timeout=2).close()"
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _docker("exec", EGRESS, "python3", "-c", probe).returncode == 0:
            return True
        state = _docker(
            "container", "inspect", "--format", "{{.State.Running}}", EGRESS
        )
        if state.stdout.strip() != "true":
            return False
        time.sleep(0.2)
    return False


def ensure_egress(config, environ):
    """The host's egress proxy, running with this configuration.

    Adopted when one is already running with the same image and settings;
    otherwise (absent, stopped, or started for another image or platform
    endpoint) replaced. Every private chat on the host shares it.
    """
    subnets = ensure_network()
    image = _docker("image", "inspect", "--format", "{{.Id}}", config["image"])
    if image.returncode:
        raise _failed(f"image inspect {config['image']}", image)
    identity = hashlib.sha256(
        json.dumps(
            [
                image.stdout.strip(),
                config.get("resolver", RESOLVER),
                _platform(environ),
                subnets,
            ]
        ).encode()
    ).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / "cheese-private-egress.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        found = _docker(
            "container",
            "inspect",
            "--format",
            '{{.State.Running}} {{index .Config.Labels "' + EGRESS_LABEL + '"}}',
            EGRESS,
        )
        if found.returncode == 0 and found.stdout.split() == ["true", identity]:
            return
        _docker("rm", "--force", EGRESS)
        created = _docker(*egress_command(config, environ, subnets, identity))
        if created.returncode:
            raise _failed(f"run {config['image']}", created)
        connected = _docker("network", "connect", OUTSIDE, EGRESS)
        if connected.returncode:
            raise _failed(f"network connect {OUTSIDE} {EGRESS}", connected)
        if not _egress_ready():
            logs = _docker("logs", "--tail", "20", EGRESS)
            raise RuntimeError(
                "Private chat egress proxy did not start: "
                + (logs.stderr + logs.stdout).strip()
            )


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
        ensure_egress(config, environ)
        info = inspect(config)
        if (
            info is not None
            and info["State"]["Running"]
            and info["HostConfig"]["NetworkMode"] != NETWORK
        ):
            # Started with a way out: no command runs there again.
            _docker("rm", "--force", container_name(config["topic"]))
            info = None
        if info is None:
            created = subprocess.run(
                run_command(config), capture_output=True, text=True, timeout=60
            )
            if created.returncode:
                raise _failed(f"run {config['image']}", created)
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
            HOME="/work/.home",
            TMPDIR="/work/.tmp",
            CHEESE_WORKTREE_ROOT="/work",
            **proxy_env(),
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
