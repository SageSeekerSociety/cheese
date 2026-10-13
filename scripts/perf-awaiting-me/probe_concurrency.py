"""The number the tracker actually states: 20 concurrent /awaiting-me requests.

Measures three things that the single-request harness cannot separate:

  * total wall for the batch — what a 20-way burst costs end to end;
  * total CPU for the batch — what the one event loop actually has to run,
    which is the part a second worker could not absorb;
  * per-request CPU under contention, to compare with the single-request run.

It runs one batch per iteration against the database it is pointed at; to see
how the cost moves with the data, resize the seed (`PERF_DATABASE_URL`) and run
it again.

Run from the repo root.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401
from app.api.routes.awaiting import waiting_items  # noqa: E402

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
N = int(os.environ.get("PERF_CONCURRENCY", "20"))
BATCHES = int(os.environ.get("PERF_BATCHES", "5"))


async def one(session_factory, chat) -> int:
    async with session_factory() as db:
        items = await waiting_items(db, chat, handle=VIEWER, user_id=None)
        rows = [item.as_dict() for item in items]
    return len(rows)


async def main() -> None:
    engine = create_async_engine(DB_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    from types import SimpleNamespace

    chat = SimpleNamespace(live=SimpleNamespace(active_turn_ids={}))

    await one(session_factory, chat)  # warm pool + statement cache

    print(f"concurrency={N}  batches={BATCHES}  (one batch = the 4.1s figure)")
    print()
    print("batch    wall ms    CPU ms   CPU/req ms   rows")
    walls: list[float] = []
    cpus: list[float] = []
    for i in range(BATCHES):
        cpu_start = time.process_time()
        wall_start = time.perf_counter()
        results = await asyncio.gather(*(one(session_factory, chat) for _ in range(N)))
        wall = (time.perf_counter() - wall_start) * 1000
        cpu = (time.process_time() - cpu_start) * 1000
        walls.append(wall)
        cpus.append(cpu)
        print(f"{i:5d} {wall:10.1f} {cpu:9.1f} {cpu / N:11.2f} {results[0]:6d}")

    wall_med = statistics.median(walls)
    cpu_med = statistics.median(cpus)
    print()
    print(f"median wall for {N} concurrent: {wall_med:.1f} ms")
    print(f"median CPU  for {N} concurrent: {cpu_med:.1f} ms")
    print(f"  per request: {cpu_med / N:.2f} ms CPU")
    print(f"  loop busy share of wall: {cpu_med / wall_med * 100:.1f}%")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
