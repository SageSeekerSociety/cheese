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
LEGACY_BASE = "https://cheese-dev-env1-gateway.119net.ghg.org.cn/api/connector"


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, text=True, capture_output=True,
                          timeout=kwargs.pop("timeout", 60), **kwargs).stdout


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


def remote(device_id, *, legacy=False, inspect_only=False):
    path = Path.home() / ".config/cheese/config.json"
    original = path.read_bytes()
    config = json.loads(original)
    if config.get("device_id") != device_id:
        raise RuntimeError("Device identity changed; configuration untouched")
    if config.get("ws") == NEW:
        verify_route()
        return {"status": "already_direct"}
    legacy_route = legacy and config.get("ws") is None and config.get("base") in (
        LEGACY_BASE, "https://okcheese.com/api/connector")
    if config.get("ws") != OLD and not legacy_route:
        return {"status": "unmanaged_route"}
    mode = command(["systemctl", "--user", "show", "cheese.service",
                    "--property=KillMode", "--value"]).strip()
    if mode != "process":
        raise RuntimeError("Connector restart would signal session processes")
    if inspect_only:
        return {"status": "eligible"}
    for attempt in range(30 if legacy else 1):
        try:
            with socket.create_connection(("127.0.0.1", 18083), timeout=1 if legacy else 5):
                break
        except OSError:
            if not legacy or attempt == 29:
                raise
            time.sleep(1)
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


REGISTER = '''
import asyncio, json, os, sys
import asyncpg
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://'))
    try:
        async with c.transaction():
            await c.execute("SET LOCAL lock_timeout = '5s'")
            await c.execute("SET LOCAL statement_timeout = '10s'")
            row = await c.fetchrow("""
                SELECT d.cloud_control_private FROM device d
                JOIN project_machines m ON m.device_id = d.device_id
                WHERE m.machine_id = $1 AND d.device_id = $2 AND m.ip = $3
                  AND m.login_user = $4 AND m.released_at IS NULL
                  AND m.status NOT IN ('deleted', 'deleting', 'error')
                  AND d.supply = 'cloud'
                FOR UPDATE OF d, m
            """, int(sys.argv[1]), *sys.argv[2:5])
            if row is None:
                raise RuntimeError('Legacy manifest does not match a live managed machine')
            before = row['cloud_control_private']
            if sys.argv[5] == 'activate' and not before:
                await c.execute('UPDATE device SET cloud_control_private = true WHERE device_id = $1', sys.argv[2])
        print(json.dumps({'private_before': before, 'operation': sys.argv[5]}))
    finally:
        await c.close()
asyncio.run(main())
'''


def register_legacy(key, operation):
    return json.loads(command(["docker", "exec", "-i", "cheese-backend-1", "python", "-",
                               *map(str, key), operation], input=REGISTER))


def pin_host(key, host_key):
    """Use the host key verified through the provider, never a network keyscan."""
    import base64
    parts = host_key.split()
    if len(parts) != 2 or parts[0] != "ssh-ed25519":
        raise ValueError("Expected one verified ed25519 host key")
    base64.b64decode(parts[1], validate=True)
    alias = f"cheese-cloud-{key[0]}-{key[1]}"
    path = Path.home() / ".local/state/cheese-cloud-control/known_hosts"
    original = path.read_bytes()
    existing = subprocess.run(["ssh-keygen", "-F", alias, "-f", str(path)],
                              capture_output=True, text=True, timeout=10)
    if existing.returncode == 0:
        keys = [" ".join(line.split()[1:3]) for line in existing.stdout.splitlines()
                if line and not line.startswith("#")]
        if host_key not in keys:
            raise RuntimeError("Existing device host-key pin differs from the verified key")
        return
    if existing.returncode != 1:
        raise RuntimeError("Could not inspect the existing host-key pins")
    backup = path.with_name("known_hosts.pre-legacy-" + hashlib.sha256(original).hexdigest())
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    if backup.read_bytes() != original:
        raise RuntimeError("Host-key backup does not match the original")
    with path.open("a") as stream:
        stream.write(f"\n{alias} {host_key}\n")
        stream.flush()
        os.fsync(stream.fileno())


