#!/usr/bin/env python3
"""Keep cloud connector traffic on encrypted SSH forwards to the backend host."""
import argparse
import asyncio
import ipaddress
import json
import re
import signal
from datetime import datetime, timezone
from pathlib import Path

# Execute inside the backend container so database credentials stay there.
INVENTORY = '''
import asyncio, json, os
import asyncpg
from app.core.config import settings
async def main():
    if not settings.microcloud_direct_control:
        print("[]")
        return
    connection = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    try:
        rows = await connection.fetch("""
            select machine_id, device_id, ip, login_user from project_machines
            where released_at is null and status not in ('deleted', 'deleting', 'error')
              and device_id is not null and ip is not null
            union
            select machine_id, device_id, ip, create_request->>'user' as login_user
            from warm_machines where state in ('preparing', 'ready')
              and machine_id is not null and device_id is not null and ip is not null
        """)
        print(json.dumps([dict(row) for row in rows]))
    finally:
        await connection.close()
asyncio.run(main())
'''


def log(event, **fields):
    print(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                      "event": event, **fields}), flush=True)


def identity(row):
    ipaddress.ip_address(row["ip"])
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", row["login_user"]):
        raise ValueError("invalid cloud login user")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", row["device_id"]):
        raise ValueError("invalid cloud device identity")
    return int(row["machine_id"]), row["device_id"], row["ip"], row["login_user"]


async def inventory(container):
    process = await asyncio.create_subprocess_exec(
        "docker", "exec", "-i", "-w", "/app", container, "python", "-",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(INVENTORY.encode()), 20)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise RuntimeError(f"inventory exited {process.returncode}")
    rows = json.loads(output)
    return {identity(row): row for row in rows}


async def forward(key, state_dir, backend_port):
    machine_id, device_id, ip, user = key
    while True:
        process = None
        try:
            log("connecting", machine_id=machine_id, device_id=device_id)
            process = await asyncio.create_subprocess_exec(
                "ssh", "-NT", "-o", "IdentityAgent=none", "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10", "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
                "-o", "StrictHostKeyChecking=accept-new",
                # IPs are recycled; a new provider/device identity gets a new key pin.
                "-o", f"HostKeyAlias=cheese-cloud-{machine_id}-{device_id}",
                "-o", f"UserKnownHostsFile={state_dir / 'known_hosts'}",
                "-R", f"127.0.0.1:18080:127.0.0.1:{backend_port}", f"{user}@{ip}",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, error = await process.communicate()
            log("disconnected", machine_id=machine_id, exit=process.returncode,
                error=error.decode(errors="replace")[-1000:])
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        await asyncio.sleep(2)


async def reconcile(tasks, desired, start):
    retired = [tasks.pop(key) for key in list(tasks) if key not in desired]
    for task in retired:
        task.cancel()
    if retired:
        await asyncio.gather(*retired, return_exceptions=True)
    for key in desired:
        if key in tasks and tasks[key].done():
            error = tasks.pop(key).exception()
            log("worker_failed", machine_id=key[0], error=repr(error))
        if key not in tasks:
            tasks[key] = asyncio.create_task(start(key))


async def run(args):
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    tasks = {}
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log("started", backend_container=args.backend_container, backend_port=args.backend_port)
    try:
        while not stop.is_set():
            try:
                desired = await inventory(args.backend_container)
                await reconcile(tasks, desired,
                                lambda key: forward(key, args.state_dir, args.backend_port))
            except Exception as error:
                # A container rollout must not tear down established connections.
                log("inventory_failed", error=repr(error))
            try:
                await asyncio.wait_for(stop.wait(), 3)
            except TimeoutError:
                pass
    finally:
        await reconcile(tasks, {}, None)
        log("stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-container", default="cheese-backend-1")
    parser.add_argument("--backend-port", type=int, default=8081)
    parser.add_argument("--state-dir", type=Path,
                        default=Path.home() / ".local/state/cheese-cloud-control")
    asyncio.run(run(parser.parse_args()))
