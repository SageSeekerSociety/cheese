"""Where does one ``/awaiting-me`` request spend its event-loop CPU?

Four buckets, matching the tracker's ask: 取数 / 逐条判定 / 组装 DTO / 序列化.
CPU is ``time.process_time`` — the process's own user+sys time, not wall clock,
so a loaded host does not change the answer. With one request in flight that is
the single-threaded event loop's occupancy, which is exactly what serialises at
20 concurrent requests.

Two passes:

  * an instrumented run: every data-fetch call site is wrapped and charged to
    取数; ``address`` and ``presentation.*`` to 逐条判定; the remainder of
    ``waiting_items`` to 组装 DTO;
  * a ``cProfile`` run: per-function exclusive time, so the biggest single
    contributors are visible even when they sit inside a bucket.

Run from the repo root::

    uv run --project backend python scripts/perf-awaiting-me/measure_awaiting_me.py
"""

from __future__ import annotations

import asyncio
import cProfile
import io
import json
import os
import pstats
import statistics
import sys
import time
from pathlib import Path

# Run from anywhere: ``app`` lives in ``backend/``.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
ROUNDS = int(os.environ.get("PERF_ROUNDS", "7"))
PROFILE_ROUNDS = int(os.environ.get("PERF_PROFILE_ROUNDS", "5"))

import app.models  # noqa: E402,F401 — register every table
from app.api.awaiting_deps import waiting_items  # noqa: E402

#: (bucket, dotted path) for every leaf fetch ``waiting_items`` performs. They
#: do not nest, so charging each one is exclusive. A path here is the name
#: ``waiting_items`` itself looks up, which is not always the one the leaf is
#: defined under: it lives in ``app.api.awaiting_deps`` and imports
#: ``threads_of_rooms`` and ``listed_cards`` by name, so
#: those two have to be patched on that module — patching the module they
#: come from charges nothing and leaves their cost in 组装 DTO. Repository
#: methods need no such care: the class object is the same one either way.
#: ``address`` is imported by name the same way, but it is a judgement call, so
#: it sits in ``JUDGEMENT_TARGETS`` below rather than here.
FETCH_LEAVES = [
    ("projects", "app.domain.project.repositories.ProjectRepository.list_visible_to"),
    (
        "topics",
        "app.domain.topic.repositories.TopicRepository.visible_unarchived_ids_for_projects",
    ),
    (
        "tasks.brief",
        "app.domain.room_task.repositories.TaskRepository.brief_for_projects",
    ),
    ("cards.listed", "app.api.awaiting_deps.listed_cards"),
    (
        "cards.reviewers",
        "app.domain.review.services.AcceptService.reviewer_handles_for_cards",
    ),
    ("threads", "app.api.awaiting_deps.threads_of_rooms"),
    (
        "blocks.questions",
        "app.domain.block.repositories.BlockRepository.awaiting_answer_blocks",
    ),
    (
        "alerts",
        "app.domain.notification.services.ProjectNotificationService.still_open",
    ),
    ("tasks.rows", "app.domain.room_task.repositories.TaskRepository.listed_by_ids"),
    ("teammate_runs", "app.api.awaiting_deps.teammate_runs"),
    ("topics.rows", "app.domain.topic.repositories.TopicRepository.listed_by_ids"),
    (
        "teammates.named",
        "app.domain.agent_instance.services.AgentInstanceService.for_task_by_id",
    ),
]


def resolve(dotted: str):
    """``"a.b.C.m"`` -> ``(the object holding ``m``, "m")``.

    The holder is a class for repository methods and a module for free
    functions. Import the longest prefix that is a module, then walk the rest.
    """
    import importlib

    parts = dotted.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            holder = importlib.import_module(".".join(parts[:split]))
        except ModuleNotFoundError:
            continue
        for name in parts[split:-1]:
            holder = getattr(holder, name)
        return holder, parts[-1]
    raise ImportError(dotted)