def retire_legacy_tunnel(key):
    """Hand an existing single-machine tunnel to the standing cloud controller."""
    machine, device, ip, user = key
    unit = f"cheese-control-{machine}.service"
    path = Path.home() / ".config/systemd/user" / unit
    if not path.exists():
        return None
    original = path.read_bytes()
    expected = (
        "ExecStart=/usr/bin/ssh -NT -o IdentityAgent=none -o BatchMode=yes "
        "-o ExitOnForwardFailure=yes -o ServerAliveInterval=5 -o ServerAliveCountMax=2 "
        "-o StrictHostKeyChecking=yes "
        f"-o UserKnownHostsFile={Path.home()}/ops/cloud-warm-20260908/known_hosts-pipeline "
        f"-R 127.0.0.1:18080:127.0.0.1:8081 {user}@{ip}"
    )
    lines = original.decode().splitlines()
    execution = [line for line in lines if line.startswith("Exec")]
    if execution != [expected]:
        raise RuntimeError("Legacy tunnel unit differs from the verified single-device forward")
    if command(["systemctl", "--user", "cat", unit]).splitlines()[1:] != lines:
        raise RuntimeError("Legacy tunnel has overrides; refusing the handoff")
    backup = Path.home() / ".local/state/cheese-cloud-control" / (
        unit + ".pre-direct-" + hashlib.sha256(original).hexdigest())
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    if backup.read_bytes() != original:
        raise RuntimeError("Legacy tunnel backup differs from the original")
    command(["systemctl", "--user", "disable", "--now", unit])
    return {"unit": unit, "backup": str(backup)}


def migrate(log_dir, limit, *, legacy=False):
    cloud = runpy.run_path(str(Path(__file__).with_name("cloud-control.py")))
    if legacy:
        rows = json.loads(os.environ["LEGACY_DEVICE_MANIFEST"])
        inventory = {cloud["identity"](row): row for row in rows}
    else:
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
                ssh = [
                    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                    "-o", "StrictHostKeyChecking=yes", "-o", "IdentityAgent=none",
                    "-o", f"HostKeyAlias=cheese-cloud-{machine}-{device}",
                    "-o", f"UserKnownHostsFile={Path.home()}/.local/state/cheese-cloud-control/known_hosts",
                    f"{user}@{ip}", "python3", "-"]
                if legacy:
                    register_legacy(key, "validate")
                    pin_host(key, inventory[key]["host_key"])
                    inspected = json.loads(command([*ssh, "--inspect-legacy", device], input=source))
                    if inspected["status"] == "unmanaged_route":
                        emit(device, "unmanaged_route")
                        continue
                    registration = register_legacy(key, "activate")
                    emit(device, "private_control_registered", **registration)
                    retired = retire_legacy_tunnel(key)
                    if retired:
                        emit(device, "legacy_tunnel_retired", **retired)
                result = json.loads(command([
                    *ssh, "--legacy-remote" if legacy else "--remote", device],
                    input=source, timeout=120 if legacy else 60))
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
                    if legacy and retired:
                        (Path.home() / ".config/systemd/user" / retired["unit"]).unlink()
                        command(["systemctl", "--user", "daemon-reload"])
                        emit(device, "legacy_unit_removed", **retired)
                    changed += 1
                    if limit and changed >= limit:
                        break
            except Exception as exc:
                emit(device, "failed", error=str(exc))
                raise
        emit("batch", "complete", changed=changed)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in ("--remote", "--legacy-remote", "--inspect-legacy"):
        print(json.dumps(remote(sys.argv[2], legacy=sys.argv[1] != "--remote",
                                inspect_only=sys.argv[1] == "--inspect-legacy")))
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--limit", type=int, default=1)
        parser.add_argument("--legacy", action="store_true")
        parser.add_argument("--log-dir", type=Path, default=Path("logs/device-route-migration"))
        args = parser.parse_args()
        migrate(args.log_dir, args.limit, legacy=args.legacy)
