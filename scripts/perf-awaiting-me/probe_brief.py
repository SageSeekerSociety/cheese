"""Is the 30ms column projection's cost per *project* or per *row*?

``brief_for_projects`` is handed all 13 visible project ids and scans every
task under them (~1917 rows), while only ~66 ever reach the screen. Which of
those two numbers the cost tracks decides what a fix has to shrink:

  * per row    -> the projection itself is the problem; make it narrower, or
                  stop scanning tasks whose person could not be on them.
  * per project-> the IN-list / plan shape is the problem; batching or an
                  index-only path helps even at the same row count.

So: time the same call with the full id list, with a prefix, and with a single
id, and also count the rows the same predicate returns. Run from the repo root.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401
from app.domain.project.repositories import ProjectRepository  # noqa: E402
from app.domain.room_task.models import Task  # noqa: E402
from app.domain.room_task.repositories import TaskRepository  # noqa: E402

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
ROUNDS = int(os.environ.get("PERF_ROUNDS", "7"))


async def timed(fn, rounds: int) -> float:
    await fn()  # warm
    samples = []
    for _ in range(rounds):
        start = time.process_time()
        await fn()
        samples.append(time.process_time() - start)
    return statistics.median(samples) * 1000


async def main() -> None:
    engine = create_async_engine(DB_URL, poolclass=None)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        projects = await ProjectRepository(db).list_visible_to(
            handle=VIEWER, user_id=None
        )
        ids = [p.id for p in projects]
        total_all = await db.scalar(
            select(func.count()).select_from(Task).where(Task.project_id.in_(ids))
        )
        total_open = await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.project_id.in_(ids), Task.status == "open")
        )

    print(
        f"visible projects={len(ids)}  tasks under them={total_all}  open={total_open}"
    )
    print()
    print("brief_for_projects CPU (median ms):")
    for label, subset in (
        ("all 13 projects", ids),
        ("first 7", ids[:7]),
        ("first 1", ids[:1]),
    ):
        ms = await timed(lambda s=subset: TaskRepository_fetch(factory, s), ROUNDS)
        print(f"  {label:16s} {ms:7.2f}")

    print()
    print("same rows, but the projection only keeps room_id (no ORM entity):")
    for label, subset in (("all 13 projects", ids), ("first 1", ids[:1])):
        ms = await timed(lambda s=subset: bare_scan(factory, s), ROUNDS)
        print(f"  {label:16s} {ms:7.2f}")

    await engine.dispose()


async def TaskRepository_fetch(factory, ids) -> None:
    async with factory() as db:
        await TaskRepository(db).brief_for_projects(ids)


async def bare_scan(factory, ids) -> None:
    """The identical predicate, but one UUID column and no ORM row building."""
    async with factory() as db:
        await db.execute(select(Task.room_id).where(Task.project_id.in_(ids)))


if __name__ == "__main__":
    asyncio.run(main())