class Recorder:
    """CPU charged to each wrapped call.

    ``cpu`` / ``calls`` are the round being measured; ``total`` / ``calls_total``
    keep every round, so a leaf's share is the median of the same population as
    the phase line it belongs to.
    """

    def __init__(self) -> None:
        self.cpu: dict[str, float] = {}
        self.calls: dict[str, int] = {}
        self.total: dict[str, float] = {}
        self.calls_total: dict[str, int] = {}
        self._saved: list[tuple[object, str, object]] = []

    def _charge(self, key: str, elapsed: float) -> None:
        self.cpu[key] = self.cpu.get(key, 0.0) + elapsed
        self.calls[key] = self.calls.get(key, 0) + 1
        self.total[key] = self.total.get(key, 0.0) + elapsed
        self.calls_total[key] = self.calls_total.get(key, 0) + 1

    def next_round(self) -> None:
        self.cpu.clear()
        self.calls.clear()

    def wrap(self, key: str, dotted: str) -> None:
        holder, attr = resolve(dotted)
        original = getattr(holder, attr)
        recorder = self

        if asyncio.iscoroutinefunction(original):

            async def wrapper(*args, _o=original, _k=key, **kwargs):
                start = time.process_time()
                try:
                    return await _o(*args, **kwargs)
                finally:
                    recorder._charge(_k, time.process_time() - start)

        else:

            def wrapper(*args, _o=original, _k=key, **kwargs):
                start = time.process_time()
                try:
                    return _o(*args, **kwargs)
                finally:
                    recorder._charge(_k, time.process_time() - start)

        self._saved.append((holder, attr, original))
        setattr(holder, attr, wrapper)

    def undo(self) -> None:
        for holder, attr, original in self._saved:
            setattr(holder, attr, original)
        self._saved.clear()


#: 逐条判定 reads these; patched on the module *objects* ``waiting_items``
#: actually reaches them through (it imports ``address`` by name, so patching
#: ``addressing.address`` would miss).
JUDGEMENT_TARGETS = [
    ("address", "app.api.awaiting_deps.address"),
    ("task_presentation", "app.domain.room_task.presentation.task_presentation"),
    ("room_presentation", "app.domain.room_task.presentation.room_presentation"),
    ("facts_for_task", "app.domain.room_task.presentation.facts_for_task"),
    ("facts_for_room", "app.domain.room_task.presentation.facts_for_room"),
]


async def one_request(session_factory) -> tuple[list, list[dict]]:
    async with session_factory() as db:
        items = await waiting_items(db, handle=VIEWER, user_id=None)
        rows = [item.as_dict() for item in items]
    return items, rows


def _serial_cpu(items: list) -> float:
    """CPU to turn ``items`` into the response body: as_dict + JSON encode."""
    start = time.process_time()
    rows = [item.as_dict() for item in items]
    json.dumps(
        {"code": 200, "message": "ok", "data": {"data": rows, "total": len(rows)}}
    ).encode()
    return time.process_time() - start


