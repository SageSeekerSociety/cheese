"""The number the tracker actually states: 20 concurrent /awaiting-me requests.

Measures three things that the single-request harness cannot separate:

  * wall for each burst — what a 20-way burst costs end to end, and the tail of
    it, which is the figure the bar is about (worst burst, not the average);
  * total CPU for the batch — what the one event loop actually has to run,
    which is the part a second worker could not absorb;
  * per-request CPU under contention, to compare with the single-request run.

It also reports how much of the burst was the cyclic collector. Generation-2
passes cost 150-420 ms each on this seed and land *inside* a burst, which is
what puts the worst burst an extra half-second above the median — so a run
without that column reads as pure noise.

The pool is built the way the app builds it (`core/db.py`): a burst of 20 that
could only check out 15 connections would measure the queue for a connection,
not the read path. Pass `PERF_POOL=default` to get the old shape back for a
before/after comparison.

The collector is configured the way the app configures it too: `app.main`'s
lifespan hook freezes the startup heap, and this probes the endpoint directly so
that hook never runs — without repeating the call here the probe would report a
shape the server no longer has. `PERF_FREEZE=0` turns it off for a before/after.

It runs batches against the database it is pointed at; to see how the cost
moves with the data, resize the seed (`PERF_DATABASE_URL`) and run it again.

Run from the repo root.
"""

from __future__ import annotations

import asyncio
import gc
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401
from app.api.awaiting_deps import waiting_items  # noqa: E402

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
N = int(os.environ.get("PERF_CONCURRENCY", "20"))
BATCHES = int(os.environ.get("PERF_BATCHES", "5"))


def engine_kwargs() -> dict:
    """The pool the app runs with, unless asked for the SQLAlchemy default."""
    if os.environ.get("PERF_POOL") == "default":
        return {}
    return {
        "pool_size": 20,
        "max_overflow": 15,
        "pool_timeout": 30.0,
        "pool_pre_ping": True,
    }


def freeze_startup_heap() -> bool:
    """What `app.main.lifespan` does at boot — see the module docstring."""
    if os.environ.get("PERF_FREEZE") == "0":
        return False
    gc.freeze()
    return True


class Collector:
    """What the cyclic collector charged to each burst."""

    def __init__(self) -> None:
        self.gen2 = 0
        self.pause_ms = 0.0
        self._started = 0.0

    def __enter__(self) -> "Collector":
        gc.collect()
        gc.callbacks.append(self._callback)
        return self

    def _callback(self, phase: str, info: dict) -> None:
        if phase == "start":
            self._started = time.perf_counter()
            return
        self.pause_ms += (time.perf_counter() - self._started) * 1000
        if info.get("generation") == 2:
            self.gen2 += 1

    def __exit__(self, *exc: object) -> None:
        gc.callbacks.remove(self._callback)


async def one(session_factory) -> int:
    async with session_factory() as db:
        items = await waiting_items(db, handle=VIEWER, user_id=None)
        rows = [item.as_dict() for item in items]
    return len(rows)


async def main() -> None:
    engine = create_async_engine(DB_URL, **engine_kwargs())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    frozen = freeze_startup_heap()
    await one(session_factory)  # warm pool + statement cache

    shape = "SQLAlchemy default" if not engine_kwargs() else "production 20+15"
    print(f"concurrency={N}  batches={BATCHES}  pool={shape}"
          f"  startup heap {'frozen' if frozen else 'unfrozen'}")
    print()
    print("batch    wall ms    CPU ms   CPU/req ms   rows    gc ms  gen2")
    walls: list[float] = []
    cpus: list[float] = []
    for i in range(BATCHES):
        with Collector() as gcstats:
            cpu_start = time.process_time()
            wall_start = time.perf_counter()
            results = await asyncio.gather(
                *(one(session_factory) for _ in range(N))
            )
            wall = (time.perf_counter() - wall_start) * 1000
            cpu = (time.process_time() - cpu_start) * 1000
        walls.append(wall)
        cpus.append(cpu)
        print(
            f"{i:5d} {wall:10.1f} {cpu:9.1f} {cpu / N:11.2f} {results[0]:6d}"
            f" {gcstats.pause_ms:8.1f} {gcstats.gen2:5d}"
        )

    wall_sorted = sorted(walls)
    cpu_med = statistics.median(cpus)
    print()
    print(f"burst wall for {N} concurrent: median {statistics.median(walls):.1f} ms"
          f"  p90 {wall_sorted[int(0.9 * len(wall_sorted))]:.1f} ms"
          f"  worst {wall_sorted[-1]:.1f} ms")
    print(f"median CPU  for {N} concurrent: {cpu_med:.1f} ms")
    print(f"  per request: {cpu_med / N:.2f} ms CPU")
    print(f"  loop busy share of median wall: {cpu_med / statistics.median(walls) * 100:.1f}%")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
