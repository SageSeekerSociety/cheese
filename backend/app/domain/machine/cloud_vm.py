"""A session's whole cloud VM is released once the session is idle.

A VM is disposable: nothing on it outlives the session's use of it, and code
is truth in git. Like a sandbox it goes once idle, and the session's next tool
call prepares a new one.

**Idle** is a sandbox's definition (``SandboxLifecycle._idle_since``), held for
the vm spec's idle time (``Spec.idle_s``): the room runs no turn, the session
has asked for no tool since, counted from the later of its last tool call and
the end of the room's last turn, and no command the executor runs for it in
the background keeps it up (up to ``cloud_sandbox_background_cap_s``).

**Before it goes**, the VM gets the one best-effort checkpoint a switch of work
computer gives the machine it leaves (``session_work.checkpoint``, ``cheese
sync --all``), and goes whether or not it went through: what a lost
checkpoint loses is held by the snapshot of the session's last turn. Then the
session's lease and home go, the room is told, and the pool sweep deletes the
VM (``HostPool.maintain``).

The sandbox sweep runs this (``runner.SandboxSweeper``).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.agent.environment import VM, spec_of
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.conversation.services import of_room
from app.domain.machine import lease_claim, session_work
from app.domain.machine.models import CloudHost, SandboxHome
from app.domain.machine.progress import publish_line, tell_vm_released
from app.domain.machine.services import HostPool
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.services import TopicService

logger = logging.getLogger("cheese.machine.cloud_vm")

#: VMs looked at per sweep: each may need a checkpoint of up to
#: ``session_work.PUSH_WAIT_S``.
PER_SWEEP = 5


async def release_idle(db, lifecycle) -> int:
    """Checkpoint and release every idle VM; returns how many were released.
    ``lifecycle`` is the ``SandboxLifecycle`` whose idle measure is used."""
    now = datetime.now(UTC)
    vm = spec_of(VM)
    assert vm is not None
    idle_for = timedelta(seconds=vm.idle_s)
    candidates = (
        await db.execute(
            select(
                SandboxHome.id,
                SandboxHome.session_id,
                SandboxHome.topic_id,
                SandboxHome.active_at,
                CloudHost.device_id,
            )
            .join(CloudHost, CloudHost.id == SandboxHome.host_id)
            .join(Topic, Topic.id == SandboxHome.topic_id)
            .where(
                # An archived room's VM goes with the room's cleanup.
                Topic.status != TopicStatus.archived,
                CloudHost.whole_machine,
                CloudHost.released_at.is_(None),
                CloudHost.device_id.is_not(None),
                SandboxHome.session_id.is_not(None),
                SandboxHome.active_at < now - idle_for,
            )
            .order_by(SandboxHome.active_at)
            .limit(PER_SWEEP)
        )
    ).all()
    await db.commit()
    released = 0
    for home in candidates:
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
        # No transaction is held while the VM runs the checkpoint.
        start = await session_work.restart_executor(db, row, lease)
        await db.commit()
        await session_work.checkpoint(lease, start)

    # Decide again under the room's lock, which every tool call takes first:
    # a tool call or a turn since the look above keeps the VM.
    await TopicService(db).lock_for_execution(home.topic_id)
    row = await AgentSessionService(db).by_id(home.session_id, lock=True)
    current = await db.scalar(
        select(SandboxHome)
        .where(SandboxHome.id == home.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None or current.active_at != home.active_at:
        await db.commit()
        return False
    # Looked at with the room locked: no turn has started since.

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
    await HostPool(db).leave(home.session_id)
    await db.commit()
    await publish_line(home.topic_id, line)
    logger.info("released the idle cloud vm of session %s", home.session_id)
    return True
