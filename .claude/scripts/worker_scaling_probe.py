"""What does a second worker buy at 20 concurrent requests?

``concurrent_read_probe.py`` runs the HTTP leg through one uvicorn process, which
is the shape the container ships with by default (`BACKEND_WORKERS=1` in
`backend/Dockerfile`, and the backend service in
`deploy/compose/docker-compose.base.yml` passes `${BACKEND_WORKERS:-1}` through,
so a box can raise it without a code change). Every endpoint the 2026-10-15
report still has over a second is over it because one event loop has to run
~40 ms of CPU per request 20 times over.

This runs the same 20-concurrency burst against 1, 2 and 3 workers and reports
wall clock and CPU for each. It exists because the answer to "how many workers
should the box run" cannot be read off a single-worker reading.

CPU is read from ``/proc`` over the whole **process tree** (`--workers N` puts
the serving loops in children, so the parent's own CPU is just the supervisor
and would understate the run by the entire cost being measured).

    PERF_DB_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf \\
    PERF_REDIS_URL=redis://127.0.0.1:6379/0 \\
        uv run --project backend python .claude/scripts/worker_scaling_probe.py

``PERF_WORKERS`` (``1,2``), ``PERF_ONLY``, ``PERF_BATCHES`` (5), ``PERF_CONCURRENCY``
(20), ``PERF_HTTP_PORT`` (5599) and ``PERF_OUT`` behave as in the probe. The host's
load average goes into the output: on a box already at load 60 the workers contend
for the same cores, and that has to be visible in whatever this reports.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "scripts"))
sys.path.insert(0, str(REPO / "backend"))

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
PORT = int(os.environ.get("PERF_HTTP_PORT", "5599"))
N = int(os.environ.get("PERF_CONCURRENCY", "20"))
BATCHES = int(os.environ.get("PERF_BATCHES", "5"))
WORKER_COUNTS = [
    int(w) for w in os.environ.get("PERF_WORKERS", "1,2").split(",") if w.strip()
]
ONLY = [s.strip() for s in os.environ.get("PERF_ONLY", "").split(",") if s.strip()]
OUT = os.environ.get("PERF_OUT", "")

os.environ.setdefault("DATABASE_URL", DB_URL)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET", "perf-probe-secret")
os.environ.setdefault(
    "REDIS_URL", os.environ.get("PERF_REDIS_URL", "redis://127.0.0.1:6379/0")
)

from concurrent_read_probe import discover_ids, endpoint_paths  # noqa: E402

CLK = os.sysconf("SC_CLK_TCK")


def _read_proc(pid: int) -> tuple[int, float] | None:
    """(ppid, cpu seconds) for one pid, or None if it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None  # exited between the listing and the read
    fields = stat[stat.rindex(")") + 2 :].split()
    ppid = int(fields[1])
    cpu = (int(fields[11]) + int(fields[12])) / CLK
    return ppid, cpu


def tree_cpu(supervisor: int) -> float:
    """CPU seconds burned by the supervisor and everything under it.

    Not matched on the command line: with ``--workers N`` uvicorn forks the
    serving loops off through ``multiprocessing``, and those children's
    ``cmdline`` is the spawn bootstrap rather than a uvicorn command line. The
    supervisor's own CPU is a fraction of a millisecond — it is the children,
    by ppid, that do the work.
    """
    parents: dict[int, int] = {}
    cpus: dict[int, float] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        got = _read_proc(int(entry.name))
        if got is not None:
            parents[int(entry.name)] = got[0]
            cpus[int(entry.name)] = got[1]

    total = 0.0
    for pid in cpus:
        walk = pid
        for _ in range(8):  # bounded: a live ppid chain is never deeper
            if walk == supervisor:
                total += cpus[pid]
                break
            walk = parents.get(walk, 0)
            if walk <= 1:
                break
    return total


async def burst(port: int, supervisor: int, paths: list[tuple[str, str]], results: dict) -> None:
    import httpx

    from app.common.auth import create_access_token

    limits = httpx.Limits(max_connections=N, max_keepalive_connections=N)
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", timeout=180.0, limits=limits
    ) as client:
        for _ in range(120):
            with contextlib.suppress(Exception):
                if (await client.get("/health")).status_code < 500:
                    break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("uvicorn never answered /health")

        token = create_access_token(None, handle=VIEWER)
        client.headers["Authorization"] = f"Bearer {token}"

        for label, path in paths:
            for _ in range(2):
                warm = await client.get(path)
            if warm.status_code != 200:
                print(f"  {label:26} -> HTTP {warm.status_code} {warm.text[:120]}")
                continue

            samples = []
            for _ in range(BATCHES):
                cpu0 = tree_cpu(supervisor)
                wall0 = time.perf_counter()
                done = await asyncio.gather(
                    *(client.get(path) for _ in range(N)), return_exceptions=True
                )
                wall = (time.perf_counter() - wall0) * 1000
                samples.append(
                    {
                        "wall_ms": wall,
                        "cpu_ms": (tree_cpu(supervisor) - cpu0) * 1000,
                        "errors": sum(1 for r in done if isinstance(r, Exception)),
                    }
                )
            results[label] = samples
            wall = statistics.median(s["wall_ms"] for s in samples)
            cpu = statistics.median(s["cpu_ms"] for s in samples)
            worst = max(s["wall_ms"] for s in samples)
            print(
                f"  {label:26} wall {wall:7.1f}  cpu {cpu:7.1f} "
                f"({cpu / wall * 100:4.1f}% of wall)  worst {worst:7.1f}"
            )


async def main() -> None:
    ids = await discover_ids()
    paths = [e for e in endpoint_paths(ids) if not ONLY or any(s in e[0] for s in ONLY)]
    all_results: dict = {}
    print(
        f"ids {json.dumps(ids)} concurrency={N} batches={BATCHES} "
        f"loadavg={Path('/proc/loadavg').read_text().strip()}"
    )

    for workers in WORKER_COUNTS:
        err_path = Path(f"/tmp/perf-uvicorn-w{workers}-{PORT}.log")
        proc = subprocess.Popen(
            [
                str(REPO / "backend/.venv/bin/python"),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
                "--workers",
                str(workers),
                "--log-level",
                "warning",
                "--app-dir",
                str(REPO / "backend"),
                "--lifespan",
                "off",
            ],
            cwd=str(REPO),
            env=dict(os.environ),
            stdout=subprocess.DEVNULL,
            stderr=err_path.open("wb"),
        )
        try:
            print(f"\n== {workers} worker(s) ==")
            results: dict = {}
            await burst(PORT, proc.pid, paths, results)
            all_results[str(workers)] = results
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=20)

    if OUT:
        Path(OUT).write_text(json.dumps(all_results, indent=2))
        print(f"\nraw samples -> {OUT}")


asyncio.run(main())
