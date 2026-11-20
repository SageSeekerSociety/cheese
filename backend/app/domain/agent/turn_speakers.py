"""Whose private memory a turn carries, and whether a turn is a routine's run.

In a person's private chat with the agent, the turn carries that person's whole
private memory: every turn there is theirs, and nobody else reads its records or
speaks to its session.

In a multi-person place — a channel's main line, a task, a 支线 — a turn carries,
for each person it answers, only that person's `communication` notes: how they
want to be talked to (`memory.files.talk_view`). A seat there keeps one session
from turn to turn and answers whoever speaks; what it read for one person is
still in its context, and in the transcript the place's executor is served
(`runner.transcript_chunk`), when it answers the next. So the class that reaches
a multi-person place is the one whose staying behind does not change how the
agent treats anybody else; everything else of anyone's private memory stays in
their private chat. The platform picks that class by the note's type, not the
model.

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
from app.domain.user.services import handles_by_ids, user_by_handle


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
    """Whose whole private memory this agent's turn carries: the person in a
    private chat (``private_owner``, None in any other place). Only a person has
    one (`names_a_person`).

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


async def talk_notes_for(
    session: AsyncSession, instance_id: uuid.UUID, answered: list[str]
) -> list[str]:
    """In a multi-person place, whose `communication` notes this agent's turn
    carries: the people it answers (``answered``), held to the same machine
    rule as a private chat (`private_memory_for`) — a session on a member's own
    machine is read by that member, so it carries only theirs."""
    people = list(dict.fromkeys(h for h in answered if names_a_person(h)))
    if not people:
        return []
    owned = await owned_instance(session, instance_id)
    if owned is None or owned.on_account:
        return people
    if owned.device_id is None:
        return []
    machine = await session.get(DeviceRow, owned.device_id)
    if machine is None or machine.owner_user_id is None:
        return []
    owner = await handles_by_ids(session, [machine.owner_user_id])
    return [handle for handle in people if handle in owner]
