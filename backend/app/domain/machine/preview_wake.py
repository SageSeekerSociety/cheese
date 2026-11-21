"""Browse-activity keepalive and direct wake for a previewed app.

Two pieces that keep a ``cheese serve`` preview responsive without spending
model turns:

1. ``touch_preview_activity`` moves the serving session's ``SandboxHome.active_at``
   forward when a reader's browser loads or interacts with the preview, at most
   once per ``ACTIVE_STEP`` (1 minute). While someone is actively browsing the
   preview, the idle sweeper does not reclaim the sandbox under them; once they
   leave, the idle clock runs out as usual. A sandbox found frozen is thawed on
   the spot: its processes were stopped, its home was not taken apart, so the
   app somebody is opening comes back as it was.

2. ``wake_preview_on_machine`` brings a sleeping app's sandbox back: a frozen
   sandbox is thawed in place, and one that is still running is left as it is,
   so the app it was running keeps running. Nothing is restarted here and no
   launch command is re-run. Only when the sandbox has already been destroyed
   does the caller (``topics_preview_app``) wake the agent for a turn: starting
   the app again is the agent's job.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis_client
from app.domain.agent.device_hub import device_hub
from app.domain.environment import services as environment_rows
from app.domain.machine import lifecycle, occupants
from app.domain.machine.models import SandboxHome
from app.domain.machine.services import ACTIVE_STEP, HostPool
from app.domain.room_task.place import Place

logger = logging.getLogger("cheese.machine.preview_wake")

#: Minimum interval between DB updates of ``SandboxHome.active_at`` per
#: (conversation, seat) when a reader browses a preview.
PREVIEW_TOUCH_STEP_S = ACTIVE_STEP.total_seconds()

_preview_touched: dict[tuple[uuid.UUID, str], float] = {}
_waking_conversations: set[uuid.UUID] = set()
_WAKE_TASKS: set[asyncio.Task[None]] = set()
_THAW_TASKS: set[asyncio.Task[bool]] = set()


def clear_preview_touch_cache() -> None:
    """Clear the in-memory throttle table (used by tests)."""
    _preview_touched.clear()


def is_preview_waking(conversation_id: uuid.UUID) -> bool:
    """Whether a direct machine wake is currently running for ``conversation_id``."""
    return conversation_id in _waking_conversations


async def _never_gone() -> bool:
    return False


async def sandbox_to_wake(
    db: AsyncSession, conversation_id: uuid.UUID
) -> uuid.UUID | None:
    """The environment the conversation points to, when its sandbox still
    exists, running or frozen; None otherwise.

    A home that is on its way out (``stopped_at`` set) counts as gone: what it
    held is already being taken apart, so waking nothing would help."""
    environment = await environment_rows.environment_of_conversation(
        db, conversation_id
    )
    if environment is None:
        return None
    home = await occupants.home_of_environment(db, environment.id)
    # A parked one runs nothing to wake: its session starts it again.
    if home is None or home.stopped_at is not None or home.parked_at is not None:
        return None
    return environment.id


async def touch_preview_activity(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    seat: str,
) -> list[asyncio.Task[bool]]:
    """Refresh ``SandboxHome.active_at`` when a reader browses an app preview:
    the sandbox of the environment the conversation points to, where the
    ``seat``'s app is served from.

    Throttled in-memory and in SQL to ``ACTIVE_STEP`` (1 minute) so bursts of
    asset requests from a dev server cost at most one row update per minute.
    A sandbox found frozen is also thawed, in the background and with no model
    turn: opening its preview is using it, and what it was running is still
    there to resume. Returns the thaw tasks it started, for tests.

    Best-effort: a transient database failure is logged and never interrupts
    preview serving.
    """
    key = (conversation_id, seat)
    now_mono = time.monotonic()
    if now_mono - _preview_touched.get(key, -math.inf) < PREVIEW_TOUCH_STEP_S:
        return []
    if len(_preview_touched) >= 256:
        stale_keys = [
            k
            for k, ts in _preview_touched.items()
            if now_mono - ts >= PREVIEW_TOUCH_STEP_S
        ]
        for k in stale_keys:
            _preview_touched.pop(k, None)
    _preview_touched[key] = now_mono
    try:
        environment = await environment_rows.environment_of_conversation(
            db, conversation_id
        )
        if environment is None or environment.sandbox_home_id is None:
            return []
        now = datetime.now(UTC)
        cutoff = now - ACTIVE_STEP
        updated = list(
            await db.execute(
                update(SandboxHome)
                .where(
                    SandboxHome.id == environment.sandbox_home_id,
                    SandboxHome.stopped_at.is_(None),
                    or_(
                        SandboxHome.active_at.is_(None),
                        SandboxHome.active_at <= cutoff,
                    ),
                )
                .values(active_at=now)
                .returning(SandboxHome.id, SandboxHome.frozen_at)
            )
        )
        for home_id, _frozen_at in updated:
            lifecycle._asked.pop(home_id, None)
        if not updated:
            return []
        await db.commit()
        if any(frozen_at is not None for _home_id, frozen_at in updated):
            return [schedule_preview_thaw(db.bind, environment_id=environment.id)]
        return []
    except Exception:
        logger.warning(
            "touching preview activity for conversation %s failed",
            conversation_id,
            exc_info=True,
        )
        with contextlib.suppress(Exception):
            await db.rollback()
        return []


async def _mark_awake(db: AsyncSession, hub, environment_id: uuid.UUID):
    """`HostPool.mark_awake` for the environment."""
    return await HostPool(db, hub=hub).mark_awake(environment_id)


def schedule_preview_thaw(
    bind, *, environment_id: uuid.UUID, hub=None
) -> asyncio.Task[bool]:
    """Thaw a frozen sandbox a preview is being read from, in the background.

    A reader opening the preview should not wait a host round trip for it, and
    a host that cannot thaw it must not break the preview request: the mark is
    left for the next tool call there to thaw again."""
    task = asyncio.create_task(
        thaw_preview_on_machine(bind, environment_id=environment_id, hub=hub)
    )
    _THAW_TASKS.add(task)
    task.add_done_callback(_THAW_TASKS.discard)
    return task


async def thaw_preview_on_machine(bind, *, environment_id: uuid.UUID, hub=None) -> bool:
    """Wake a frozen sandbox somebody is browsing, without a model turn.

    Its processes are resumed the way a tool call's wake does it: the home is
    marked under the pool's lock and the host call runs with no database lock
    held (`lifecycle.thaw_if_asked`). Returns whether there was a frozen home
    to wake; a failed thaw is logged and leaves the thaw asked for, which the
    session's next tool call retries."""
    hub = hub or device_hub
    try:
        async with AsyncSession(bind, expire_on_commit=False) as db:
            marked = await _mark_awake(db, hub, environment_id)
            await db.commit()
            if marked is None:
                return False
            device_id, work_resource = marked
            if device_id is None:
                return False
            await lifecycle.thaw_if_asked(
                db,
                hub,
                environment_id=environment_id,
                device_id=device_id,
                work_resource=work_resource,
            )
            return True
    except Exception:
        logger.warning("thawing the sandbox a preview belongs to failed", exc_info=True)
        return False


