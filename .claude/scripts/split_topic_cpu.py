"""Where does one request's event-loop CPU go, call site by call site?

``concurrent_read_probe.py`` says how many milliseconds each endpoint burns on
the loop under 20-concurrency; ``measure_awaiting_me.py`` splits one of those
endpoints (``/awaiting-me``) into 取数 / 逐条判定 / 组装 / 序列化. This does the
same for the two endpoints ``/topics/{id}`` and ``/awaiting-me``, but splits
*every await the route makes* rather than a hand-picked set of buckets — the
2026-10-15 report found the channel detail's remaining 1.3 s is spread over 37
SQL statements, so the question is which of the route's own calls they hang off.

It wraps, on the modules the route actually reaches them through:

  * every function ``get_topic`` calls once per request (``place_or_404``,
    ``last_activity_for_topics``, … ``rail_tasks``);
  * ``AsyncSession.execute`` / ``AsyncSession.scalars`` / ``AsyncConnection``,
    so the SQL count and its CPU are attributed next to the caller;
  * for ``/awaiting-me``: every fetch leaf ``waiting_items`` performs, plus the
    judgement helpers, plus the whole ``waiting_items`` remainder.

CPU is ``time.process_time``. One request in flight is the single-threaded
event loop's occupancy — exactly what serialises at 20 concurrent requests, and
a loaded host does not change it.

Two numbers per leaf, and they are different questions:

  * **self** — CPU while that leaf's own frame was on the stack, minus the CPU
    of the leaves it called (nesting is subtracted, so a parent is not charged
    its children);
  * **cum** — CPU for the whole await, children included.

Read ``cum`` when the question is "what does this call cost the loop", ``self``
when it is "what is this frame's own work".

Each target gets a fresh recorder (``make_split``), so a key both routes touch —
``sql.execute`` above all — reports each route's own cost, and no row in one
table is a leftover from the other.

Run from the repo root, with the perf database up and seeded::

    PERF_DB_URL=postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf \\
    PERF_REDIS_URL=redis://127.0.0.1:6379/0 \\
        uv run --project backend python .claude/scripts/split_topic_cpu.py

Environment: ``PERF_DB_URL``, ``PERF_REDIS_URL``, ``PERF_HANDLE`` (``perfviewer``),
``PERF_ROUNDS`` (7), ``PERF_WHICH`` (``topic`` | ``awaiting`` | ``both``).
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
sys.path.insert(0, str(REPO / ".claude" / "scripts"))

DB_URL = os.environ.get(
    "PERF_DB_URL", "postgresql+asyncpg://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)
VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
ROUNDS = int(os.environ.get("PERF_ROUNDS", "7"))
WHICH = os.environ.get("PERF_WHICH", "both")

os.environ.setdefault("DATABASE_URL", DB_URL)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET", "perf-probe-secret")
os.environ.setdefault(
    "REDIS_URL", os.environ.get("PERF_REDIS_URL", "redis://127.0.0.1:6379/0")
)


class Split:
    """Charge CPU to a stack of leaves; ``self`` = cum minus what it called.

    The wrapped functions nest (``place_or_404`` reaches repositories, which
    reach ``AsyncSession.execute``), so a flat accumulator would count the same
    millisecond at every level. Keeping a stack and subtracting children from
    their parent gives both readings from one pass.
    """

    def __init__(self) -> None:
        self.stack: list[tuple[str, float, float]] = []  # (key, started, child_cpu)
        self.cum: dict[str, list[float]] = {}  # per-key totals, one per round
        self.own: dict[str, list[float]] = {}
        self.calls: dict[str, list[int]] = {}
        self.round_cum: dict[str, float] = {}
        self.round_own: dict[str, float] = {}
        self.round_calls: dict[str, int] = {}
        self._saved: list[tuple[object, str, object]] = []

    def enter(self, key: str) -> None:
        self.stack.append((key, time.process_time(), 0.0))

    def leave(self) -> None:
        key, started, child = self.stack.pop()
        total = time.process_time() - started
        self.round_cum[key] = self.round_cum.get(key, 0.0) + total
        self.round_own[key] = self.round_own.get(key, 0.0) + total - child
        self.round_calls[key] = self.round_calls.get(key, 0) + 1
        if self.stack:
            parent_key, parent_started, parent_child = self.stack[-1]
            self.stack[-1] = (parent_key, parent_started, parent_child + total)

    def next_round(self) -> None:
        self.round_cum = {}
        self.round_own = {}
        self.round_calls = {}

    def close_round(self) -> None:
        """Fold this round's per-key totals into the per-round samples."""
        for key, value in self.round_cum.items():
            self.cum.setdefault(key, []).append(value)
        for key, value in self.round_own.items():
            self.own.setdefault(key, []).append(value)
        for key, value in self.round_calls.items():
            self.calls.setdefault(key, []).append(value)

    def wrap(self, key: str, dotted: str) -> None:
        holder, attr = resolve(dotted)
        original = getattr(holder, attr)
        recorder = self

        if asyncio.iscoroutinefunction(original):

            async def wrapper(*args, _o=original, _k=key, **kwargs):
                recorder.enter(_k)
                try:
                    return await _o(*args, **kwargs)
                finally:
                    recorder.leave()

        else:

            def wrapper(*args, _o=original, _k=key, **kwargs):
                recorder.enter(_k)
                try:
                    return _o(*args, **kwargs)
                finally:
                    recorder.leave()

        self._saved.append((holder, attr, original))
        setattr(holder, attr, wrapper)

    def undo(self) -> None:
        for holder, attr, original in self._saved:
            setattr(holder, attr, original)
        self._saved.clear()


