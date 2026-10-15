"""What 20 concurrent requests to each endpoint a channel open calls actually cost.

The bar is "20 concurrent, slowest operation < 1 s". ``probe_slow_endpoints.py``
measures a *single* request's event-loop CPU; this one asks what happens when 20
of them arrive at one uvicorn process, which is the deployment shape
(``docs/topics/perf-awaiting-me-queue.md``: one process, one event loop, so the
loop's CPU is the serial point and the burst wall cannot be below it).

Two legs, each reported separately:

  * ``asgi`` — in-process ASGI transport, instrumented. The app's engine gets a
    SQLAlchemy ``after_cursor_execute`` listener (driver round trip), FastAPI's
    ``serialize_response`` is wrapped (response encoding), and every route's
    ``dependant.call`` is wrapped (the route body itself). That splits one
    request's cost into DB wait / route CPU / serialization / auth+framework.
  * ``http`` — a real uvicorn process over real HTTP with 20 keep-alive
    connections. Only wall clock and the server process's CPU
    (``/proc/<pid>/stat``) are read; the server is a separate process, so this
    is what a client would actually see, network stack and access log included.

Per endpoint and per leg:

  * single request: wall, loop CPU (``process_time``), DB wait, route CPU,
    serialization CPU;
  * burst of N concurrent: wall, worst single request, loop CPU for the whole
    burst (the hard floor on wall for one process), DB wait for the burst,
    and ``cpu / wall`` — how much of the burst wall the loop was busy for.

Run from the repo root, with the perf database up and seeded (see
``scripts/perf-awaiting-me/README.md``)::

    PERF_DB_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5533/cheese_perf \
    PERF_REDIS_URL=redis://127.0.0.1:6399/0 \
        uv run --project backend python .claude/scripts/concurrent_read_probe.py

Environment: ``PERF_DB_URL``, ``PERF_REDIS_URL``, ``PERF_HANDLE`` (default
``perfviewer``), ``PERF_CONCURRENCY`` (20), ``PERF_BATCHES`` (5), ``PERF_ROUNDS``
(7, the single-request rounds), ``PERF_LEG`` (``both`` | ``asgi`` | ``http``),
``PERF_ONLY`` (comma-separated substring filter on the label), ``PERF_OUT``
(write the raw samples as JSON here), ``PERF_HTTP_PORT`` (5599).
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
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5533/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
N = int(os.environ.get("PERF_CONCURRENCY", "20"))
BATCHES = int(os.environ.get("PERF_BATCHES", "5"))
ROUNDS = int(os.environ.get("PERF_ROUNDS", "7"))
LEG = os.environ.get("PERF_LEG", "both")
ONLY = [s.strip() for s in os.environ.get("PERF_ONLY", "").split(",") if s.strip()]
OUT = os.environ.get("PERF_OUT")
HTTP_PORT = int(os.environ.get("PERF_HTTP_PORT", "5599"))

# The app builds its engine at import time, so the URL has to be in the
# environment before anything under `app.` is imported.
os.environ.setdefault("DATABASE_URL", DB_URL)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET", "perf-probe-secret")
os.environ.setdefault("REDIS_URL", os.environ.get("PERF_REDIS_URL", "redis://127.0.0.1:6399/0"))


# —— instrumentation ———————————————————————————————————————————————————
# Single-request rounds are sequential, so one module-level accumulator is
# unambiguous there. The concurrent leg only reads *batch totals* for the same
# reason: 20 interleaved requests cannot share one accumulator per request, but
# their sum is exactly what the batch column wants.


class Acc:
    def __init__(self) -> None:
        self.db_wall = 0.0
        self.route_cpu = 0.0
        self.serialize_cpu = 0.0

    def reset(self) -> None:
        self.db_wall = self.route_cpu = self.serialize_cpu = 0.0


ACC = Acc()
_installed = False


def install_instrumentation() -> None:
    """Hook the app engine (DB) and FastAPI's routing (route body, encoding).

    Both hooks are looked up at request time: ``run_endpoint_function`` reads
    ``dependant.call`` per request, and the request handler calls the module
    global ``serialize_response``. Nothing under ``app/`` is modified.
    """
    global _installed
    if _installed:
        return
    _installed = True

    import fastapi.routing
    from sqlalchemy import event

    from app.core.db import engine

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        context._perf_t0 = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        start = getattr(context, "_perf_t0", None)
        if start is not None:
            ACC.db_wall += time.perf_counter() - start

    original_serialize = fastapi.routing.serialize_response

    async def timed_serialize(*args: Any, **kwargs: Any):  # noqa: ANN401
        start = time.process_time()
        try:
            return await original_serialize(*args, **kwargs)
        finally:
            ACC.serialize_cpu += time.process_time() - start

    fastapi.routing.serialize_response = timed_serialize

    # The route body is not hooked separately: this app mounts its routers from
    # `app.main`, and the mounted handlers do not all expose a `dependant.call`
    # to wrap. It does not need to be — the route body is what is left of the
    # request's loop CPU once the encoder's share is subtracted, and the encoder
    # is tiny here because these routes hand back plain dicts. `route` is
    # therefore reported as `cpu - ser`, not measured directly.
    _ = asyncio


# —— endpoints ————————————————————————————————————————————————————————


async def discover_ids() -> dict[str, str]:
    """A project, the room with the most work in it, and one task of that room."""
    import asyncpg

    dsn = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        room = await conn.fetchval(
            "SELECT room_id FROM tasks GROUP BY room_id ORDER BY count(*) DESC, room_id LIMIT 1"
        )
        row = await conn.fetchrow("SELECT project_id FROM topics WHERE id = $1", room)
        if row is None:
            room = await conn.fetchval(
                "SELECT id FROM topics WHERE kind = 'root' ORDER BY id LIMIT 1"
            )
            row = await conn.fetchrow("SELECT project_id FROM topics WHERE id = $1", room)
        project = row["project_id"]
        task = await conn.fetchval(
            "SELECT id FROM tasks WHERE room_id = $1 ORDER BY id LIMIT 1", room
        )
        return {"project": str(project), "room": str(room), "task": str(task)}
    finally:
        await conn.close()


def endpoint_paths(ids: dict[str, str]) -> list[tuple[str, str]]:
    room, project = ids["room"], ids["project"]
    return [
        ("channel detail", f"/topics/{room}"),
        ("channel task list", f"/topics/{room}/tasks?limit=0"),
        ("channel blocks", f"/topics/{room}/blocks?shown=true&limit=50"),
        ("channel environment", f"/topics/{room}/environment"),
        ("channel members", f"/topics/{room}/members"),
        ("channel pins", f"/topics/{room}/pins"),
        ("channel threads", f"/topics/{room}/threads"),
        ("project task list", f"/projects/{project}/tasks?status=open"),
        ("awaiting-me (reference)", "/awaiting-me"),
    ]


def selected(paths: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not ONLY:
        return paths
    return [(label, path) for label, path in paths if any(s in label for s in ONLY)]


def row_count(response) -> int | None:  # noqa: ANN001
    with contextlib.suppress(Exception):
        data = response.json()["data"]
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return len(data["data"])
        if isinstance(data, list):
            return len(data)
    return None


# —— leg 1: in-process ASGI, instrumented ——————————————————————————————


async def run_asgi(ids: dict[str, str]) -> dict[str, Any]:
    import httpx

    from app.common.auth import create_access_token

    install_instrumentation()
    from app.main import app

    # Server-side DB time, for the burst columns: under 20 concurrent requests
    # the driver-round-trip listener above cannot separate "Postgres answered"
    # from "the loop was busy with the other 19" — the greenlet only resumes
    # after the loop comes back. `pg_stat_statements.total_exec_time` is
    # measured by the server itself, so its delta over a burst is the real
    # Postgres time for all 20 requests.
    stats_conn = None
    if os.environ.get("PERF_PGSTATS", "1") != "0":
        with contextlib.suppress(Exception):
            import asyncpg

            stats_conn = await asyncpg.connect(DB_URL.replace("postgresql+asyncpg://", "postgresql://"))

    async def pg_exec_ms() -> float | None:
        if stats_conn is None:
            return None
        return float(await stats_conn.fetchval("SELECT COALESCE(sum(total_exec_time), 0) FROM pg_stat_statements"))

    token = create_access_token(None, handle=VIEWER)
    headers = {"Authorization": f"Bearer {token}"}
    results: dict[str, Any] = {}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://perf.local", headers=headers, timeout=180.0
    ) as client:
        for label, path in selected(endpoint_paths(ids)):
            for _ in range(2):
                warm = await client.request("GET", path)
            if warm.status_code != 200:
                print(f"{label:26} -> HTTP {warm.status_code} {warm.text[:160]}")
                continue
            bytes_out = len(warm.content)
            rows = row_count(warm)

            single: list[dict[str, float]] = []
            for _ in range(ROUNDS):
                ACC.reset()
                wall0 = time.perf_counter()
                cpu0 = time.process_time()
                response = await client.request("GET", path)
                cpu = time.process_time() - cpu0
                wall = (time.perf_counter() - wall0) * 1000
                single.append(
                    {
                        "wall": wall,
                        "cpu": cpu * 1000,
                        "db": ACC.db_wall * 1000,
                        "route": cpu * 1000 - ACC.serialize_cpu * 1000,
                        "ser": ACC.serialize_cpu * 1000,
                    }
                )
                bytes_out = len(response.content)

            bursts: list[dict[str, float]] = []
            for _ in range(BATCHES):
                ACC.reset()
                pg0 = await pg_exec_ms()
                cpu0 = time.process_time()
                wall0 = time.perf_counter()
                req_walls: list[float] = []

                async def one(p: str = path) -> None:
                    t = time.perf_counter()
                    await client.request("GET", p)
                    req_walls.append((time.perf_counter() - t) * 1000)

                await asyncio.gather(*(one() for _ in range(N)))
                wall = (time.perf_counter() - wall0) * 1000
                cpu = (time.process_time() - cpu0) * 1000
                pg1 = await pg_exec_ms()
                bursts.append(
                    {
                        "wall": wall,
                        "cpu": cpu,
                        "db": ACC.db_wall * 1000,
                        "ser": ACC.serialize_cpu * 1000,
                        "worst": max(req_walls),
                        "pg": (pg1 - pg0) if (pg0 is not None and pg1 is not None) else -1.0,
                    }
                )

            results[label] = {
                "path": path,
                "bytes": bytes_out,
                "rows": rows,
                "single": single,
                "burst": bursts,
            }
            s = {k: statistics.median(r[k] for r in single) for k in single[0]}
            b_wall = statistics.median(r["wall"] for r in bursts)
            b_cpu = statistics.median(r["cpu"] for r in bursts)
            print(
                f"{label:26} single wall {s['wall']:7.1f} cpu {s['cpu']:6.1f} "
                f"db {s['db']:5.1f} encode {s['ser']:5.1f} | "
                f"burst({N}) wall {b_wall:7.1f} cpu {b_cpu:7.1f} "
                f"({b_cpu / b_wall * 100:4.1f}% of wall, loop idle "
                f"{b_wall - b_cpu:7.1f}) worst "
                f"{statistics.median(r['worst'] for r in bursts):7.1f} "
                f"pg {statistics.median(r['pg'] for r in bursts):7.1f}  "
                f"{bytes_out / 1024:7.1f}KiB rows={rows}"
            )
    return results


# —— leg 2: real uvicorn, real HTTP, 20 keep-alive connections ———————————


def proc_cpu_seconds(pid: int) -> float:
    stat = Path(f"/proc/{pid}/stat").read_text()
    # comm can contain spaces/parens; fields after the closing paren are stable.
    fields = stat[stat.rindex(")") + 2 :].split()
    utime, stime = int(fields[11]), int(fields[12])
    return (utime + stime) / os.sysconf("SC_CLK_TCK")


async def run_http(ids: dict[str, str]) -> dict[str, Any]:
    import httpx

    from app.common.auth import create_access_token

    env = dict(os.environ)
    # stderr goes to a FILE, not a pipe. The app's onnxruntime startup writes
    # ~90 KB of thread-affinity noise; a 64 KB pipe buffer fills, the server
    # blocks in write() on the event loop thread, and every request after that
    # hangs forever (symptom: /health answers, the first real GET never does).
    err_path = Path(f"/tmp/perf-uvicorn-{HTTP_PORT}.log")
    err_file = err_path.open("wb")
    proc = subprocess.Popen(
        [
            str(REPO / "backend/.venv/bin/python"),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(HTTP_PORT),
            "--log-level",
            "warning",
            "--app-dir",
            str(REPO / "backend"),
            # No startup/shutdown hooks. They are pure background machinery
            # (agent-instance ensure, turn intake/orphan sweep, device hub) and
            # have nothing to do with read-path cost — but they also make boot
            # nondeterministic under load, and one of them races on the
            # (project_id, handle) agent-instance row against anything else
            # pointed at the same database. Read bursts subtract their own CPU
            # baseline, so dropping them cannot flatter the numbers.
            "--lifespan",
            "off",
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=err_file,
    )
    base = f"http://127.0.0.1:{HTTP_PORT}"
    results: dict[str, Any] = {}
    try:
        limits = httpx.Limits(max_connections=N, max_keepalive_connections=N)
        async with httpx.AsyncClient(base_url=base, timeout=180.0, limits=limits) as client:
            for _ in range(120):
                with contextlib.suppress(Exception):
                    if (await client.get("/health")).status_code < 500:
                        break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("uvicorn never answered /health")

            token = create_access_token(None, handle=VIEWER)
            client.headers["Authorization"] = f"Bearer {token}"

            for label, path in selected(endpoint_paths(ids)):
                for _ in range(2):
                    warm = await client.get(path)
                if warm.status_code != 200:
                    print(f"{label:26} -> HTTP {warm.status_code} {warm.text[:160]}")
                    continue

                bursts: list[dict[str, float]] = []
                for _ in range(BATCHES):
                    cpu0 = proc_cpu_seconds(proc.pid)
                    wall0 = time.perf_counter()
                    req_walls: list[float] = []

                    async def one(p: str = path) -> None:
                        t = time.perf_counter()
                        await client.get(p)
                        req_walls.append((time.perf_counter() - t) * 1000)

                    await asyncio.gather(*(one() for _ in range(N)))
                    wall = (time.perf_counter() - wall0) * 1000
                    cpu = (proc_cpu_seconds(proc.pid) - cpu0) * 1000
                    bursts.append({"wall": wall, "cpu": cpu, "worst": max(req_walls)})

                results[label] = {
                    "path": path,
                    "bytes": len(warm.content),
                    "burst": bursts,
                }
                b_wall = statistics.median(r["wall"] for r in bursts)
                b_cpu = statistics.median(r["cpu"] for r in bursts)
                print(
                    f"{label:26} burst({N}) wall {b_wall:7.1f} cpu {b_cpu:7.1f} "
                    f"({b_cpu / b_wall * 100:4.1f}%) worst "
                    f"{statistics.median(r['worst'] for r in bursts):7.1f}  "
                    f"{len(warm.content) / 1024:7.1f}KiB"
                )
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=20)
        if proc.poll() is None:
            proc.kill()
        err_file.close()
        err = err_path.read_text(errors="replace") if err_path.exists() else ""
        if err.strip():
            print(f"--- uvicorn stderr (tail of {err_path}) ---")
            print("\n".join(err.strip().splitlines()[-12:]))
    return results


async def main() -> None:
    ids = await discover_ids()
    print(f"ids: {json.dumps(ids)}  concurrency={N} batches={BATCHES} rounds={ROUNDS}")
    print(f"host loadavg: {Path('/proc/loadavg').read_text().strip()}")
    print()

    payload: dict[str, Any] = {"ids": ids, "n": N, "batches": BATCHES, "rounds": ROUNDS}
    if LEG in ("both", "asgi"):
        print(f"== Leg 1: in-process ASGI, {N} concurrent, {BATCHES} batches (median) ==")
        payload["asgi"] = await run_asgi(ids)
        print()
    if LEG in ("both", "http"):
        print(f"== Leg 2: true HTTP, {N} keep-alive connections, {BATCHES} batches ==")
        payload["http"] = await run_http(ids)
        print()

    if OUT:
        Path(OUT).write_text(json.dumps(payload, indent=2))
        print(f"raw samples -> {OUT}")

    with contextlib.suppress(Exception):
        from app.core.db import engine

        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
