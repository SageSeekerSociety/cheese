"""Whose private memory a turn carries, and whether a turn is a routine's run.

A turn carries a person's private memory only in that person's private chat
with the agent: every turn there is theirs, and nobody else reads its records
or speaks to its session. In a shared room nobody's is carried. A room's seat
keeps one session from turn to turn and answers whoever speaks; a session that
had read one person's private memory would still hold it when it answers the
next — in its own conversation and in the transcript the room's executor is
served (`runner.transcript_chunk`) — and nothing it is asked to do then is
marked as that person's (a command's output names no file). So a shared room's
turns carry the shared memory only, and the agent there does not use any one
person's private preferences. The root design that would let a shared room
carry them is a session per (seat, speaker), or a fresh session whenever the
person a seat answers changes; until then the private chat is the one place.

(A module of its own: `chat.py` is near its size limit, and this is all reads.)
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.own import owned_instance
from app.domain.delivery.models import Delivery
from app.domain.device.models import DeviceRow
from app.domain.identity.handles import names_a_person
from app.domain.notice_meta import EVENT_ROUTINE_RUN
from app.domain.user.services import user_by_handle


async def is_routine_run(session: AsyncSession, delivery_id: uuid.UUID | None) -> bool:
    """这一轮是不是周期任务的一次执行：同样从那一笔投递上认。那一轮在支线里
    也可以写（存它的结果），之后有人追问的那几轮照支线的规矩只读。"""
    if delivery_id is None:
        return False

    row = await session.get(Delivery, delivery_id)
    payload = (row.payload if row is not None else None) or {}
    return payload.get("eventType") == EVENT_ROUTINE_RUN


async def private_memory_for(
    session: AsyncSession, instance_id: uuid.UUID, *, private_owner: str | None
) -> str | None:
    """Whose private memory this agent's turn carries: the person in a private
    chat (``private_owner``, None in any other room), and nobody's anywhere
    else (see the module's opening). Only a person has one (`names_a_person`).

    A member's own agent whose model connection is on their machine runs its
    session there (`owner_provider`), and its owner reads that disk: it
    carries the private memory only of the machine's owner, in their chat.
    Every other session runs on the platform's session host, kept to its own
    seat (`remote_execution/namespace._keep_only`)."""
    if not private_owner or not names_a_person(private_owner):
        return None
    owned = await owned_instance(session, instance_id)
    if owned is None or owned.on_account:
        return private_owner
    if owned.device_id is None:
        return None
    machine = await session.get(DeviceRow, owned.device_id)
    person = await user_by_handle(session, private_owner)
    if machine is None or person is None or machine.owner_user_id != person.id:
        return None
    return private_owner
