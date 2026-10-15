#!/usr/bin/env python3
"""How many SQL round-trips does one request make, and which statements own it?

``concurrent_read_probe.py`` says what each channel-page endpoint costs under
20-concurrency. This says what a SINGLE request spends it on, statement by
statement: a counter and a per-statement wall clock, hung off SQLAlchemy's
``after_cursor_execute``.

Sequential (concurrency 1) on purpose: with one request in flight the driver's
own timer around the cursor is a truthful reading of that statement's wait, and
the statement order is the request's own order. Under 20-way interleaving the
same timer mixes requests together and is not readable that way -- that is what
``pg_stat_statements`` is for, and the probe already uses it.

  uv run --project backend python .claude/scripts/sql_census.py

Environment is the same as ``concurrent_read_probe.py``; ``PERF_ONLY`` filters
the endpoint labels.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5533/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
ROUNDS = int(os.environ.get("PERF_ROUNDS", "5"))
ONLY = [s.strip() for s in os.environ.get("PERF_ONLY", "").split(",") if s.strip()]

os.environ.setdefault("DATABASE_URL", DB_URL)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET", "perf-probe-secret")
os.environ.setdefault(
    "REDIS_URL", os.environ.get("PERF_REDIS_URL", "redis://127.0.0.1:6399/0")
)

sys.path.insert(0, str(REPO / ".claude" / "scripts"))
from concurrent_read_probe import discover_ids, endpoint_paths  # noqa: E402

TALLY: list = []


def install() -> None:
    from sqlalchemy import event

    from app.core.db import engine

    def before(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("_t0", []).append(time.perf_counter())

    def after(conn, cursor, statement, parameters, context, executemany):
        t0 = conn.info["_t0"].pop()
        TALLY.append((statement, (time.perf_counter() - t0) * 1000))

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    event.listen(engine.sync_engine, "after_cursor_execute", after)


def shape(statement: str) -> str:
    """A readable label for one statement: its verb and what it touches."""
    s = re.sub(r"\s+", " ", statement).strip()
    m = re.match(r"(SELECT|INSERT|UPDATE|DELETE)\b", s, re.I)
    verb = (m.group(1).upper() if m else "OTHER")
    tables = re.findall(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+\"?([a-z_][a-z0-9_]*)\"?", s, re.I)
    seen: list = []
    for t in tables:
        if t not in seen:
            seen.append(t)
    return f"{verb} {','.join(seen[:4])}"[:70]


async def main() -> None:
    import httpx

    from app.common.auth import create_access_token
    from app.main import app

    install()
    ids = await discover_ids()
    targets = [e for e in endpoint_paths(ids) if not ONLY or any(s in e[0] for s in ONLY)]
    token = create_access_token(None, handle=VIEWER)
    transport = httpx.ASGITransport(app=app)
    load = Path("/proc/loadavg").read_text().strip()
    print(f"ids {json.dumps(ids)} rounds={ROUNDS} loadavg={load}")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://probe", timeout=600.0
    ) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        for label, path in targets:
            await client.get(path)  # warm
            TALLY.clear()
            walls = []
            for _ in range(ROUNDS):
                t = time.perf_counter()
                r = await client.get(path)
                walls.append((time.perf_counter() - t) * 1000)
            if r.status_code != 200:
                print(f"\n{label}: HTTP {r.status_code} {r.text[:120]}")
                continue

            per_shape = defaultdict(list)
            for stmt, ms in TALLY:
                per_shape[shape(stmt)].append(ms)
            total = sum(ms for _, ms in TALLY)
            n_req = ROUNDS
            print(
                f"\n===== {label}  ({n_req} req, wall {sorted(walls)[n_req // 2]:.0f} ms)"
                f"  statements/req {len(TALLY) / n_req:.1f}  db/req {total / n_req:.1f} ms ====="
            )
            for sh, mss in sorted(per_shape.items(), key=lambda kv: -sum(kv[1]))[:8]:
                print(
                    f"  {sum(mss) / n_req:8.1f} ms/req  {len(mss) / n_req:5.1f} calls/req  {sh}"
                )


asyncio.run(main())
