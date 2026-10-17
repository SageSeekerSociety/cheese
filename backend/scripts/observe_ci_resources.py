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

GIB = 1024**3

# A run is judged against the memory its own box allows, not a fixed number: a
# hosted runner has 16 GiB while a remote sandbox ends a session at its cgroup
# cap (docs/remote-execution.md: 3 GiB, no swap by default). This value is the
# last resort when neither the cgroup nor /proc/meminfo says anything.
SANDBOX_MEMORY_LIMIT_BYTES = 3 * GIB
# Within a tenth of the cap a run is one allocation from disappearing, so that
# is the line reported.
MEMORY_HEADROOM_RATIO = 0.1
CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")
PROC_CPUINFO = Path("/proc/cpuinfo")
PROC_STAT = Path("/proc/stat")

# Runnable processes beyond the CPUs are queued, and steal time says the box's
# neighbours are spending the vCPUs this run was promised.
CPU_STEAL_LIMIT_PERCENT = 5.0

# PostgreSQL keeps this many of `max_connections` for superusers; once the
# rest are taken, new clients wait. `SHOW` reports the real value when known.
SUPERUSER_RESERVED_CONNECTIONS = 3


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
                record(
                    sys.stdout,
                    settings={
                        "fsync": await connection.fetchval("SHOW fsync"),
                        "max_connections": int(
                            await connection.fetchval("SHOW max_connections")
                        ),
                        "superuser_reserved_connections": int(
                            await connection.fetchval(
                                "SHOW superuser_reserved_connections"
                            )
                        ),
                    },
                )
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


def sample_memory():
    # The cgroup counts this box's own pages, including the children, which is
    # the only number a container cap can be compared with. Sampling here keeps
    # the peak out of the storage sampler, which needs a Postgres to exist.
    record(sys.stdout, event="sampler_started", pid=os.getpid(), interval_seconds=1)
    while True:
        try:
            record(sys.stdout, mem_used_bytes=cgroup_memory_usage())
        except Exception as error:
            record(sys.stdout, error=type(error).__name__)
        time.sleep(1)


def observed_disks():
    paths = ["/", os.environ.get("RUNNER_TEMP"), tempfile.gettempdir()]
    return list(dict.fromkeys(path for path in paths if path))


def read_jsonl(path):
    """The objects a sampler wrote, ignoring a line it did not finish."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    samples = []
    for line in lines:
        try:
            sample = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(sample, dict):
            samples.append(sample)
    return samples


def read_text(path):
    try:
        return path.read_text()
    except OSError:
        return ""


VMSTAT_COLUMNS = (
    "r",
    "b",
    "swpd",
    "free",
    "buff",
    "cache",
    "si",
    "so",
    "bi",
    "bo",
    "in",
    "cs",
    "us",
    "sy",
    "id",
    "wa",
    "st",
)


def parse_vmstat(text):
    """vmstat's numeric rows as dicts keyed by its column names."""
    rows = []
    for line in text.splitlines():
        values = line.split()
        if len(values) < len(VMSTAT_COLUMNS):
            continue
        try:
            numbers = [int(value) for value in values[: len(VMSTAT_COLUMNS)]]
        except ValueError:
            continue  # the two header lines
        rows.append(dict(zip(VMSTAT_COLUMNS, numbers, strict=True)))
    return rows


def read_mem_total(meminfo):
    for line in read_text(meminfo).splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    return None


def memory_peak_bytes(
    memory_samples, storage_samples, vmstat_rows, mem_total_bytes, limit_bytes
):
    """The highest memory use any sample saw, or None if none measured it."""
    container_peaks = [
        sample["mem_used_bytes"]
        for sample in memory_samples
        if isinstance(sample.get("mem_used_bytes"), int)
    ]
    if container_peaks:
        # A cgroup sample is this box's own use; host-wide numbers would make
        # a container cap meaningless.
        return max(container_peaks)
    if mem_total_bytes is None:
        return None
    if limit_bytes < mem_total_bytes:
        # The box is capped below the machine's memory and no scoped sample
        # survived: host-wide numbers would judge the neighbours, and the
        # cgroup's use right now is not the peak. Leave it unjudged.
        return None
    peaks = [
        max(0, mem_total_bytes - (row["free"] + row["buff"] + row["cache"]) * 1024)
        for row in vmstat_rows
    ]
    peaks += [
        max(0, mem_total_bytes - sample["mem_available_bytes"])
        for sample in storage_samples
        if isinstance(sample.get("mem_available_bytes"), int)
    ]
    return max(peaks) if peaks else None


