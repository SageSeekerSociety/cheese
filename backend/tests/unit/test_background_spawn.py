"""Fire-and-forget tasks must not be collectable mid-flight.

`asyncio` keeps only a WEAK reference to a running task, so one nobody else
holds can be garbage-collected part-way through — the stdlib documents this
under `asyncio.create_task` ("Save a reference to the result of this function,
to avoid a task disappearing mid-execution").

Three call sites held no reference, and all three exist to TELL A ROOM
SOMETHING: the accept merged, the push finished, the box was rebuilt. Dropping
one is not a crash — it is a topic that never hears, which is the failure the
platform is worst at surfacing (see the deaf-sandbox hunt, #316).
"""

import asyncio
import gc

import pytest

from app.core.background import inflight_count, spawn


@pytest.mark.anyio
async def test_a_spawned_task_survives_a_collection_mid_await():
    """The actual defect. Nothing local holds this task, and a full GC pass runs
    while it is parked on an await — exactly the window `post_with_retries`
    spends sleeping between its retries."""
    finished = asyncio.Event()

    async def slow() -> None:
        await asyncio.sleep(0.05)
        finished.set()

    spawn(slow(), name="survivor")
    gc.collect()
    gc.collect()

    await asyncio.wait_for(finished.wait(), timeout=2)


@pytest.mark.anyio
async def test_the_reference_is_released_when_the_task_ends():
    """A strong reference held forever is a leak. It must be held until the task
    ends, and not one moment longer."""
    before = inflight_count()
    done = asyncio.Event()

    async def quick() -> None:
        done.set()

    spawn(quick(), name="transient")
    assert inflight_count() == before + 1

    await done.wait()
    for _ in range(50):
        if inflight_count() == before:
            break
        await asyncio.sleep(0.01)
    assert inflight_count() == before


@pytest.mark.anyio
async def test_a_crash_inside_is_logged_not_swallowed(caplog):
    """A bare `create_task` whose exception nobody retrieves surfaces (if ever)
    as an "exception was never retrieved" warning at GC time — i.e. detached
    from the thing that caused it."""
    boom = asyncio.Event()

    async def crash() -> None:
        boom.set()
        raise RuntimeError("the room was never told")

    with caplog.at_level("WARNING", logger="cheesex.background"):
        spawn(crash(), name="crasher")
        await boom.wait()
        for _ in range(50):
            if any("background task" in r.message for r in caplog.records):
                break
            await asyncio.sleep(0.01)

    assert [r for r in caplog.records if "background task" in r.message]


def test_no_running_loop_is_a_no_op_not_an_error(recwarn):
    """Sync tests and scripts reach these paths. "Nothing to schedule onto" is a
    no-op there — and the coroutine must be CLOSED, or merely dropping it emits
    a "coroutine was never awaited" RuntimeWarning."""

    async def never_runs() -> None:  # pragma: no cover - by design
        pass

    assert spawn(never_runs(), name="nowhere") is False

    gc.collect()
    assert not [w for w in recwarn if "never awaited" in str(w.message)], (
        "the un-scheduled coroutine must be closed, not leaked"
    )


def test_nobody_reintroduces_a_referenceless_create_task():
    """The guard, so this is a rule the repo enforces rather than one three
    reviewers have to remember.

    The catchable shape is a `create_task(...)` whose result is DISCARDED — the
    statement is the whole expression. Assigning it (`task = create_task(...)`)
    is not flagged: every such site here goes on to file the task somewhere
    (`_INFLIGHT[card_id] = task`, `self._tasks.add(task)`), which is the same
    fix by hand. `spawn` is the one-liner that removes the choice.
    """
    import ast
    from pathlib import Path

    app = Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []
    for path in app.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == "create_task":
                offenders.append(f"{path.relative_to(app.parent)}:{node.lineno}")

    assert not offenders, (
        "asyncio only weakly references a running task, so these can be "
        "collected mid-await and the work silently never finishes. Use "
        "app.core.background.spawn (or keep the task in a set yourself):\n  "
        + "\n  ".join(offenders)
    )