async def wake_for_files(bind, conversation_id: uuid.UUID, *, hub=None) -> None:
    """Thaw the frozen sandbox that holds a conversation's working copy before
    a person's read or save reaches it (`repository.ports.WakeWorkingCopy`).

    A frozen sandbox's executor is stopped with the rest of it, so the file
    request would wait out its deadline and fail; opening or saving a task's
    files is using the sandbox, as opening its preview is. Waited for, since
    the request needs it awake; one that is not frozen costs one read. A
    thaw that fails is logged (`thaw_preview_on_machine`) and the request
    goes on to say the machine did not answer.

    A person reading or saving the files is using the sandbox as its teammate
    does: its idle clock moves to now (at most once per ``ACTIVE_STEP``), so
    the idle sweep does not take it away under somebody editing in it."""
    async with AsyncSession(bind, expire_on_commit=False) as db:
        environment = await environment_rows.environment_of_conversation(
            db, conversation_id
        )
        if environment is None or environment.sandbox_home_id is None:
            return
        now = datetime.now(UTC)
        await db.execute(
            update(SandboxHome)
            .where(
                SandboxHome.id == environment.sandbox_home_id,
                SandboxHome.stopped_at.is_(None),
                SandboxHome.frozen_at.is_(None),
                SandboxHome.active_at <= now - ACTIVE_STEP,
            )
            .values(active_at=now)
        )
        await db.commit()
        if not await _frozen(db, environment.sandbox_home_id):
            return
    await thaw_preview_on_machine(bind, environment_id=environment.id, hub=hub)


