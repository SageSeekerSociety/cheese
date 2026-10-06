"""Which cloud sandboxes run, for the compute meter (#2320 计费).

A sandbox is charged for the time it runs, from start to idle stop
(``usage.compute``). This module watches the homes and tells the meter: a
home whose sandbox runs gets an open run, and a run whose sandbox no longer
runs is closed. It runs on a clock of its own (``runner.ComputeMeterSweeper``)
rather than inside placement and every path that stops, archives or deletes a
home, so that no such path, now or added later, can leave a sandbox running
unmetered or metered forever. A start or stop is seen within one sweep; a stop
the lifecycle recorded is closed at the moment it recorded.

A sandbox **runs** while its home is on a host the pool still holds, enrolled,
and is neither asleep (``stopped_at``) nor waiting for its sandbox to be
prepared, woken or restored (``waiting_since``): the room is not charged while
it waits for a host. A whole cloud VM runs from its creation to its release,
since the platform pays for it all that time, priced by its size and charged
to the project it was created for.

When a project's payer has run out of credits, its running sandboxes are
stopped as soon as their room runs no turn: the turn that was running when the
credits ran out finishes, as a model turn does, and no new sandbox or VM
starts (``usage.compute.admit_start``, asked by ``HostPool.place``). A whole
VM is not stopped for it; the VM sweep releases it once it is idle.

With no price set nothing is metered, and no sandbox starts either.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.conversation.services import of_rooms, room_column
from app.domain.machine.models import GONE, CloudHost, CloudHostHome
from app.domain.usage.compute import SANDBOX, ComputeMeter, hourly_price

logger = logging.getLogger("cheese.machine.metering")

#: Whether each project's payer still has credits is asked this often.
CREDIT_CHECK = timedelta(minutes=1)
_checked_at: datetime | None = None
_unpriced_logged = False


def _running():
    return (
        select(
            CloudHostHome.id,
            CloudHostHome.project_id,
            CloudHostHome.topic_id,
            CloudHostHome.session_id,
        )
        .join(CloudHost, CloudHost.id == CloudHostHome.host_id)
        .where(
            CloudHostHome.stopped_at.is_(None),
            CloudHostHome.waiting_since.is_(None),
            CloudHost.device_id.is_not(None),
            CloudHost.released_at.is_(None),
            CloudHost.whole_machine.is_(False),
        )
    )


def vm_subject(host_id: uuid.UUID) -> str:
    return f"vm-{host_id}"


async def _observe_vms(session: AsyncSession, meter: ComputeMeter) -> tuple[int, int]:
    """A whole VM runs from its creation to its release, billed to the
    project it was created for."""
    from app.domain.usage.compute import VM, vm_spec

    hosts = list(
        await session.scalars(
            select(CloudHost).where(
                CloudHost.whole_machine.is_(True),
                CloudHost.project_id.is_not(None),
            )
        )
    )
    runs = {run.subject: run for run in await meter.open_runs(VM)}
    opened = closed = 0
    live = set()
    for host in hosts:
        subject = vm_subject(host.id)
        if host.released_at is not None or host.status in GONE:
            continue
        live.add(subject)
        if subject in runs:
            continue
        assert host.project_id is not None
        home = await session.scalar(
            select(CloudHostHome).where(CloudHostHome.host_id == host.id)
        )
        await meter.open(
            kind=VM,
            spec=vm_spec(int(host.cores), int(host.memory_mb)),
            subject=subject,
            project_id=host.project_id,
            topic_id=home.topic_id if home else None,
            session_id=home.session_id if home else None,
            at=host.created_at,
        )
        opened += 1
    released = {vm_subject(h.id): h.released_at for h in hosts}
    for subject in runs:
        if subject not in live:
            await meter.close(subject, released.get(subject) or datetime.now(UTC))
            closed += 1
    return opened, closed


async def observe(session: AsyncSession) -> dict[str, int]:
    """Open a run for every sandbox that runs and has none; close every run
    whose sandbox stopped, at the moment it stopped, or now when its home is
    gone or waiting. Commits."""
    global _unpriced_logged
    meter = ComputeMeter(session)
    now = datetime.now(UTC)
    running = {row.id: row for row in (await session.execute(_running())).all()}
    runs = {run.subject: run for run in await meter.open_runs(SANDBOX)}
    opened = closed = 0
    priced = hourly_price(SANDBOX, SANDBOX) is not None
    if not priced and running and not _unpriced_logged:
        logger.error(
            "CLOUD_SANDBOX_CREDITS_PER_HOUR is not set: %d running cloud "
            "sandboxes are not metered, and no new one starts",
            len(running),
        )
        _unpriced_logged = True
    if priced:
        for home_id, home in running.items():
            if str(home_id) in runs:
                continue
            await meter.open(
                kind=SANDBOX,
                spec=SANDBOX,
                subject=str(home_id),
                project_id=home.project_id,
                topic_id=home.topic_id,
                session_id=home.session_id,
                at=now,
            )
            opened += 1
    ended = [s for s in runs if uuid.UUID(s) not in running]
    stops: dict[uuid.UUID, datetime | None] = {}
    if ended:
        rows = await session.execute(
            select(CloudHostHome.id, CloudHostHome.stopped_at).where(
                CloudHostHome.id.in_([uuid.UUID(s) for s in ended])
            )
        )
        stops = {home_id: stopped_at for home_id, stopped_at in rows.all()}
    for subject in ended:
        stopped_at = stops.get(uuid.UUID(subject))
        await meter.close(subject, stopped_at or now)
        closed += 1
    vms_opened, vms_closed = await _observe_vms(session, meter)
    await session.commit()
    return {"opened": opened + vms_opened, "closed": closed + vms_closed}


async def unpaid_sandboxes(session: AsyncSession) -> list:
    """The running sandboxes whose project's payer has no credits left and
    whose room runs no turn: those to stop. Asked at most every
    ``CREDIT_CHECK``. Commits."""
    from app.domain.agent.models import AgentTurn
    from app.domain.usage.services import UsageService

    global _checked_at
    now = datetime.now(UTC)
    if _checked_at is not None and now - _checked_at < CREDIT_CHECK:
        return []
    _checked_at = now
    rows = (
        await session.execute(
            _running().add_columns(CloudHost.device_id, CloudHostHome.left_at)
        )
    ).all()
    usage = UsageService(session)
    refused: dict[uuid.UUID, bool] = {}
    for row in rows:
        if row.project_id not in refused:
            refused[row.project_id] = (
                await usage.admit_project(row.project_id) is not None
            )
    due = [row for row in rows if refused[row.project_id]]
    rooms = {row.topic_id for row in due}
    busy = set()
    if rooms:
        busy = set(
            await session.scalars(
                select(room_column(AgentTurn.conversation_id))
                .where(
                    of_rooms(AgentTurn.conversation_id, rooms),
                    AgentTurn.stopped_at.is_(None),
                )
                .distinct()
            )
        )
    await session.commit()
    return [row for row in due if row.topic_id not in busy]
