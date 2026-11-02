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
from app.domain.agent_session.models import AgentSession
from app.domain.machine import lifecycle
from app.domain.machine.models import SandboxHome
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.services import ACTIVE_STEP, HostPool
from app.domain.machine.session_work import _session_author
from app.domain.project.services import ProjectService
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


async def find_seat_session(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    seat: str,
) -> AgentSession | None:
    """The most recent ``AgentSession`` in ``conversation_id`` acting as ``seat``."""
    project = await ProjectService(db).get(project_id)
    if project is None:
        return None
    rows = await db.scalars(
        select(AgentSession)
        .where(AgentSession.conversation_id == conversation_id)
        .order_by(AgentSession.updated_at.desc())
    )
    for row in rows:
        if row.agent_handle == seat:
            return row
        if await _session_author(db, project, row.agent_handle) == seat:
            return row
    return None


async def has_sandbox(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Whether the session's sandbox still exists, running or frozen.

    A home that is on its way out (``stopped_at`` set) counts as gone: what it
    held is already being taken apart, so waking nothing would help."""
    home = await CloudHostRepository(db).current_home(session_id)
    return home is not None and home.stopped_at is None


async def touch_preview_activity(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    seat: str,
    *,
    project_id: uuid.UUID | None = None,
) -> list[asyncio.Task[bool]]:
    """Refresh ``SandboxHome.active_at`` when a reader browses an app preview.

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
        session_row = (
            await find_seat_session(
                db,
                project_id=project_id,
                conversation_id=conversation_id,
                seat=seat,
            )
            if project_id is not None
            else await db.scalar(
                select(AgentSession)
                .where(
                    AgentSession.conversation_id == conversation_id,
                    AgentSession.agent_handle == seat,
                )
                .order_by(AgentSession.updated_at.desc())
            )
        )
        if session_row is None:
            return []
        now = datetime.now(UTC)
        cutoff = now - ACTIVE_STEP
        updated = list(
            await db.execute(
                update(SandboxHome)
                .where(
                    SandboxHome.session_id == session_row.id,
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
            return [schedule_preview_thaw(db.bind, session_id=session_row.id)]
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


def schedule_preview_thaw(
    bind, *, session_id: uuid.UUID, hub=None
) -> asyncio.Task[bool]:
    """Thaw a frozen sandbox a preview is being read from, in the background.

    A reader opening the preview should not wait a host round trip for it, and
    a host that cannot thaw it must not break the preview request: the mark is
    left for the session's next tool call to thaw again."""
    task = asyncio.create_task(
        thaw_preview_on_machine(bind, session_id=session_id, hub=hub)
    )
    _THAW_TASKS.add(task)
    task.add_done_callback(_THAW_TASKS.discard)
    return task


async def thaw_preview_on_machine(bind, *, session_id: uuid.UUID, hub=None) -> bool:
    """Wake a frozen sandbox somebody is browsing, without a model turn.

    Its processes are resumed the way a tool call's wake does it: the home is
    marked under the pool's lock and the host call runs with no database lock
    held (`lifecycle.thaw_if_asked`). Returns whether there was a frozen home
    to wake; a failed thaw is logged and leaves `THAW_KEY` behind, which the
    session's next tool call retries."""
    hub = hub or device_hub
    try:
        async with AsyncSession(bind, expire_on_commit=False) as db:
            marked = await HostPool(db, hub=hub).mark_awake(session_id)
            await db.commit()
            if marked is None:
                return False
            device_id, work_resource = marked
            if device_id is None:
                return False
            await lifecycle.thaw_if_asked(
                db,
                hub,
                session_id=session_id,
                device_id=device_id,
                work_resource=work_resource,
            )
            return True
    except Exception:
        logger.warning("thawing the sandbox a preview belongs to failed", exc_info=True)
        return False


async def wake_preview_on_machine(
    bind, *, place: Place, session_id: uuid.UUID, hub=None
) -> bool:
    """Bring the session's own sandbox back, thawing it if it was frozen.

    No launch command runs and no model turn is spent: a frozen sandbox's
    processes resume in place, so an app it was running goes on running. A
    sandbox that is still running is left as it is. Returns False when there is
    no sandbox to bring up (it was destroyed), so the caller wakes the agent
    for a turn instead."""
    hub = hub or device_hub
    try:
        async with AsyncSession(bind, expire_on_commit=False) as db:
            marked = await HostPool(db, hub=hub).mark_awake(session_id)
            await db.commit()
            if marked is None:
                return False
            device_id, work_resource = marked
            if device_id is not None:
                await lifecycle.thaw_if_asked(
                    db,
                    hub,
                    session_id=session_id,
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
    session_id: uuid.UUID,
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
                session_id=session_id,
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
