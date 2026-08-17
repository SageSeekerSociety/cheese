"""Fire-and-forget background work that actually survives.

``asyncio`` keeps only a WEAK reference to a running task. A task nobody else
holds can therefore be garbage-collected **mid-await** — the footgun the stdlib
documents in ``asyncio.create_task``: *"Save a reference to the result of this
function, to avoid a task disappearing mid-execution."*

Most of this codebase already does that (``AgentWorkRunner`` keeps
``self._tasks``). Three call sites did not, and each of
them exists to TELL A ROOM SOMETHING — the accept merged, the push finished, the
box was rebuilt. A dropped task there is not a crash: it is a topic that never
hears, which is the exact failure mode the platform is worst at surfacing.

``spawn`` is the one-liner replacement, so the fourth site can't reintroduce it:

    from app.core.background import spawn

    spawn(post_with_retries(...), name="accept notice")

Never awaited, never cancelled here — the caller has already decided this work
outlives it. A crash inside is logged rather than swallowed, because a bare
``create_task`` whose exception nobody retrieves only surfaces (if ever) as an
"exception was never retrieved" warning at GC time.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger("cheesex.background")

# Strong references to everything in flight. Entries remove themselves on
# completion, so this is bounded by concurrency, not by history.
_INFLIGHT: set[asyncio.Task[Any]] = set()


def _finished(task: asyncio.Task[Any]) -> None:
    _INFLIGHT.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("background task %r failed", task.get_name(), exc_info=exc)


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> bool:
    """Run ``coro`` in the background, holding a strong reference until it ends.

    Returns False (having closed the coroutine) when there is no running loop —
    sync tests and scripts call into these paths, and "nothing to schedule onto"
    is a no-op there, not an error. Closing it matters: an un-awaited coroutine
    that is merely dropped emits a "never awaited" RuntimeWarning.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return False
    task = loop.create_task(coro, name=name)
    _INFLIGHT.add(task)
    task.add_done_callback(_finished)
    return True


def inflight_count() -> int:
    """How many spawned tasks are still running — for tests and diagnostics."""
    return len(_INFLIGHT)
