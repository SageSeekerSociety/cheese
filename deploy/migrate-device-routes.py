#!/usr/bin/env python3
"""Move managed device links off the application proxy, one device at a time."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import time

OLD = "ws://127.0.0.1:18080/connector/agent"
NEW = "ws://127.0.0.1:18083/connector/agent"


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True,
                          timeout=60, **kwargs).stdout


def verify_route():
    for _ in range(30):
        pid = command(["systemctl", "--user", "show", "cheese.service",
                       "--property=MainPID", "--value"]).strip()
        connections = command(["ss", "-tnp", "state", "established"])
        if any("127.0.0.1:18083" in line and f"pid={pid}," in line
               for line in connections.splitlines()):
            return
        time.sleep(1)
    raise RuntimeError("Connector has no established connection to the direct port")


def remote(device_id):
    path = Path.home() / ".config/cheese/config.json"
    original = path.read_bytes()
    config = json.loads(original)
    if config.get("device_id") != device_id:
        raise RuntimeError("Device identity changed; configuration untouched")
    if config.get("ws") == NEW:
        verify_route()
        return {"status": "already_direct"}
    if config.get("ws") != OLD:
        return {"status": "unmanaged_route"}
    mode = command(["systemctl", "--user", "show", "cheese.service",
                    "--property=KillMode", "--value"]).strip()
    if mode != "process":
        raise RuntimeError("Connector restart would signal session processes")
    with socket.create_connection(("127.0.0.1", 18083), timeout=5):
        pass
    # Session process IDs are checked independently of connector availability.
    tmux_socket = Path(f"/tmp/cheese-{os.getuid()}/t.sock")
    panes = command(["tmux", "-S", str(tmux_socket), "list-panes", "-a", "-F",
                     "#{pane_pid} #{pane_dead}"]) if tmux_socket.exists() else ""
    pids = [int(line.split()[0]) for line in panes.splitlines()
            if line.split()[1] == "0"]
    backup = path.with_name("config.pre-direct-" + hashlib.sha256(original).hexdigest() + ".json")
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    if backup.read_bytes() != original:
        raise RuntimeError("Backup does not match current configuration")
    config["ws"] = NEW
    temporary = path.with_name("config.direct-next.json")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(config, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    command(["systemctl", "--user", "restart", "cheese.service"])
    verify_route()
    for pid in pids:
        os.kill(pid, 0)
    return {"status": "changed", "backup": str(backup), "preserved_pane_pids": pids}


SNAPSHOT = '''
import json, urllib.request
from app.core.config import settings
request = urllib.request.Request(
    settings.device_connection_url.rstrip('/') + '/internal/device-connection/snapshot',
    headers={'X-Device-Connection-Secret': settings.device_connection_auth_secret})
with urllib.request.urlopen(request, timeout=10) as response:
    data = json.load(response)
print(json.dumps({item['device_id']: {'online': item['online'],
    'generation': item.get('connection_generation', 0)} for item in data['devices']}))
'''


def snapshot():
    return json.loads(command(["docker", "exec", "-i", "cheese-backend-1", "python", "-"],
                              input=SNAPSHOT))


def migrate(log_dir, limit):
    cloud = runpy.run_path(str(Path(__file__).with_name("cloud-control.py")))
    inventory = asyncio.run(cloud["inventory"]("cheese-backend-1"))
    log_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    manifest = log_dir / f"{started}-inventory.json"
    manifest.write_text(json.dumps(list(inventory.values()), indent=2))
    source = Path(__file__).read_text()
    with (log_dir / f"{started}.jsonl").open("x", buffering=1) as stream:
        def emit(device, status, **values):
            entry = {"at": datetime.now(timezone.utc).isoformat(), "device": device,
                     "status": status, "manifest": str(manifest), **values}
            line = json.dumps(entry)
            stream.write(line + "\n")
            print(line, flush=True)

        changed = 0
        for key in sorted(inventory):
            machine, device, ip, user = key
            emit(device, "start", machine=machine, ip=ip,
                 source_sha256=hashlib.sha256(source.encode()).hexdigest())
            previous = snapshot().get(device, {})
            if not previous.get("online"):
                emit(device, "skipped_offline")
                continue
            try:
                result = json.loads(command([
                    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                    "-o", "StrictHostKeyChecking=yes", "-o", "IdentityAgent=none",
                    "-o", f"HostKeyAlias=cheese-cloud-{machine}-{device}",
                    "-o", f"UserKnownHostsFile={Path.home()}/.local/state/cheese-cloud-control/known_hosts",
                    f"{user}@{ip}", "python3", "-", "--remote", device], input=source))
                emit(device, "remote_result", result=result)
                if result["status"] == "changed":
                    for _ in range(60):
                        current = snapshot().get(device, {})
                        if current.get("online") and current["generation"] != previous["generation"]:
                            break
                        time.sleep(1)
                    else:
                        raise RuntimeError("Device did not reconnect after route migration")
                    emit(device, "verified", generation=current["generation"], result=result)
                    changed += 1
                    if limit and changed >= limit:
                        break
            except Exception as exc:
                emit(device, "failed", error=str(exc))
                raise
        emit("batch", "complete", changed=changed)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--remote":
        print(json.dumps(remote(sys.argv[2])))
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--limit", type=int, default=1)
        parser.add_argument("--log-dir", type=Path, default=Path("logs/device-route-migration"))
        args = parser.parse_args()
        migrate(args.log_dir, args.limit)