async def main() -> None:
    # The production pool shape (QueuePool) — not NullPool. A fresh connection
    # per request would charge connect+auth to every leaf and swamp the answer.
    engine = create_async_engine(DB_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Warm up: import caches, statement compilation, page cache.
    for _ in range(2):
        await one_request(session_factory)

    # —— pass 1: per-phase CPU ———————————————————————————————————————
    recorder = Recorder()
    for key, dotted in FETCH_LEAVES:
        recorder.wrap(key, dotted)
    jrec = Recorder()
    for key, dotted in JUDGEMENT_TARGETS:
        jrec.wrap(key, dotted)

    per_round: list[dict[str, float]] = []
    serial_rounds: list[float] = []
    wall_rounds: list[float] = []
    fetch_rounds: list[dict[str, float]] = []
    judge_rounds: list[dict[str, float]] = []
    items_shown = 0
    response_bytes = 0
    for _ in range(ROUNDS):
        recorder.next_round()
        jrec.next_round()
        wall_start = time.perf_counter()
        start = time.process_time()
        # What the request's own dependency does before ``waiting_items``: a
        # connection from the pool plus session setup. Charged separately so it
        # does not land inside 组装 DTO.
        async with session_factory() as db:
            session_cpu = time.process_time() - start
            body_start = time.process_time()
            items = await waiting_items(db, handle=VIEWER, user_id=None)
            # The route's last step, measured where the route does it.
            rows = [item.as_dict() for item in items]
            body_cpu = time.process_time() - body_start
        whole = time.process_time() - start
        wall_rounds.append(time.perf_counter() - wall_start)
        body = json.dumps(
            {"code": 200, "message": "ok", "data": {"data": rows, "total": len(rows)}}
        ).encode()
        items_shown = len(rows)
        response_bytes = len(body)
        fetch = sum(recorder.cpu.values())
        judge = sum(jrec.cpu.values())
        per_round.append(
            {
                "fetch": fetch,
                "judge": judge,
                # Everything left in ``waiting_items``: the candidate filtering,
                # the per-item loops, and building the WaitingItem rows.
                "assemble": body_cpu - fetch - judge,
                # The connection + session the dependency hands the route.
                "overhead": session_cpu,
                "whole": whole,
            }
        )
        serial_rounds.append(_serial_cpu(items))
        fetch_rounds.append(dict(recorder.cpu))
        judge_rounds.append(dict(jrec.cpu))

    # Per-leaf median across rounds — same population as the phase medians, so a
    # leaf's number and the phase it belongs to are directly comparable. Each
    # key is charged by exactly one recorder; reading both and defaulting the
    # missing side to zero would halve every median.
    def leaf_median(key: str, rounds: list[dict[str, float]]) -> float:
        return statistics.median(r.get(key, 0.0) for r in rounds) * 1000  # ms

    fetch_leaves = {(k, False) for k in recorder.calls}
    judge_leaves = {(k, True) for k in jrec.calls}
    fetch_calls = dict(recorder.calls_total)
    judge_calls = dict(jrec.calls_total)

    # —— pass 2: cProfile ————————————————————————————————————————————
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(PROFILE_ROUNDS):
        await one_request(session_factory)
    profiler.disable()
    stream = io.StringIO()
    profiler.dump_stats("/tmp/awaiting-me.prof")
    stats = pstats.Stats(profiler, stream=stream).sort_stats("tottime")
    stats.print_stats(35)
    profile_text = stream.getvalue()

    def med(key: str) -> float:
        return statistics.median(r[key] for r in per_round) * 1000

    print("=" * 78)
    print(f"db={DB_URL}")
    print(f"items on screen={items_shown}  response bytes={response_bytes}")
    print(f"median of {ROUNDS} rounds; CPU = time.process_time (loop occupancy)")
    wall_med = statistics.median(wall_rounds) * 1000
    print(
        f"wall per request: {wall_med:.2f} ms  "
        f"(CPU/wall = {med('whole') / wall_med:.2f})"
    )
    print()
    print("per-phase CPU per request (ms)          median    share")
    whole = med("whole")
    for label, key in (
        ("取数 fetch", "fetch"),
        ("组装 DTO assemble", "assemble"),
        ("逐条判定 judgement", "judge"),
        ("会话/连接 session", "overhead"),
        ("序列化 serialize", "serial"),
    ):
        value = statistics.median(serial_rounds) * 1000 if key == "serial" else med(key)
        print(f"  {label:22s} {value:9.2f}  {value / whole * 100:6.1f}%")
    print(f"  {'合计':22s} {whole:9.2f}  100.0%")
    print(
        f"  min/max whole CPU across rounds: "
        f"{min(r['whole'] for r in per_round) * 1000:.1f} / "
        f"{max(r['whole'] for r in per_round) * 1000:.1f} ms"
    )
    print()
    print("取数 / 逐条判定 leaves (median CPU ms per request, calls per request):")
    rows_out = [
        (leaf_median(k, judge_rounds if is_judge else fetch_rounds), k, is_judge)
        for k, is_judge in fetch_leaves | judge_leaves
    ]
    for value, key, is_judge in sorted(rows_out, reverse=True):
        calls = (judge_calls if is_judge else fetch_calls).get(key, 0) / ROUNDS
        where = "逐条判定" if is_judge else "取数"
        print(f"  {where}  {key:18s} {value:8.3f}  {calls:5.1f}")
    print("=" * 78)
    print(profile_text)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
