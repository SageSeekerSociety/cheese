"""A session's whole cloud VM is released once the session is idle.

A VM is disposable: nothing on it outlives the session's use of it, and code
is truth in git. So it is not kept asleep the way a sandbox's home is, and the
session's next tool call prepares a new one.

**Idle** is a sandbox's definition (``SandboxLifecycle._idle_since``), held for
``cloud_vm_idle_release_s``: the room runs no turn, the session has asked for
no tool since, counted from the later of its last tool call and the end of the
room's last turn, and no command the executor runs for it in the background
keeps it up (up to ``cloud_sandbox_background_cap_s``).

**Before it goes**, the session's work is pushed exactly as a switch of work
computer pushes it (``session_work.push_before_switch``, ``cheese sync
--all``): unpushed commits to their branches, what is not committed as a
snapshot ``cheese recover`` restores. A VM whose push fails, or that cannot be
reached to push, is kept and asked again ``RETRY_AFTER`` later: work that is
only there is never released with it. Once pushed, the session's lease and
home go, the room is told, and the pool sweep deletes the VM
(``HostPool.maintain``).

The sandbox sweep runs this (``runner.SandboxSweeper``).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.errors import ConflictError
from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import lease_claim, session_work
from app.domain.machine.models import CloudHost, CloudHostHome
from app.domain.machine.progress import publish_line, tell_vm_released
from app.domain.machine.services import HostPool
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.services import TopicService

logger = logging.getLogger("cheese.machine.cloud_vm")

#: A VM whose push failed is asked again this long after.
RETRY_AFTER = timedelta(minutes=10)
#: VMs looked at per sweep: each may need a push of up to
#: ``session_work.PUSH_WAIT_S``.
PER_SWEEP = 5

# When each home's push last failed, in this process: the sweep runs every few
# seconds, and a push is a command on the VM.
_push_failed: dict[uuid.UUID, datetime] = {}


async def release_idle(db, lifecycle) -> int:
    """Push and release every idle VM; returns how many were released.
    ``lifecycle`` is the ``SandboxLifecycle`` whose idle measure is used."""
    now = datetime.now(UTC)
    idle_for = timedelta(seconds=settings.cloud_vm_idle_release_s)
    candidates = (
        await db.execute(
            select(
                CloudHostHome.id,
                CloudHostHome.session_id,
                CloudHostHome.topic_id,
                CloudHostHome.left_at,
                CloudHostHome.active_at,
                CloudHost.device_id,
            )
            .join(CloudHost, CloudHost.id == CloudHostHome.host_id)
            .join(Topic, Topic.id == CloudHostHome.topic_id)
            .where(
                # An archived room's VM goes with the room's cleanup, which
                # checks its work was published before removing anything.
                Topic.status != TopicStatus.archived,
                CloudHost.whole_machine,
                CloudHost.released_at.is_(None),
                CloudHost.device_id.is_not(None),
                CloudHostHome.left_at.is_(None),
                CloudHostHome.session_id.is_not(None),
                CloudHostHome.active_at < now - idle_for,
            )
            .order_by(CloudHostHome.active_at)
            .limit(PER_SWEEP)
        )
    ).all()
    await db.commit()
    released = 0
    for home in candidates:
        failed = _push_failed.get(home.id)
        if failed is not None and now - failed < RETRY_AFTER:
            continue
        if await lifecycle._idle_since(home, now, idle_for) is None:
            continue
        if await _release(db, lifecycle, home, idle_for):
            released += 1
    return released


async def _release(db, lifecycle, home, idle_for: timedelta) -> bool:
    row = await AgentSessionService(db).by_id(home.session_id)
    lease = row.work_lease if row is not None else None
    if lease is not None and lease.get("device_id") != home.device_id:
        lease = None
    if lease is not None and lease_claim.still_preparing(lease):
        await db.commit()
        return False
    if lease is not None and lease.get("state"):
        # The VM goes only after its work is on the branches. No transaction
        # is held while the VM runs the push.
        start = await session_work.restart_executor(db, row, lease)
        await db.commit()
        try:
            await session_work.push_before_switch(lease, start, keeps_files=False)
        except ConflictError as refused:
            # Unreachable (`WorkComputerUnreachable`) or the push failed: the
            # VM keeps the work, and is asked again later.
            _push_failed[home.id] = datetime.now(UTC)
            logger.warning(
                "idle cloud vm of session %s kept: its work was not pushed: %s",
                home.session_id,
                refused,
            )
            return False
    _push_failed.pop(home.id, None)

    # Decide again under the room's lock, which every tool call takes first:
    # a tool call or a turn since the look above keeps the VM.
    await TopicService(db).lock_for_execution(home.topic_id)
    row = await AgentSessionService(db).by_id(home.session_id, lock=True)
    current = await db.scalar(
        select(CloudHostHome)
        .where(CloudHostHome.id == home.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        current is None
        or current.left_at is not None
        or current.active_at != home.active_at
    ):
        await db.commit()
        return False
    # Looked at with the room locked: no turn has started since.
    from app.domain.agent.models import AgentTurn
    from app.domain.conversation.services import of_room

    running = await db.scalar(
        select(AgentTurn.id)
        .where(
            of_room(AgentTurn.conversation_id, home.topic_id),
            AgentTurn.stopped_at.is_(None),
        )
        .limit(1)
    )
    if running is not None:
        await db.commit()
        return False
    if row is not None and (row.work_lease or {}).get("device_id") == home.device_id:
        row.work_lease = None
    minutes = int(idle_for.total_seconds() // 60)
    line = await tell_vm_released(db, current, minutes)
    await HostPool(db).leave(home.session_id, kept_work=False)
    await db.commit()
    await publish_line(home.topic_id, line)
    logger.info("released the idle cloud vm of session %s", home.session_id)
    return True
