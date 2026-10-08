"""Observe a test command without changing its selection or result."""

import argparse
import asyncio
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path


def record(stream, **fields):
    stream.write(json.dumps({"at": datetime.now(UTC).isoformat(), **fields}) + "\n")
    stream.flush()


async def sample_postgres():
    import asyncpg

    settings_recorded = False
    # Connection details stay in the environment, never in the evidence.
    while True:
        connection = None
        try:
            dsn = os.environ["DATABASE_URL"].replace(
                "postgresql+asyncpg://", "postgresql://"
            )
            connection = await asyncpg.connect(
                dsn,
                timeout=3,
                command_timeout=3,
                server_settings={"application_name": "cheese-ci-resource-observer"},
            )
            if not settings_recorded:
                record(sys.stdout, fsync=await connection.fetchval("SHOW fsync"))
                settings_recorded = True
            rows = await connection.fetch(
                "SELECT backend_type, wait_event_type, wait_event, count(*) AS count "
                "FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
                "GROUP BY backend_type, wait_event_type, wait_event"
            )
            record(sys.stdout, waits=[dict(row) for row in rows])
        except Exception as error:
            record(sys.stdout, error=type(error).__name__)
        finally:
            if connection is not None:
                await connection.close(timeout=1)
        await asyncio.sleep(5)


def storage_snapshot(data, meminfo):
    usage = os.statvfs(data)
    available_kb = next(
        int(line.split()[1])
        for line in meminfo.read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    return {
        "filesystem_total_bytes": usage.f_blocks * usage.f_frsize,
        "filesystem_used_bytes": (usage.f_blocks - usage.f_bfree) * usage.f_frsize,
        "filesystem_available_bytes": usage.f_bavail * usage.f_frsize,
        "mem_available_bytes": available_kb * 1024,
    }


def sample_storage(postgres_pid):
    data = Path(f"/proc/{postgres_pid}/root/var/lib/postgresql/data")
    record(sys.stdout, event="sampler_started", pid=os.getpid(), interval_seconds=5)
    # Samples cover the entire data filesystem, including WAL.
    # Peaks between samples are unknown.
    while True:
        try:
            record(sys.stdout, **storage_snapshot(data, Path("/proc/meminfo")))
        except Exception as error:
            record(sys.stdout, error=type(error).__name__)
        time.sleep(5)


def disk_snapshot(paths):
    """Size and free space of the filesystem under each path."""
    disks = {}
    for path in paths:
        usage = os.statvfs(path)
        disks[str(path)] = {
            "total_bytes": usage.f_blocks * usage.f_frsize,
            "available_bytes": usage.f_bavail * usage.f_frsize,
        }
    return disks


def sample_disks(paths):
    # The runner's own disks, beside the Postgres tmpfs above: a run that
    # writes the root or $RUNNER_TEMP full says so here instead of only in a
    # test's OSError.
    record(sys.stdout, event="sampler_started", paths=paths, interval_seconds=5)
    while True:
        try:
            record(sys.stdout, disks=disk_snapshot(paths))
        except Exception as error:
            record(sys.stdout, error=type(error).__name__)
        time.sleep(5)


def observed_disks():
    paths = ["/", os.environ.get("RUNNER_TEMP"), tempfile.gettempdir()]
    return list(dict.fromkeys(path for path in paths if path))


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def observe(output, command, *, postgres=True):
    output.mkdir(parents=True, exist_ok=False)
    monitors = []
    streams = []
    command_process = None
    previous_handlers = {}
    with (output / "lifecycle.jsonl").open("x") as events:
        record(
            events,
            event="start",
            cpu_count=os.cpu_count(),
            machine=platform.machine(),
            system=platform.system(),
            release=platform.release(),
            identity={
                key: os.environ.get(key)
                for key in (
                    "RUNNER_NAME",
                    "RUNNER_OS",
                    "RUNNER_ARCH",
                    "GITHUB_SHA",
                    "GITHUB_RUN_ID",
                    "GITHUB_RUN_ATTEMPT",
                    "TEST_SHARD",
                )
            },
        )
        for name in ("cpuinfo", "meminfo"):
            source = Path("/proc") / name
            if source.exists():
                (output / name).write_text(source.read_text())

        def forward(signum, _frame):
            if command_process is not None:
                command_process.send_signal(signum)

        try:
            vmstat = shutil.which("vmstat")
            module = [sys.executable, "-m", "scripts.observe_ci_resources"]
            disks = [
                arg for path in observed_disks() for arg in ("--sample-disk", path)
            ]
            commands = [("disks", [*module, *disks])]
            if postgres:
                commands.append(("postgres", [*module, "--sample-postgres"]))
            if postgres_pid := os.environ.get("CHEESE_CI_POSTGRES_PID"):
                commands.append(
                    (
                        "storage",
                        [
                            "sudo",
                            "-n",
                            "/usr/bin/python3",
                            str(Path(__file__).resolve()),
                            "--sample-storage",
                            postgres_pid,
                        ],
                    )
                )
            if vmstat:
                commands.append(("vmstat", [vmstat, "-t", "1"]))
            else:
                record(events, event="unavailable", monitor="vmstat")
            for name, arguments in commands:
                stream = (output / f"{name}.log").open("x")
                streams.append(stream)
                try:
                    process = subprocess.Popen(
                        arguments, stdout=stream, stderr=subprocess.DEVNULL
                    )
                except OSError as error:
                    record(
                        events,
                        event="unavailable",
                        monitor=name,
                        error=type(error).__name__,
                    )
                    continue
                monitors.append((name, process))
                record(events, event="monitor_started", monitor=name, pid=process.pid)
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, forward)
            command_process = subprocess.Popen(command)
            code = command_process.wait()
            record(events, event="command_finished", returncode=code)
            return code if code >= 0 else 128 - code
        finally:
            if command_process is not None:
                stop(command_process)
            for name, process in monitors:
                stop(process)
                record(
                    events,
                    event="monitor_stopped",
                    monitor=name,
                    returncode=process.returncode,
                )
            for stream in streams:
                stream.close()
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            record(events, event="finished")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-postgres", action="store_true")
    parser.add_argument("--sample-storage", type=int)
    parser.add_argument("--sample-disk", action="append")
    parser.add_argument(
        "--no-postgres",
        action="store_true",
        help="the command has no database to watch (the pure layer)",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.sample_postgres:
        asyncio.run(sample_postgres())
    elif args.sample_storage:
        sample_storage(args.sample_storage)
    elif args.sample_disk:
        sample_disks(args.sample_disk)
    else:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        raise SystemExit(observe(args.output, command, postgres=not args.no_postgres))


if __name__ == "__main__":
    main()
