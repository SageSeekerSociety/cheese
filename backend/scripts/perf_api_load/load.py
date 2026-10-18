"""Drive the five read paths at 1 / 50 / 200 concurrent callers and report tails.

What it measures, per endpoint and per concurrency level:

  * wall latency p50/p95/p99 (what a caller sees);
  * requests per second (what one backend process absorbs);
  * the backend process's own CPU time (utime+stime from /proc), so the number
    is the work the one event loop had to do, not the sandbox's scheduling.

The server is a real uvicorn process; this script only sends HTTP. Point it at
a backend started from the same tree and seeded with ``seed.py``::

    PERF_PROJECT_ID=... PERF_ROOM_ID=... PERF_TASK_ID=... \\
        uv run --project backend python backend/scripts/perf_api_load/load.py \\
        --base http://127.0.0.1:18091 --server-pid <uvicorn pid>

``--server-pid`` is optional; without it CPU columns are omitted. The token is
minted in-process from the same ``JWT_SECRET`` the server was started with.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from app.common.auth import create_access_token  # noqa: E402

PROJECT_ID = os.environ["PERF_PROJECT_ID"]
ROOM_ID = os.environ["PERF_ROOM_ID"]
TASK_ID = os.environ["PERF_TASK_ID"]
HANDLE = os.environ.get("PERF_HANDLE", "perfviewer")
USER_ID = int(os.environ.get("PERF_USER_ID", "9"))

ENDPOINTS = [
    ("topics.list", "GET", f"/topics?project_id={PROJECT_ID}"),
    ("history.page", "GET", f"/topics/{ROOM_ID}/history?limit=50"),
    ("history.channel", "GET", f"/topics/{ROOM_ID}/history?limit=50&channel=true"),
    ("tasks.list", "GET", f"/projects/{PROJECT_ID}/tasks"),
    ("accept-card", "GET", f"/topics/{TASK_ID}/accept-card"),
]

CLOCKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def cpu_seconds(pid: int) -> float | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
    except OSError:
        return None
    return (int(fields[13]) + int(fields[14])) / CLOCKS


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * len(ordered))) - 1))
    return ordered[index]


async def one(
    client: httpx.AsyncClient, headers: dict, method: str, path: str
) -> tuple[float, int]:
    started = time.perf_counter()
    try:
        response = await client.request(method, path, headers=headers)
        return time.perf_counter() - started, response.status_code
    except httpx.HTTPError:
        # A dropped connection is a result, not a reason to abandon the whole
        # scenario: at 200 callers the server may close one keep-alive
        # connection while answering the rest, and losing the other 199
        # samples would hide the tail we came to measure.
        return time.perf_counter() - started, -1


async def scenario(
    *,
    base: str,
    headers: dict,
    method: str,
    path: str,
    concurrency: int,
    total: int,
    server_pid: int | None,
) -> dict:
    limits = httpx.Limits(
        max_connections=concurrency + 20, max_keepalive_connections=concurrency + 20
    )
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: list[int] = []

    async with httpx.AsyncClient(base_url=base, timeout=120.0, limits=limits) as client:
        # Warm connections and statement cache before the timed burst.
        for _ in range(min(concurrency, 8)):
            await one(client, headers, method, path)

        async def task() -> None:
            async with sem:
                wall, status = await one(client, headers, method, path)
                latencies.append(wall)
                statuses.append(status)

        before = cpu_seconds(server_pid) if server_pid else None
        started = time.perf_counter()
        await asyncio.gather(*(task() for _ in range(total)))
        elapsed = time.perf_counter() - started
        after = cpu_seconds(server_pid) if server_pid else None

    ok = sum(1 for s in statuses if s == 200)
    return {
        "concurrency": concurrency,
        "requests": total,
        "ok": ok,
        "errors": total - ok,
        "rps": round(total / elapsed, 1),
        "p50_ms": round(percentile(latencies, 0.50) * 1000, 1),
        "p95_ms": round(percentile(latencies, 0.95) * 1000, 1),
        "p99_ms": round(percentile(latencies, 0.99) * 1000, 1),
        "mean_ms": round(statistics.mean(latencies) * 1000, 1),
        "cpu_s": round(after - before, 2)
        if before is not None and after is not None
        else None,
        "cpu_ms_per_req": (
            round((after - before) * 1000 / total, 1)
            if before is not None and after is not None
            else None
        ),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:18091")
    parser.add_argument("--server-pid", type=int, default=None)
    parser.add_argument("--levels", default="1,50,200")
    parser.add_argument(
        "--per-level",
        type=int,
        default=0,
        help="requests per level; 0 = 10x concurrency",
    )
    parser.add_argument(
        "--endpoints", default="", help="comma-separated labels; default all"
    )
    args = parser.parse_args()

    wanted = {label for label in args.endpoints.split(",") if label}
    endpoints = [e for e in ENDPOINTS if not wanted or e[0] in wanted]
    levels = [int(level) for level in args.levels.split(",")]

    token = create_access_token(USER_ID, handle=HANDLE)
    headers = {"Authorization": f"Bearer {token}"}
    report = []
    for label, method, path in endpoints:
        for concurrency in levels:
            total = args.per_level or max(30, concurrency * 10)
            result = await scenario(
                base=args.base,
                headers=headers,
                method=method,
                path=path,
                concurrency=concurrency,
                total=total,
                server_pid=args.server_pid,
            )
            row = {"endpoint": label, "path": path, **result}
            report.append(row)
            print(
                f"{label:14s} c={concurrency:3d} rps={row['rps']:7.1f}"
                f" p50={row['p50_ms']:7.1f} p95={row['p95_ms']:8.1f}"
                f" p99={row['p99_ms']:8.1f} err={row['errors']}"
                f" cpu/req={row['cpu_ms_per_req']}",
                flush=True,
            )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
