"""Durable startup observations, separate from the turn wake-up watermark."""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.machine.models import (
    MAX_PROVIDER_ERRORS,
    PROVIDER_ERROR_WINDOW,
    ProjectMachine,
)
from app.domain.machine.repositories import ProjectMachineRepository

logger = logging.getLogger(__name__)

#: The line that ends a session machine's startup record in its room: its
#: connector has reached the platform, which is what the enrollment's last step
#: (「连接器安装完成，等待平台确认连接」) waits for.
CONNECTED = say("cloudConnected")
#: How long an enrolled machine's connector has to reach the platform before
#: the room is told it did not. The bootstrap has already seen the service stay
#: up, so a connector that is still not heard from after this is not coming on
#: its own.
CONNECT_GRACE = timedelta(minutes=5)
#: Allocations older than this are not looked at again: every one has been
#: told one way or the other long before, and the look reads the room's events.
SETTLE_WINDOW = timedelta(hours=1)

_STARTUP_EVENTS = ("cloud_startup", "cloud_provisioning")

#: ``meta.retired`` on the line that tells a room one of its machines failed at
#: the provider and was let go. Those lines are the room's count of failures:
#: the machine rows themselves are dropped once the provider has deleted them.
PROVIDER_ERROR = "provider_error"


async def provider_errors(session: AsyncSession, topic_id: uuid.UUID) -> int:
    """How many of the room's machines the provider failed within the window."""
    since = datetime.now(UTC) - PROVIDER_ERROR_WINDOW
    return (
        await session.scalar(
            select(func.count())
            .select_from(Block)
            .where(
                Block.topic_id == topic_id,
                Block.kind == BlockKind.event,
                Block.created_at >= since,
                Block.meta["retired"].as_string() == PROVIDER_ERROR,
            )
        )
        or 0
    )


async def tell_machine_replaced(
    session: AsyncSession, machine: ProjectMachine, *, failures: int
) -> dict | None:
    """Say in the machine's room that it was let go after a provider error,
    and whether another is coming; written in the caller's transaction, since
    the line is also the count. Returns what to publish once committed."""
    topic_id = machine.topic_id
    assert topic_id is not None
    exhausted = failures >= MAX_PROVIDER_ERRORS
    minutes = int(PROVIDER_ERROR_WINDOW.total_seconds() // 60)
    block = await announce(
        session,
        place_id=topic_id,
        content=say("cloudMachineGaveUp", count=failures, minutes=minutes)
        if exhausted
        else say("cloudMachineReplaced", attempt=failures + 1),
        meta={
            "event_type": "cloud_startup",
            "severity": "error" if exhausted else "info",
            "who": "platform",
            "machine": str(machine.id),
            "retired": PROVIDER_ERROR,
            **({"state": "failed"} if exhausted else {}),
        },
    )
    if block is None:
        return None
    return BlockOut.model_validate(block).model_dump(mode="json")


async def publish_line(topic_id: uuid.UUID, payload: dict | None) -> None:
    from app.domain.agent.runtime import get_broker

    if payload is not None:
        await get_broker().publish(
            str(topic_id), {"type": "event_block", "block": payload}
        )


async def startup_progress(
    topic_id: uuid.UUID | None,
    text: str,
    *,
    machine_id: uuid.UUID | None = None,
    failed: bool = False,
) -> None:
    from app.domain.agent.runtime import get_broker

    if topic_id is None:
        return
    # Observability must not turn a working machine into a failed enrollment.
    try:
        async with async_session_factory() as session:
            block = await announce(
                session,
                place_id=topic_id,
                content=text,
                meta={
                    "event_type": "cloud_startup",
                    "severity": "info",
                    "who": "platform",
                    # Which allocation this line is about: a room can hold more
                    # than one, and only the one it names may end its record.
                    **({"machine": str(machine_id)} if machine_id else {}),
                    **({"state": "failed"} if failed else {}),
                },
            )
            if block is None:
                return
            payload = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
        await get_broker().publish(
            str(topic_id), {"type": "event_block", "block": payload}
        )
    except Exception:
        logger.exception("Could not record cloud startup progress for %s", topic_id)


async def settle_startups(
    machines: list[ProjectMachine],
    is_online: Callable[[str], bool],
    *,
    now: datetime | None = None,
) -> None:
    """End the startup record of every session machine whose outcome is known.

    A session machine's record used to stop at 「等待平台确认连接」: the line
    that closes it was written only by the room-lease wake-up, which a session
    allocation never goes through. The room then counted 「已等待」 for as long
    as anyone looked, with the machine connected and working the whole time.

    Level-triggered from the enrollment sweep with the allocations
    ``MachineService.unsettled_startups`` names, so it does not matter whether
    the connector attached before or after enrollment was recorded. Each
    outcome is said once: a machine whose connector is online gets
    ``CONNECTED``; one enrolled for ``CONNECT_GRACE`` without connecting gets a
    failure and the platform stops waiting. A connector that turns up after
    that is still announced, since the room was told it was not coming.
    """
    now = now or datetime.now(UTC)
    for machine in machines:
        assert machine.device_id is not None
        if is_online(machine.device_id):
            await _close(machine, ready=True)
        elif now - max(machine.created_at, machine.enrolled_at or now) >= (
            CONNECT_GRACE
        ):
            await _close(machine, ready=False)


async def _close(machine: ProjectMachine, *, ready: bool) -> None:
    from app.domain.agent.runtime import get_broker

    topic_id = machine.topic_id
    assert topic_id is not None
    try:
        async with async_session_factory() as session:
            # Two sweeps must not both find the record open and both close it.
            await ProjectMachineRepository(session).lock_topic(topic_id)
            latest = await session.scalar(
                select(Block.meta)
                .where(
                    Block.topic_id == topic_id,
                    Block.kind == BlockKind.event,
                    Block.created_at >= machine.created_at,
                    Block.meta["event_type"].as_string().in_(_STARTUP_EVENTS),
                )
                .order_by(Block.created_at.desc(), Block.id.desc())
                .limit(1)
            )
            if latest is None:
                await session.commit()
                return
            state = latest.get("state")
            ended = {"ready"} if ready else {"ready", "failed"}
            if (
                latest.get("machine") not in (None, str(machine.id))
                # A room lease waiting to deliver a held message is told by
                # the wake-up that delivers it (machine/wakeup.py).
                or state == "waiting"
                or state in ended
            ):
                await session.commit()
                return
            minutes = int(CONNECT_GRACE.total_seconds() // 60)
            block = await announce(
                session,
                place_id=topic_id,
                content=CONNECTED
                if ready
                else say("cloudConnectorSilent", minutes=minutes),
                meta={
                    "event_type": "cloud_provisioning",
                    "state": "ready",
                    "severity": "info",
                    "who": "platform",
                    "machine": str(machine.id),
                }
                if ready
                else {
                    "event_type": "cloud_startup",
                    "state": "failed",
                    "severity": "error",
                    "who": "human",
                    "machine": str(machine.id),
                    "detail": say("cloudConnectorSilentDetail", host=machine.hostname),
                    "detail_label": say("labelWhatHappensNext"),
                },
            )
            if block is None:
                return
            payload = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
        await get_broker().publish(
            str(topic_id), {"type": "event_block", "block": payload}
        )
    except Exception:
        logger.exception("Could not settle cloud startup for %s", topic_id)