def resolve(dotted: str):
    """``a.b.c`` → (module-or-class of ``a.b``, ``c``), importing as needed."""
    import importlib

    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        try:
            holder = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for name in parts[cut:-1]:
            holder = getattr(holder, name)
        return holder, parts[-1]
    raise ImportError(dotted)


#: Each entry is what ``get_topic`` itself awaits, in the order it awaits them.
#: Patched on ``app.api.routes.topics`` for the names that module imported by
#: value (``_actor_in_place``, ``moving_topics``, …), on the class otherwise.
TOPIC_LEAVES = [
    ("place_or_404", "app.api.routes.topics.TopicService.place_or_404"),
    ("last_activity", "app.api.routes.topics.TopicService.last_activity_for_topics"),
    ("relevance", "app.api.routes.topics.TopicService.relevance_for_topics"),
    ("live_cards", "app.api.routes.topics.topics_signals.live_cards"),
    ("managed_ids", "app.api.routes.topics.TopicMemberService.managed_topic_ids"),
    ("awaiting_answer", "app.api.routes.topics._awaiting_an_answer"),
    ("member_waits", "app.api.routes.topics.MemberWaits.for_rooms"),
    ("working_in_threads", "app.api.routes.topics.topics_signals.working_in_threads"),
    ("moving_topics", "app.api.routes.topics.moving_topics"),
    ("unseen", "app.api.routes.topics.TaskService.unseen_deliveries"),
    ("replied_to_me", "app.api.routes.topics.TopicService.replied_to_me"),
    ("rail_tasks", "app.api.routes.topics.rail_tasks"),
]