async def _frozen(db: AsyncSession, home_id: uuid.UUID) -> bool:
    return (
        await db.scalar(
            select(SandboxHome.id).where(
                SandboxHome.id == home_id,
                SandboxHome.frozen_at.is_not(None),
                SandboxHome.stopped_at.is_(None),
            )
        )
        is not None
    )


async def is_frozen(db: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """Whether the sandbox holding a conversation's working copy — that of
    the environment it points to — is frozen: a request sent to it would wait
    out its deadline, and asking costs no thaw."""
    environment = await environment_rows.environment_of_conversation(
        db, conversation_id
    )
    if environment is None or environment.sandbox_home_id is None:
        return False
    return await _frozen(db, environment.sandbox_home_id)


async def wake_preview_on_machine(
    bind, *, place: Place, environment_id: uuid.UUID, hub=None
) -> bool:
    """Bring the environment's sandbox back, thawing it if it was frozen.

    No launch command runs and no model turn is spent: a frozen sandbox's
    processes resume in place, so an app it was running goes on running.
    Returns False when that cannot bring the app back — no sandbox (destroyed
    or parked), or one already running whose app exited — so the caller wakes
    the agent for a turn instead."""
    hub = hub or device_hub
    try:
        async with AsyncSession(bind, expire_on_commit=False) as db:
            marked = await _mark_awake(db, hub, environment_id)
            await db.commit()
            if marked is None:
                return False
            device_id, work_resource = marked
            if device_id is None:
                # Running, and its app still does not answer (the route asks
                # the app before it wakes anything): it exited, and nothing on
                # the machine starts it again but the agent.
                return False
            await lifecycle.thaw_if_asked(
                db,
                hub,
                environment_id=environment_id,
                device_id=device_id,
                work_resource=work_resource,
            )
            return True
    except Exception:
        logger.warning(
            "direct preview wake for conversation %s failed; falling back to agent turn",
            place.conversation_id,
            exc_info=True,
        )
        return False


def schedule_preview_wake(
    bind,
    *,
    place: Place,
    environment_id: uuid.UUID,
    fallback: Callable[[], None],
    dedup_key: str = "",
    hub=None,
) -> asyncio.Task[None]:
    """Run ``wake_preview_on_machine`` in the background; call ``fallback`` if it fails."""
    conversation_id = place.conversation_id
    _waking_conversations.add(conversation_id)

    async def _run() -> None:
        try:
            woken = await wake_preview_on_machine(
                bind,
                place=place,
                environment_id=environment_id,
                hub=hub,
            )
            if not woken:
                try:
                    fallback()
                except Exception:
                    logger.warning(
                        "fallback turn submission for preview wake in %s failed",
                        conversation_id,
                        exc_info=True,
                    )
                    redis = get_redis_client()
                    if redis is not None and dedup_key:
                        with contextlib.suppress(Exception):
                            await redis.delete(dedup_key)
        finally:
            _waking_conversations.discard(conversation_id)

    task = asyncio.create_task(_run())
    _WAKE_TASKS.add(task)
    task.add_done_callback(_WAKE_TASKS.discard)
    return task
