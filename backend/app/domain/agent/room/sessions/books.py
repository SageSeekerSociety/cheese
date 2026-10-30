"""What the room hears, and the books kept from it.

``_hear`` is the one door: one thing a session said or did, handed to the room's
reader. Around it sit the receipt a runner's acceptance produces
(``_hear_receipt``), the room's memory reconciliation (``reconcile_memory``,
``memory``), and, for a harness whose runner keeps no stuck-work clocks of its
own (`harness/driven/lifecycle.py`'s ``Watchdog``), the room's account of the
open turn — ``pulse`` records a mark, ``verdict`` asks whether the seat's clocks
show a turn stuck in a way nothing else notices, and ``_end_by_verdict`` ends
one.

``_consume`` and ``_activity`` are the two shapes everything here takes: an event
to the room (or to the platform's own queue, when the work is the platform's), and
a turn opening or closing. Both are on the path that ends a turn without an
answer, which is ``reading``'s; the door is shared, so it is one module.
"""

from __future__ import annotations

import logging
import time
import uuid

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven.subscription import Seat
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_CODE,
    PROMPT_UNDELIVERED_MESSAGE,
    TURN_TIMEOUT_CODE,
    TURN_TIMEOUT_MESSAGE,
)
from app.domain.agent.reads import (
    Read,
    Received,
    Working,
)
from app.domain.agent.room import sessions as pkg
from app.domain.agent.room.sessions.records import Clock, Live
from app.domain.agent.service import AgentResult
from app.domain.delivery.input_identity import InputReceipt

logger = logging.getLogger("app.domain.agent.room.sessions")


async def _hear(
    svc: pkg.RoomSessions, session: SessionRef, read: Read, *, required: bool = False
) -> object:
    """Hand the room one thing its session said or did, and what the room
    answered (``room.reads.RoomReader``). What the room has to hear for its
    books to be right (``required``) is an error to drop; the rest is only
    what it shows."""
    if svc.reader is not None:
        return await svc.reader(session, read)
    if required:
        raise RuntimeError(f"{svc.label} room reader is not bound")
    return None


def _room(
    svc: pkg.RoomSessions, project: uuid.UUID, topic: uuid.UUID, agent: str = ""
) -> SessionRef:
    return SessionRef(project, topic, agent, harness=svc.harness)


async def _hear_receipt(svc: pkg.RoomSessions, receipt: InputReceipt) -> None:
    identity = receipt.identity
    await svc._hear(
        svc._room(identity.project_id, identity.conversation_id),
        Read(str(identity.work_id), Received(receipt)),
        required=True,
    )


async def reconcile_memory(svc: pkg.RoomSessions, session: SessionRef) -> None:
    """Ask the room to reconcile its memory tree, and never fail the turn on it.

    A memory tree that could not be reconciled is a memory that is a turn
    behind — the next moment asks again, with the same three sides. Letting
    the exception through would end a turn that was otherwise fine, over the
    room's notes.
    """
    if svc._memory is None:
        return
    try:
        await svc._memory(session)
    except Exception:
        logger.exception(
            "memory reconciliation failed conversation=%s agent=%s",
            session.conversation_id,
            session.agent_handle,
        )


async def memory(
    svc: pkg.RoomSessions, session: SessionRef, request: dict
) -> dict | None:
    """One memory reconciliation, with the live session of ``session``'s
    seat. ``None`` is 「这里没有记忆文件」 — no live session there, or a
    harness whose sessions keep none — which the caller reads as 「这一轮不用
    对账」, not as a failure."""
    live = svc.live.get(svc._seat_of(session))
    if live is None:
        return None
    return await svc.host.memory(live.ref, request)


def pulse(svc: pkg.RoomSessions, seat: Seat, marks: frozenset[str]) -> None:
    """What a record just said about the seat's open turn."""
    if clock := svc.clocks.get(seat):
        clock.pulse(marks, time.monotonic())


def verdict(svc: pkg.RoomSessions, seat: Seat) -> AgentResult | None:
    """Is the seat's open turn stuck in a way only its own clocks can show?"""
    topic = seat[0]
    clock = svc.clocks.get(seat)
    if clock is None:
        return None
    now = time.monotonic()
    if (
        svc.no_progress_s > 0
        and clock.said is not None
        and now - clock.said < pkg.TALKING_S
        and now - clock.progressed >= svc.no_progress_s
    ):
        logger.warning(
            "%s output for %.0fs with no tool call or ending topic=%s",
            svc.label,
            now - clock.progressed,
            topic,
        )
        return AgentResult(
            text=TURN_TIMEOUT_MESSAGE,
            session_id=None,
            is_error=True,
            failure_code=TURN_TIMEOUT_CODE,
        )
    # Input is read at tool boundaries, so while a tool runs the clock does
    # not run, and after one it runs from the return: a message that sat
    # behind a 40-minute command gets its grace from the first moment the
    # session could have read it. Before the turn said anything there is no
    # evidence of reading at all, which is the process's business.
    if (
        svc.unread_grace_s > 0
        and svc.unread is not None
        and clock.seen
        and not clock.running
    ):
        waiting = svc.unread(topic)
        if waiting is not None:
            waiting = max(waiting, clock.returned or 0.0, clock.opened)
            if now - waiting >= svc.unread_grace_s:
                logger.warning(
                    "%s input unread for %.0fs topic=%s",
                    svc.label,
                    now - waiting,
                    topic,
                )
                return AgentResult(
                    text=PROMPT_UNDELIVERED_MESSAGE,
                    session_id=None,
                    is_error=True,
                    failure_code=PROMPT_UNDELIVERED_CODE,
                )
    return None


async def _end_by_verdict(
    svc: pkg.RoomSessions, live: Live, result: AgentResult
) -> None:
    """End the turn in the room's books, and take the work away."""
    seat = svc._seat_of(live.session)
    work = svc.work[seat]
    await svc._consume(
        live.session.project_id,
        seat[0],
        work,
        AgentResult(
            text=result.text,
            session_id=live.conversation,
            is_error=result.is_error,
            failure_code=result.failure_code,
            agent_handle=live.acting,
            harness=svc.harness,
        ),
        f"{svc.harness}:{live.conversation}:verdict:{work}",
        False,
        False,
    )
    await svc._activity(live.session.project_id, seat, work, False)
    svc.closed.add(work)
    await svc.host.stop(live.ref)


async def _consume(
    svc: pkg.RoomSessions, project, topic, work, event, eid, seen, unsolicited
):
    if isinstance(event, AgentResult) and work in svc.closed:
        return
    await svc._hear(
        svc._room(project, topic),
        Read(
            str(work),
            event,
            eid=eid,
            text_seen=seen,
            unsolicited=unsolicited,
        ),
        required=True,
    )


async def _activity(svc: pkg.RoomSessions, project, seat: Seat, work, active):
    if work in svc.closed:
        return
    if active:
        svc.work[seat] = work
        now = time.monotonic()
        svc.clocks[seat] = Clock(opened=now, progressed=now)
    elif svc.work.get(seat) == work:
        svc.work.pop(seat, None)
        svc.clocks.pop(seat, None)
    await svc._hear(
        svc._room(project, seat[0], seat[1]), Read(str(work), Working(active))
    )
