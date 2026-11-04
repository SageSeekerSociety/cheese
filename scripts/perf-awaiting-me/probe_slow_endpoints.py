"""Which of the endpoints a room open calls is expensive per request, and where?

`/awaiting-me` was the first one measured (the task this script descends from).
A channel open fans out over more than one endpoint, so the same question is
asked of each of them here: how much **event-loop CPU** does one request cost?
CPU is `time.process_time`, not wall clock — one process, one loop, so this is
the occupancy that serialises when 20 requests arrive together, and a loaded
host does not change it (the host this ran on was loaded; wall clock would say
nothing).

Every request goes through the real app (routing, dependencies, response
serialisation) via an ASGI transport, so the number is a request's, not a
function call's. Unauthenticated knock-on endpoints (`/healthz` and friends) are
not in the list: the app's access-log middleware skips them.

Run from the repo root, with the perf database up and seeded (see README.md)::

    PERF_DB_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf \\
        uv run --project backend python scripts/perf-awaiting-me/probe_slow_endpoints.py

Environment: `PERF_DB_URL` (must also be given as `DATABASE_URL` to the app — the
script does that itself), `PERF_HANDLE` (default `perfviewer`), `PERF_ROUNDS`.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
ROUNDS = int(os.environ.get("PERF_ROUNDS", "7"))

# The app builds its engine at import time, so the URL has to be in the
# environment before anything under `app.` is imported.
os.environ.setdefault("DATABASE_URL", DB_URL)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET", "perf-probe-secret")
os.environ.setdefault(
    "REDIS_URL",
    os.environ.get("PERF_REDIS_URL") or os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0",
)


async def discover_ids() -> dict[str, str]:
    """A project, a room with a lot of work in it, and one task of that room.

    Picked from the database rather than from a fixture: the numbers are only
    interesting on the shape the tracker measures (a long-lived room), and this
    makes the choice visible in the output.
    """
    import asyncpg

    dsn = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        # A channel is a `topic`; its work lives in `tasks`, keyed by `room_id`.
        # The heaviest one is the shape worth measuring — a room alive for a
        # long time, so its task list is the expensive case.
        room = await conn.fetchval(
            """
            SELECT room_id FROM tasks
             GROUP BY room_id ORDER BY count(*) DESC, room_id LIMIT 1
            """
        )
        row = await conn.fetchrow("SELECT project_id FROM topics WHERE id = $1", room)
        if row is None:
            # A room whose conversation is not in `topics` (a task carrying
            # others): fall back to a project root.
            room = await conn.fetchval(
                "SELECT id FROM topics WHERE kind = 'root' ORDER BY id LIMIT 1"
            )
            row = await conn.fetchrow("SELECT project_id FROM topics WHERE id = $1", room)
        project = row["project_id"]
        task = await conn.fetchval(
            "SELECT id FROM tasks WHERE room_id = $1 ORDER BY id LIMIT 1", room
        )
        return {
            "project": str(project),
            "room": str(room),
            "task": str(task),
        }
    finally:
        await conn.close()


def endpoint_paths(ids: dict[str, str]) -> list[tuple[str, str, str]]:
    """The endpoints a channel open (or the rail beside it) actually calls."""
    room, project = ids["room"], ids["project"]
    return [
        ("channel detail", "GET", f"/topics/{room}"),
        ("channel task list", "GET", f"/topics/{room}/tasks?limit=0"),
        ("channel blocks", "GET", f"/topics/{room}/blocks?shown=true&limit=50"),
        ("channel environment", "GET", f"/topics/{room}/environment"),
        ("channel members", "GET", f"/topics/{room}/members"),
        ("channel pins", "GET", f"/topics/{room}/pins"),
        ("channel threads", "GET", f"/topics/{room}/threads"),
        ("project task list", "GET", f"/projects/{project}/tasks?status=open"),
        ("awaiting-me (reference)", "GET", "/awaiting-me"),
    ]


async def main() -> None:
    import httpx

    from app.common.auth import create_access_token
    from app.main import app

    ids = await discover_ids()
    print(f"ids: {json.dumps(ids)}")

    handle = VIEWER
    token = create_access_token(None, handle=handle)
    headers = {"Authorization": f"Bearer {token}"}

    paths = endpoint_paths(ids)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://perf.local", headers=headers, timeout=120.0
    ) as client:
        for label, method, path in paths:
            # Warm-up: statement compilation, page cache, connection.
            for _ in range(2):
                first = await client.request(method, path)
            if first.status_code != 200:
                print(f"{label:26} {path}  -> HTTP {first.status_code} {first.text[:160]}")
                continue

            samples: list[float] = []
            body_bytes = 0
            rows = None
            for _ in range(ROUNDS):
                start = time.process_time()
                response = await client.request(method, path)
                samples.append(time.process_time() - start)
                body_bytes = len(response.content)
            try:
                data = response.json()["data"]
                if isinstance(data, dict) and isinstance(data.get("data"), list):
                    rows = len(data["data"])
                elif isinstance(data, list):
                    rows = len(data)
            except Exception:  # noqa: BLE001 — the count is a nicety, not the answer
                rows = None
            print(
                f"{label:26} {path:52} cpu p50={statistics.median(samples)*1000:7.1f}ms "
                f"min={min(samples)*1000:7.1f}ms  {body_bytes/1024:8.1f} KiB  "
                f"rows={rows}"
            )


if __name__ == "__main__":
    asyncio.run(main())