def cpu_peak(vmstat_rows):
    """The runnable queue and the steal share at their worst."""
    if not vmstat_rows:
        return None, None
    # `r` is instantaneous in every report, so all of them describe this run.
    runnable = max(row["r"] for row in vmstat_rows)
    # The steal percentage in the first report averages since boot, not this
    # run. A run that ends inside one second only ever has that report, and
    # its steal says nothing about the run, so it stays unknown.
    later = vmstat_rows[1:]
    steal = float(max(row["st"] for row in later)) if later else None
    return runnable, steal


def processor_count():
    """How many CPUs the machine vmstat measures has, as the kernel reports it.

    `os.cpu_count()` follows the process's affinity mask, so on a shared box it
    counts the cores this run may use, while `vmstat`'s queue counts the whole
    machine. The queue is only meaningful against the machine's own count.
    """
    processors = sum(
        1
        for line in read_text(PROC_CPUINFO).splitlines()
        if line.startswith("processor")
    )
    if not processors:
        processors = sum(
            1
            for line in read_text(PROC_STAT).splitlines()
            if line.startswith("cpu") and line[3:4].isdigit()
        )
    return processors or os.cpu_count() or 1


def database_peak(postgres_samples):
    """The most client backends and the server's usable connection slots."""
    limit = None
    peak = None
    for sample in postgres_samples:
        settings = sample.get("settings")
        if isinstance(settings, dict):
            try:
                max_connections = int(settings["max_connections"])
                reserved = int(
                    settings.get(
                        "superuser_reserved_connections",
                        SUPERUSER_RESERVED_CONNECTIONS,
                    )
                )
            except (KeyError, TypeError, ValueError):
                pass
            else:
                limit = max_connections - reserved
        waits = sample.get("waits")
        if isinstance(waits, list):
            clients = sum(
                row["count"]
                for row in waits
                if isinstance(row, dict)
                and row.get("backend_type") == "client backend"
                and isinstance(row.get("count"), int)
            )
            peak = clients if peak is None else max(peak, clients)
    return peak, limit


def cgroup_dirs():
    """This process's own cgroup first, then the hierarchy root.

    A sandbox usually does not own the cgroup namespace, so the files under
    `/sys/fs/cgroup` describe the host; only the path named in
    `/proc/self/cgroup` is the box's own. A cgroup v1 controller is mounted
    under a directory of its own, so its path carries that directory.
    """
    directories = []
    for line in read_text(PROC_SELF_CGROUP).splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy, controllers, relative = parts
        relative = relative.strip("/")
        if hierarchy == "0":
            directories.append(CGROUP_ROOT / relative)
        elif "memory" in controllers.split(","):
            directories.append(CGROUP_ROOT / "memory" / relative)
    directories.append(CGROUP_ROOT)
    return directories


def cgroup_ancestors(directory):
    """`directory` and every cgroup above it, up to the hierarchy root."""
    directories = [directory]
    while directory != CGROUP_ROOT and CGROUP_ROOT in directory.parents:
        directory = directory.parent
        directories.append(directory)
    return directories


def _cgroup_int(directory, names):
    for name in names:
        try:
            value = (directory / name).read_text().strip()
        except OSError:
            continue
        return int(value) if value.isdigit() else None
    return None


def _memory_stat_int(directory, names):
    for line in read_text(directory / "memory.stat").splitlines():
        name, _, value = line.partition(" ")
        if name in names and value.strip().isdigit():
            return int(value)
    return None


def cgroup_memory_usage():
    """Bytes this cgroup holds that the kernel cannot drop, or None.

    `memory.current` also counts page cache, and the kernel reclaims that
    before it kills anything at the cap: a run that writes files reaches the
    cap with almost no real pressure. Leaving the reclaimable file pages out
    is what makes the number comparable with the cap.
    """
    for directory in cgroup_dirs():
        value = _cgroup_int(directory, ("memory.current", "memory.usage_in_bytes"))
        if value is None:
            continue
        reclaimable = _memory_stat_int(
            directory, ("inactive_file", "total_inactive_file")
        )
        if reclaimable is None:
            return value
        return max(0, value - reclaimable)
    return None


