"""What a room is told about its sessions' sandboxes on cloud.

A room hears about the sandbox (or the session's whole cloud VM), never the
host under it: which machine a session landed on, and how that machine was
opened, is the platform's own scheduling. Each line is written in the
caller's transaction and returned as the payload to publish once that
transaction has committed.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.block.schemas import BlockOut
from app.domain.machine.models import CloudHostHome


async def _line(
    session: AsyncSession, home: CloudHostHome, content: str, meta: dict
) -> dict | None:
    block = await announce(
        session,
        place_id=home.topic_id,
        content=content,
        meta={"who": "platform", "home": str(home.id), **meta},
    )
    if block is None:
        return None
    return BlockOut.model_validate(block).model_dump(mode="json")


async def tell_preparing(
    session: AsyncSession,
    home: CloudHostHome,
    sentence: str = "sandboxPreparing",
    *,
    whole_machine: bool = False,
) -> dict | None:
    """The first line of a sandbox getting ready: being prepared, woken
    (``sandboxWaking``) or restored from its archive (``sandboxRestoring``).
    A session's whole cloud VM is only ever prepared."""
    return await _line(
        session,
        home,
        say("cloudVmPreparing" if whole_machine else sentence),
        {"event_type": "cloud_startup", "severity": "info", **_vm(whole_machine)},
    )


async def tell_ready(
    session: AsyncSession, home: CloudHostHome, whole_machine: bool = False
) -> dict | None:
    return await _line(
        session,
        home,
        say("cloudVmReady" if whole_machine else "sandboxReady"),
        {
            "event_type": "cloud_provisioning",
            "state": "ready",
            "severity": "info",
            **_vm(whole_machine),
        },
    )


async def tell_replaced(
    session: AsyncSession, home: CloudHostHome, whole_machine: bool = False
) -> dict | None:
    return await _line(
        session,
        home,
        say("cloudVmReplaced" if whole_machine else "sandboxReplaced"),
        {"event_type": "cloud_startup", "severity": "info", **_vm(whole_machine)},
    )


async def tell_asleep(
    session: AsyncSession, home: CloudHostHome, minutes: int
) -> dict | None:
    return await _line(
        session,
        home,
        say("sandboxAsleep", minutes=minutes),
        {"event_type": "sandbox_asleep", "severity": "info"},
    )


async def tell_archive_lost(session: AsyncSession, home: CloudHostHome) -> dict | None:
    return await _line(
        session,
        home,
        say("sandboxArchiveLost"),
        {"event_type": "cloud_startup", "severity": "warn"},
    )


async def tell_restore_failed(
    session: AsyncSession, home: CloudHostHome, reason: str = ""
) -> dict | None:
    """A restore that did not finish says so, in the machine's own words.

    Without it the room keeps the ``sandboxRestoring`` line it was last given
    and its timer goes on climbing: a restore that keeps failing looked exactly
    like a restore that never ends, and the reason — which the tool call that
    asked for it did get — reached nobody who was reading the room."""
    said = reason.strip()[-1000:]
    return await _line(
        session,
        home,
        say("sandboxRestoreFailed"),
        {
            "event_type": "cloud_startup",
            "severity": "warn",
            "detail": said or None,
            "detail_label": say("labelReason") if said else None,
        },
    )


async def tell_vm_released(
    session: AsyncSession, home: CloudHostHome, minutes: int
) -> dict | None:
    return await _line(
        session,
        home,
        say("cloudVmReleasedIdle", minutes=minutes),
        {"event_type": "cloud_startup", "severity": "info", **_vm(True)},
    )


def _vm(whole_machine: bool) -> dict:
    """Lines about a whole cloud VM say so, for the screen that shows a room's
    startup lines by their state rather than their sentence."""
    return {"environment": "vm"} if whole_machine else {}


async def publish_line(topic_id: uuid.UUID, payload: dict | None) -> None:
    from app.domain.agent.runtime import get_broker

    if payload is not None:
        await get_broker().publish(
            str(topic_id), {"type": "event_block", "block": payload}
        )
