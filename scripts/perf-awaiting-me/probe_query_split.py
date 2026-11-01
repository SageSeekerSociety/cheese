"""Per query: how much of its cost is Postgres answering, how much is us.

``measure_awaiting_me.py`` says a leaf costs N ms of event-loop CPU. That N has
two halves with opposite fixes:

  * Postgres answering — shrink the plan (index, predicate, fewer places asked).
  * us turning the rows into Python — shrink the *result* (fewer rows, fewer
    columns), because no index helps when the cost is per decoded row.

A SQLAlchemy ``after_cursor_execute`` listener gives the driver round trip
(wait + server), and the difference from the leaf's ``process_time`` is what we
spend decoding. Run from the repo root.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401
from app.api.awaiting_deps import waiting_items  # noqa: E402

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
ROUNDS = int(os.environ.get("PERF_ROUNDS", "7"))

#: Statements are grouped by the table they read first — enough to name the
#: query without pinning the exact SQL text, which changes with parameters.
GROUPERS = ("blocks", "tasks", "topics", "accept_cards", "projects", "notification")


def group_of(statement: str) -> str:
    lower = statement.lower()
    for name in GROUPERS:
        if f"from {name}" in lower or f"join {name}" in lower:
            return name
    return "other"


async def main() -> None:
    engine = create_async_engine(DB_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    db_wall: dict[str, float] = defaultdict(float)
    db_calls: dict[str, int] = defaultdict(int)
    db_rows: dict[str, int] = defaultdict(int)
    # Per distinct statement, so the slow one can be named rather than guessed.
    stmt_wall: dict[str, float] = defaultdict(float)
    stmt_calls: dict[str, int] = defaultdict(int)

    def label(statement: str) -> str:
        flat = " ".join(statement.split())
        return flat[:110]

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        group = group_of(statement)
        db_calls[group] += 1
        try:
            db_rows[group] += cursor.rowcount
        except Exception:  # pragma: no cover - rowcount is best effort
            pass

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        context._perf_t0 = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _elapsed(conn, cursor, statement, parameters, context, executemany):
        elapsed = time.perf_counter() - context._perf_t0
        db_wall[group_of(statement)] += elapsed
        stmt_wall[label(statement)] += elapsed
        stmt_calls[label(statement)] += 1

    async def once() -> float:
        start = time.process_time()
        async with factory() as db:
            await waiting_items(db, handle=VIEWER, user_id=None)
        return time.process_time() - start

    await once()  # warm

    # Accumulate across every round so the slow statement is not a one-round
    # fluke, then report per request.
    totals_wall: dict[str, float] = defaultdict(float)
    totals_calls: dict[str, int] = defaultdict(int)
    cpu: list[float] = []
    for _ in range(ROUNDS):
        db_wall.clear()
        db_calls.clear()
        db_rows.clear()
        stmt_wall.clear()
        stmt_calls.clear()
        cpu.append(await once())
        for key, value in stmt_wall.items():
            totals_wall[key] += value
            totals_calls[key] += stmt_calls[key]
        snapshot = (dict(db_wall), dict(db_calls), dict(db_rows))
        print(
            f"round: leaf CPU {cpu[-1] * 1000:7.2f} ms | "
            + "  ".join(
                f"{g}: {snapshot[0].get(g, 0) * 1000:.1f}ms/"
                f"{snapshot[2].get(g, 0)}r"
                for g in GROUPERS
                if snapshot[1].get(g)
            )
        )

    print()
    print(f"median leaf CPU: {statistics.median(cpu) * 1000:.2f} ms")
    print()
    print("slowest statements, wall ms per request (wait + server):")
    for key in sorted(totals_wall, key=lambda k: -totals_wall[k])[:8]:
        print(
            f"  {totals_wall[key] / ROUNDS * 1000:8.2f} ms "
            f"x{totals_calls[key] / ROUNDS:.0f}  {key}"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