def cgroup_memory_limit(mem_total_bytes):
    """The tightest memory cap on this process's cgroup path.

    cgroup v2 enforces the minimum along the path from the cgroup to the
    root, so a child whose own `memory.max` says "max" is still capped by an
    ancestor.
    """
    limits = []
    for directory in cgroup_dirs():
        for ancestor in cgroup_ancestors(directory):
            for name in ("memory.max", "memory.limit_in_bytes"):
                try:
                    value = int((ancestor / name).read_text().strip())
                except (OSError, ValueError):
                    continue
                # cgroup v1 spells "unlimited" as a number just under 2**63;
                # anything at or above the machine's memory is no cap at all.
                if mem_total_bytes is None or value < mem_total_bytes:
                    limits.append(value)
    return min(limits) if limits else None


def memory_limit_bytes(mem_total_bytes=None):
    """The cap to compare against: this box's, whichever layer sets it."""
    try:
        return int(os.environ["CHEESE_CI_SANDBOX_MEMORY_BYTES"])
    except (KeyError, ValueError):
        pass
    limit = cgroup_memory_limit(mem_total_bytes)
    if limit is not None:
        return limit
    return mem_total_bytes or SANDBOX_MEMORY_LIMIT_BYTES


def summarize(output):
    """One observation of the run's peak memory, CPU queue and connections."""
    output = Path(output)
    lifecycle = read_jsonl(output / "lifecycle.jsonl")
    start = next((event for event in lifecycle if event.get("event") == "start"), {})
    storage = read_jsonl(output / "storage.log")
    memory = read_jsonl(output / "memory.log")
    vmstat_rows = parse_vmstat(read_text(output / "vmstat.log"))
    postgres = read_jsonl(output / "postgres.log")
    mem_total_bytes = read_mem_total(output / "meminfo")
    limit_bytes = memory_limit_bytes(mem_total_bytes)
    runnable_peak, steal_peak = cpu_peak(vmstat_rows)
    backends_peak, connection_limit = database_peak(postgres)
    return {
        # vmstat counts runnable processes across the whole machine, so the
        # queue is judged against that machine, not this box's affinity.
        "cpu_count": (
            start.get("processors") or start.get("cpu_count") or os.cpu_count() or 1
        ),
        "memory_total_bytes": mem_total_bytes,
        "memory_peak_bytes": memory_peak_bytes(
            memory, storage, vmstat_rows, mem_total_bytes, limit_bytes
        ),
        "memory_limit_bytes": limit_bytes,
        "postgres_watched": start.get("postgres", True),
        "runnable_peak": runnable_peak,
        "steal_peak_percent": steal_peak,
        "client_backends_peak": backends_peak,
        "db_connection_limit": connection_limit,
        "samples": {
            "storage": len(storage),
            "memory": len(memory),
            "vmstat": len(vmstat_rows),
            "postgres": len(postgres),
        },
    }


def _state(observed, threshold):
    if observed is None or threshold is None:
        return "unknown"
    return "over" if observed > threshold else "ok"


def _conclusion(total, crossed, unknown):
    if len(unknown) == total:
        return "not judged: no sample for any threshold"
    if crossed:
        verdict = f"{len(crossed)} of {total} checks crossed: {', '.join(crossed)}"
    else:
        verdict = "no threshold crossed"
    if unknown:
        return f"{verdict}; not judged: {', '.join(unknown)}"
    return verdict


def _next_steps(crossed, unknown, postgres_watched=True):
    steps = []
    if "memory" in crossed:
        steps.append("narrow the run: fewer selected files or workers")
    if "cpu" in crossed:
        steps.append("rerun on another shard or a quieter runner")
    if "database" in crossed:
        steps.append("narrow the run or raise the database's connection budget")
    if crossed:
        steps.append("hand the run to CI when the local box is shared")
    # A pure-layer run never starts Postgres, so it can never collect that
    # sample; asking it to is advice with no way to follow it.
    missing = [metric for metric in unknown if metric != "database" or postgres_watched]
    if missing:
        steps.append("collect a sample before judging the missing checks")
    if not steps:
        steps.append("no action needed")
    return steps