#: ``waiting_items``' fetch leaves, the same set ``measure_awaiting_me.py``
#: charges — named the way ``app.api.awaiting_deps`` reaches them.
AWAITING_LEAVES = [
    ("projects", "app.api.awaiting_deps.ProjectRepository.list_visible_to"),
    (
        "topics",
        "app.api.awaiting_deps.TopicRepository.visible_unarchived_ids_for_projects",
    ),
    ("tasks.brief", "app.api.awaiting_deps.TaskRepository.brief_for_projects"),
    ("cards.listed", "app.api.awaiting_deps.listed_cards"),
    ("threads", "app.api.awaiting_deps.threads_of_rooms"),
    ("blocks.questions", "app.api.awaiting_deps.BlockRepository.awaiting_answer_blocks"),
    ("alerts", "app.api.awaiting_deps.ProjectNotificationService.still_open"),
    ("tasks.rows", "app.api.awaiting_deps.TaskRepository.listed_by_ids"),
    ("topics.rows", "app.api.awaiting_deps.TopicRepository.listed_by_ids"),
    ("running", "app.api.awaiting_deps.running_tasks"),
    ("waiting_items", "app.api.awaiting_deps.waiting_items"),
]

#: The driver call every fetch leaf bottoms out in. Charged under whatever
#: route-level leaf is on the stack, so the SQL count lands next to its caller.
SQL_LEAVES = [
    ("sql.execute", "sqlalchemy.ext.asyncio.AsyncSession.execute"),
    ("sql.scalars", "sqlalchemy.ext.asyncio.AsyncSession.scalars"),
    ("sql.get", "sqlalchemy.ext.asyncio.AsyncSession.get"),
    ("sql.flush", "sqlalchemy.ext.asyncio.AsyncSession.flush"),
]


def make_split() -> Split:
    """A recorder with every leaf wrapped.

    One per target, not one per process: the two routes share leaves (both call
    ``session.execute``, ``session.scalars``), and a shared recorder would carry
    the first route's samples into the second route's table under the same key.
    """
    split = Split()
    for key, dotted in [*TOPIC_LEAVES, *AWAITING_LEAVES, *SQL_LEAVES]:
        try:
            split.wrap(key, dotted)
        except (ImportError, AttributeError) as exc:  # a name moved; say which
            print(f"!! could not wrap {key} ({dotted}): {exc}", file=sys.stderr)
    return split


async def main() -> None:
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 — register every table
    from app.common.auth import create_access_token
    from app.main import app

    ids = json.loads(
        (await _discover_ids())
    )
    token = create_access_token(None, handle=VIEWER)
    transport = httpx.ASGITransport(app=app)
    print(
        f"ids {json.dumps(ids)} rounds={ROUNDS} "
        f"loadavg={Path('/proc/loadavg').read_text().strip()}"
    )

    async with httpx.AsyncClient(
        transport=transport, base_url="http://probe", timeout=600.0
    ) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        targets = []
        if WHICH in ("topic", "both"):
            targets.append(("channel detail", f"/topics/{ids['room']}"))
        if WHICH in ("awaiting", "both"):
            targets.append(("awaiting-me", "/awaiting-me"))

        for label, path in targets:
            for _ in range(2):
                await client.get(path)  # warm: import + route + statement caches
            split = make_split()
            whole: list[float] = []
            for _ in range(ROUNDS):
                split.next_round()
                start = time.process_time()
                await client.get(path)
                whole.append((time.process_time() - start) * 1000)
                split.close_round()
            print(f"\n===== {label}: {ROUNDS} rounds, single request =====")
            print(f"  whole loop CPU per request: {statistics.median(whole):.2f} ms")
            rows = sorted(
                split.cum.items(),
                key=lambda kv: -statistics.median(kv[1]),
            )
            print(f"  {'leaf':24} {'cum ms':>8} {'self ms':>8} {'calls':>7}")
            for key, vals in rows:
                c = statistics.median(split.calls[key])
                o = statistics.median(split.own.get(key, [0.0]))
                print(
                    f"  {key:24} {statistics.median(vals) * 1000:8.3f} "
                    f"{o * 1000:8.3f} {c:7.0f}"
                )
            split.undo()
            del split


async def _discover_ids() -> str:
    from concurrent_read_probe import discover_ids

    return json.dumps(await discover_ids())


if __name__ == "__main__":
    asyncio.run(main())
