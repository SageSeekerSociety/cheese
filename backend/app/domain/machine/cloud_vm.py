"""A session's whole cloud VM is released once the session is idle.

A VM is disposable: nothing on it outlives the session's use of it, and code
is truth in git. Like a sandbox it goes once idle, and the session's next tool
call prepares a new one.

**Idle** is a sandbox's definition (``SandboxLifecycle._idle_since``), held for
the vm spec's idle time (``Spec.idle_s``): the session's own conversation runs
no turn of its own — not its room's, which every task of the channel hangs
under, and where another line's work used to keep this session's VM for days —
the session has asked for no tool since, and no command the executor runs for
it in the background keeps it up (up to ``cloud_vm_background_cap_s``). A
home that no session holds any more is measured by its own activity and
released like any other: it is nobody else's to release.

**Before it goes**, the VM gets the one best-effort checkpoint a switch of work
computer gives the machine it leaves (``session_work.checkpoint``, ``cheese
sync --all``), and goes whether or not it went through: what a lost
checkpoint does not push goes with the VM. Then the
session's lease and home go, the room is told, and the pool sweep deletes the
VM (``HostPool.maintain``).

The sandbox sweep runs this (``runner.SandboxSweeper``).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.agent.environment import VM, spec_of
from app.domain.agent_session import attachment as attachments
from app.domain.machine import holder as holders
from app.domain.machine import lease_claim, occupants, session_work
from app.domain.machine.models import CloudHost, SandboxHome
from app.domain.machine.progress import publish_line, tell_vm_released
from app.domain.machine.repositories import CloudHostRepository
from app.domain.machine.services import HostPool
from app.domain.topic.models import Topic

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
                SandboxHome.topic_id,
                SandboxHome.active_at,
                SandboxHome.spec,
                CloudHost.device_id,
            )
            .join(CloudHost, CloudHost.id == SandboxHome.host_id)
            .join(Topic, Topic.id == SandboxHome.topic_id)
            .where(
                # Every whole VM whose session has gone quiet, whatever the
                # room's state: an archived room's cleanup may never come for
                # its VM, which holds the machine and its slot until it does,
                # and a home no session holds any more is nobody's to release
                # but this sweep's.
                CloudHost.whole_machine,
                CloudHost.released_at.is_(None),
                CloudHost.device_id.is_not(None),
                SandboxHome.active_at < now - idle_for,
                # A whole cloud VM is not frozen (``lifecycle``): it is
                # released, and this is where that happens.
                SandboxHome.frozen_at.is_(None),
            )
            .order_by(SandboxHome.active_at)
        )
    ).all()
    await db.commit()
    released = 0
    attempts = 0
    for home in candidates:
        # Protected homes must not occupy the whole batch on every tick.
        # Keep the bound on checkpoint attempts after the idle check.
        if attempts >= PER_SWEEP:
            break
        if await lifecycle._idle_since(home, now, idle_for) is None:
            continue
        attempts += 1
        if await _release(db, lifecycle, home, idle_for):
            released += 1
    return released


async def _release(db, lifecycle, home, idle_for: timedelta) -> bool:
    now = datetime.now(UTC)
    working = await occupants.sessions_of(db, home.id)
    row = working[0] if working else None
    held = await attachments.attachment_of(db, row.id) if row is not None else None
    lease = held.environment if held is not None else None
    if lease is not None and lease.get("device_id") != home.device_id:
        lease = None
    if lease is not None and lease_claim.still_preparing(lease):
        await db.commit()
        return False
    # A turn of a session working in it keeps its VM. Asked before the
    # checkpoint: a session at work must not be restarted and pushed for a
    # release that is not going to happen.
    if await _at_work(lifecycle, working, now):
        await db.commit()
        return False
    if row is not None and lease is not None and lease.get("state"):
        # No transaction is held while the VM runs the checkpoint.
        start = await session_work.restart_executor(db, row, lease)
        await db.commit()
        await session_work.checkpoint(lease, start)

    # Decide again under the holder's lock, which every tool call takes first:
    # a tool call or a turn since the look above keeps the VM. The holder's
    # own lock, not the one for executions: an archived room takes no tool
    # call, and its VM still goes.
    await holders.lock(db, home.topic_id)
    working = await occupants.sessions_of(db, home.id, lock=True)
    current = await db.scalar(
        select(SandboxHome)
        .where(SandboxHome.id == home.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None or current.active_at != home.active_at:
        await db.commit()
        return False
    # Looked at again with the room locked: no turn of a session working in
    # it has started since, and none is running.
    if await _at_work(lifecycle, working, now):
        await db.commit()
        return False
    minutes = int(idle_for.total_seconds() // 60)
    line = await tell_vm_released(db, current, minutes)
    named = await occupants.environment_named(db, home.id)
    if named is None:
        # Nobody to move off it, and so no home nobody works in on a whole
        # VM: the home goes, and the machine with it.
        await CloudHostRepository(db).delete_home(current)
    else:
        await HostPool(db).leave(named)
    for row in working:
        held = await attachments.attachment_of(db, row.id)
        if held is not None and held.environment_id == named:
            await attachments.unattach(db, row.id)
    await db.commit()
    await publish_line(home.topic_id, line)
    logger.info("released the idle cloud vm of environment %s", named)
    return True


async def _at_work(lifecycle, working, now: datetime) -> bool:
    """Whether a session working in the VM is running a turn, which keeps
    it. A home nobody works in is nobody's work."""
    for row in working:
        if await lifecycle.at_work(row.conversation_id, row.agent_handle, now):
            return True
    return False
