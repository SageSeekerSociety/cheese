#!/usr/bin/env python3
"""Where does the loop CPU of one read endpoint actually go?

``concurrent_read_probe.py`` says how MANY milliseconds each endpoint burns on
the event loop under 20-concurrency; this says WHAT burns them. It runs the
same in-process ASGI burst, under ``cProfile``, and prints the cumulative time
by function for the burst as a whole.

cProfile has no idea about asyncio: it attributes a coroutine's cost to the
``coro.send``/``await`` frames in ``asyncio`` and ``httpx``, so the report is
read "inside the app" versus "inside the plumbing". Filter with
``PERF_ONLY=<substring>`` on the endpoint label.

  PERF_ONLY="project task list" PERF_CONCURRENCY=20 PERF_BURSTS=3 \
    uv run --project backend python .claude/scripts/profile_burst.py

Environment is the same as ``concurrent_read_probe.py``.
"""

from __future__ import annotations

import asyncio
import cProfile
import io
import json
import os
import pstats
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5533/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
N = int(os.environ.get("PERF_CONCURRENCY", "20"))
BURSTS = int(os.environ.get("PERF_BURSTS", "3"))
ONLY = [s.strip() for s in os.environ.get("PERF_ONLY", "project task list").split(",") if s.strip()]
TOP = int(os.environ.get("PERF_PROFILE_TOP", "28"))

os.environ.setdefault("DATABASE_URL", DB_URL)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET", "perf-probe-secret")
os.environ.setdefault(
    "REDIS_URL", os.environ.get("PERF_REDIS_URL", "redis://127.0.0.1:6399/0")
)

sys.path.insert(0, str(REPO / ".claude" / "scripts"))
from concurrent_read_probe import discover_ids, endpoint_paths  # noqa: E402


async def main() -> None:
    import httpx

    from app.common.auth import create_access_token
    from app.main import app

    ids = await discover_ids()
    targets = [e for e in endpoint_paths(ids) if any(s in e[0] for s in ONLY)]
    token = create_access_token(None, handle=VIEWER)
    transport = httpx.ASGITransport(app=app)
    print(f"ids {json.dumps(ids)} n={N} bursts={BURSTS} loadavg={Path('/proc/loadavg').read_text().strip()}")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://probe", timeout=600.0
    ) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        for label, path in targets:
            for _ in range(2):
                await client.get(path)  # warm: import + route + caches

            prof = cProfile.Profile()
            t0 = time.perf_counter()
            prof.enable()
            for _ in range(BURSTS):
                await asyncio.gather(*(client.get(path) for _ in range(N)))
            prof.disable()
            wall = (time.perf_counter() - t0) * 1000

            print(f"\n===== {label}: {BURSTS} bursts x {N} concurrent, wall {wall:.0f} ms =====")
            buf = io.StringIO()
            stats = pstats.Stats(prof, stream=buf).sort_stats("tottime")
            stats.print_stats(TOP)
            text = buf.getvalue()
            # Trim the per-line noise: keep file:line and the numbers.
            lines = [ln for ln in text.splitlines() if ln.strip()]
            print("\n".join(lines[: 12 + TOP]))
            print("--- cumulative ---")
            buf2 = io.StringIO()
            pstats.Stats(prof, stream=buf2).sort_stats("cumulative").print_stats(18)
            print("\n".join(ln for ln in buf2.getvalue().splitlines() if ln.strip())[:3000])


asyncio.run(main())
