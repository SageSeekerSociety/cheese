"""What a room is told about its sessions' sandboxes on cloud.

Getting ready, being released when idle: those are the platform's own
running, kept as run records (`run_record`) for the 现场, not said in the
conversation. Only what a person has to act on, or would otherwise not know,
is said there: a sandbox released for want of credits, a sandbox replaced
because it stopped answering.

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
from app.domain.run_record.service import FRAME as RUN_RECORD_FRAME
from app.domain.run_record.service import as_payload as run_record_payload
from app.domain.run_record.service import record as keep_record


async def _conversation(session: AsyncSession, home: CloudHostHome) -> uuid.UUID:
    """The conversation the sandbox is for: its session's. A task's session
    works in the task's own conversation, while ``home.topic_id`` names the
    room its machine belongs to."""
    if home.session_id is not None:
        from app.domain.agent_session.models import AgentSession

        owner = await session.get(AgentSession, home.session_id)
        if owner is not None:
            return owner.conversation_id
    return home.topic_id


async def _line(
    session: AsyncSession, home: CloudHostHome, content: str, meta: dict
) -> dict | None:
    """Say it in the conversation: a person has something to do."""
    block = await announce(
        session,
        place_id=await _conversation(session, home),
        content=content,
        meta={"who": "platform", "home": str(home.id), **meta},
        published_by_caller=True,
    )
    if block is None:
        return None
    return BlockOut.model_validate(block).model_dump(mode="json")


async def _record(
    session: AsyncSession, home: CloudHostHome, content: str, meta: dict
) -> dict | None:
    """Keep it as a run record of the conversation the sandbox is for."""
    kept = await keep_record(
        session,
        conversation_id=await _conversation(session, home),
        content=content,
        meta={"who": "platform", "home": str(home.id), **meta},
    )
    return run_record_payload(kept) if kept is not None else None


async def tell_preparing(
    session: AsyncSession,
    home: CloudHostHome,
    sentence: str = "sandboxPreparing",
    *,
    whole_machine: bool = False,
) -> dict | None:
    """The first line of a sandbox (or a session's whole cloud VM) getting
    ready."""
    return await _record(
        session,
        home,
        say("cloudVmPreparing" if whole_machine else sentence),
        {"event_type": "cloud_startup", "severity": "info", **_vm(whole_machine)},
    )


async def tell_ready(
    session: AsyncSession, home: CloudHostHome, whole_machine: bool = False
) -> dict | None:
    return await _record(
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
    return await _record(
        session,
        home,
        say("cloudVmReplaced" if whole_machine else "sandboxReplaced"),
        {"event_type": "cloud_startup", "severity": "info", **_vm(whole_machine)},
    )


async def tell_lost(session: AsyncSession, home: CloudHostHome) -> dict | None:
    """The sandbox's host stopped answering and the pool gave up on it: the
    session works in a new sandbox from what was pushed."""
    return await _line(
        session,
        home,
        say("sandboxLost"),
        {"event_type": "cloud_startup", "severity": "warn"},
    )


async def tell_released(
    session: AsyncSession, home: CloudHostHome, minutes: int
) -> dict | None:
    return await _record(
        session,
        home,
        say("sandboxReleased", minutes=minutes),
        {"event_type": "sandbox_asleep", "severity": "info"},
    )


async def tell_unpaid(session: AsyncSession, home: CloudHostHome) -> dict | None:
    return await _line(
        session,
        home,
        say("sandboxReleasedNoCredits"),
        {"event_type": "sandbox_asleep", "severity": "warn"},
    )


async def tell_vm_released(
    session: AsyncSession, home: CloudHostHome, minutes: int
) -> dict | None:
    return await _record(
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

    if payload is None:
        return
    if payload.get("run_record"):
        await get_broker().publish(
            payload.get("conversation_id") or str(topic_id),
            {"type": RUN_RECORD_FRAME, "record": payload},
        )
        return
    await get_broker().publish(
        payload.get("conversation_id") or str(topic_id),
        {"type": "event_block", "block": payload},
    )
