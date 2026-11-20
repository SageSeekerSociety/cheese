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
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent_session.models import AgentSession
from app.domain.block.schemas import BlockOut
from app.domain.conversation.services import room_of
from app.domain.machine.models import Environment
from app.domain.repository.kept_work import keeps_work
from app.domain.run_record.service import FRAME as RUN_RECORD_FRAME
from app.domain.run_record.service import as_payload as run_record_payload
from app.domain.run_record.service import record as keep_record


async def _conversation(session: AsyncSession, home: Environment) -> uuid.UUID:
    """The conversation the sandbox is for: its session's. A task's session
    works in the task's own conversation, while ``home.topic_id`` names the
    room its machine belongs to."""
    if home.session_id is not None:
        owner = await session.get(AgentSession, home.session_id)
        if owner is not None:
            return owner.conversation_id
    return home.topic_id


async def say_in(
    session: AsyncSession, conversation_id: uuid.UUID, content: str, meta: dict
) -> dict | None:
    """Say it in a conversation, a room's or one inside a room (a task's, a
    支线's), as the platform. Returns what to publish once committed."""
    room = await room_of(session, conversation_id)
    block = await announce(
        session,
        place_id=room,
        task_id=None if conversation_id == room else conversation_id,
        content=content,
        meta={"who": "platform", **meta},
        published_by_caller=True,
    )
    if block is None:
        return None
    return BlockOut.model_validate(block).model_dump(mode="json")


async def _line(
    session: AsyncSession, home: Environment, content: str, meta: dict
) -> dict | None:
    """Say it in the conversation: a person has something to do. A person's
    own 芝士 conversation has no lines of the platform's in it; its 芝士 hears
    on its next tool call that the sandbox is a new one (``LOST_KEY``) and
    tells its person, and the line is kept as a record."""
    if home.project_id is None:
        return await _record(session, home, content, meta)
    return await say_in(
        session,
        await _conversation(session, home),
        content,
        {"home": str(home.id), **meta},
    )


async def _record(
    session: AsyncSession, home: Environment, content: str, meta: dict
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
    home: Environment,
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
    session: AsyncSession, home: Environment, whole_machine: bool = False
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
    session: AsyncSession, home: Environment, whole_machine: bool = False
) -> dict | None:
    return await _record(
        session,
        home,
        say("cloudVmReplaced" if whole_machine else "sandboxReplaced"),
        {"event_type": "cloud_startup", "severity": "info", **_vm(whole_machine)},
    )


async def _kept(session: AsyncSession, home: Environment) -> bool:
    """Whether the sandbox's project keeps its task checkouts' uncommitted
    work off the sandbox (`repository.kept_work`): there that work comes along
    to the next one too, and the ``…Kept`` sentence says so."""
    return home.project_id is not None and await keeps_work(home.project_id, session)


async def tell_lost(session: AsyncSession, home: Environment) -> dict | None:
    """The sandbox's host stopped answering and the pool gave up on it: the
    session works in a new sandbox from what was kept."""
    return await _line(
        session,
        home,
        say("sandboxLostKept" if await _kept(session, home) else "sandboxLost"),
        {"event_type": "cloud_startup", "severity": "warn"},
    )


async def tell_released(
    session: AsyncSession, home: Environment, minutes: int
) -> dict | None:
    return await _record(
        session,
        home,
        say(
            "sandboxReleasedKept" if await _kept(session, home) else "sandboxReleased",
            minutes=minutes,
        ),
        {"event_type": "sandbox_asleep", "severity": "info"},
    )


async def tell_unpaid(session: AsyncSession, home: Environment) -> dict | None:
    return await _line(
        session,
        home,
        say(
            "sandboxReleasedNoCreditsKept"
            if await _kept(session, home)
            else "sandboxReleasedNoCredits"
        ),
        {"event_type": "sandbox_asleep", "severity": "warn"},
    )


async def tell_vm_released(
    session: AsyncSession, home: Environment, minutes: int
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