def evaluate(summary):
    """Judge the summary against the sandbox, CPU and connection thresholds."""
    memory_threshold = int(summary["memory_limit_bytes"] * (1 - MEMORY_HEADROOM_RATIO))
    runnable_over = (
        summary["runnable_peak"] is not None
        and summary["runnable_peak"] > summary["cpu_count"]
    )
    steal_over = (
        summary["steal_peak_percent"] is not None
        and summary["steal_peak_percent"] > CPU_STEAL_LIMIT_PERCENT
    )
    cpu_over = runnable_over or steal_over
    if cpu_over:
        cpu_state = "over"
    elif summary["runnable_peak"] is None and summary["steal_peak_percent"] is None:
        cpu_state = "unknown"
    else:
        cpu_state = "ok"
    checks = [
        {
            "metric": "memory",
            "state": _state(summary["memory_peak_bytes"], memory_threshold),
            "observed": summary["memory_peak_bytes"],
            "threshold": memory_threshold,
        },
        {
            "metric": "cpu",
            "state": cpu_state,
            # The record names which signal crossed: the numbers beside it
            # describe the queue only, and steal alone can set the state.
            "over_by": "runnable"
            if runnable_over
            else ("steal" if steal_over else None),
            "observed": summary["runnable_peak"],
            "threshold": summary["cpu_count"],
            "steal_percent": summary["steal_peak_percent"],
            "steal_limit_percent": CPU_STEAL_LIMIT_PERCENT,
        },
        {
            "metric": "database",
            "state": _state(
                summary["client_backends_peak"], summary["db_connection_limit"]
            ),
            "observed": summary["client_backends_peak"],
            "threshold": summary["db_connection_limit"],
        },
    ]
    crossed = [check["metric"] for check in checks if check["state"] == "over"]
    unknown = [check["metric"] for check in checks if check["state"] == "unknown"]
    return {
        "checks": checks,
        "crossed": crossed,
        "unknown": unknown,
        "conclusion": _conclusion(len(checks), crossed, unknown),
        "next_steps": _next_steps(
            crossed, unknown, summary.get("postgres_watched", True)
        ),
    }


def _gib(value):
    return f"{value / GIB:.2f} GiB"


def _describe(check, summary):
    state = check["state"]
    if check["metric"] == "memory":
        if state == "unknown":
            return "memory: unknown — no memory sample"
        return (
            f"memory: {state} — peak {_gib(check['observed'])}, "
            f"threshold {_gib(check['threshold'])} "
            f"(cap {_gib(summary['memory_limit_bytes'])})"
        )
    if check["metric"] == "cpu":
        steal = summary["steal_peak_percent"]
        steal_text = (
            "no steal sample"
            if steal is None
            else f"steal {steal:.1f}% (limit {CPU_STEAL_LIMIT_PERCENT:.1f}%)"
        )
        if check["observed"] is None:
            return f"cpu: {state} — no runnable sample; {steal_text}"
        return (
            f"cpu: {state} — peak {check['observed']} runnable on "
            f"{summary['cpu_count']} CPUs; {steal_text}"
        )
    if state == "unknown":
        return "database: unknown — no connection sample"
    return (
        f"database: {state} — peak {check['observed']} client backends, "
        f"limit {check['threshold']}"
    )


def report_lines(summary, verdict):
    lines = [f"resources: {verdict['conclusion']}"]
    lines += [f"  {_describe(check, summary)}" for check in verdict["checks"]]
    lines.append(f"resources next: {'; '.join(verdict['next_steps'])}")
    return lines


def print_report(summary, verdict):
    print("\n".join(report_lines(summary, verdict)))


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
            processors=processor_count(),
            cpu_count=os.cpu_count(),
            postgres=postgres,
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
            commands = [
                ("disks", [*module, *disks]),
                ("memory", [*module, "--sample-memory"]),
            ]
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
            # Judge the run only after its samples are closed, and never let
            # the judgement itself change the command's result.
            try:
                summary = summarize(output)
                verdict = evaluate(summary)
                record(
                    events,
                    event="resources_summary",
                    summary=summary,
                    verdict=verdict,
                )
                print_report(summary, verdict)
            except Exception as error:
                record(events, event="summary_failed", error=type(error).__name__)
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
            record(events, event="finished")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-postgres", action="store_true")
    parser.add_argument("--sample-memory", action="store_true")
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
    elif args.sample_memory:
        sample_memory()
    elif args.sample_storage:
        sample_storage(args.sample_storage)
    elif args.sample_disk:
        sample_disks(args.sample_disk)
    else:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        raise SystemExit(observe(args.output, command, postgres=not args.no_postgres))


if __name__ == "__main__":
    main()
