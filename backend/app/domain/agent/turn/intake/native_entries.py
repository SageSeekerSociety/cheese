"""Native entry intake and exact postcommit takeover cleanup.

Attachment locking remains around the real bind/transition transaction. The
existing native-input authority owns the algorithm; no receiver walk is copied.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.agent import attachments, turn_inputs
from app.domain.agent.models import AgentTurn
from app.domain.agent.nonce import nonce_in
from app.domain.agent.service import (
    AgentUserEntry,
)
from app.domain.agent.session_turn_events import SessionTurnEvents
from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent.turn_inputs import AgentSeatOwner, bind, transition
from app.domain.agent_session.models import AgentSession


async def _drop_takeover_marks(
    sessions,
    live: LiveWork,
    work_runner: "SessionTurnEvents",
    topic_id: uuid.UUID,
    seat: str,
    generation: uuid.UUID,
) -> None:
    """Drop the memory side of exactly the predecessor this seat's owner
    record names as retired (FB-56 P2-2).

    The target comes from the durable owner record — the takeover wrote it
    there in the same transaction as the crown — never from scanning the
    topic: another seat's self-started work, an earlier generation's, and
    anything whose interval is still open is left alone, and a caller
    cancelled between commit and cleanup is finished by the next replay,
    because the closed row plus the recorded id is the whole predicate.
    """

    async with sessions() as session:
        owner = await session.get(AgentSeatOwner, (topic_id, seat, generation))
        retired = owner.retired_turn_id if owner is not None else None
        if retired is None:
            return
        state = live.hook_work.get((topic_id, retired))
        if state is None or not state.self_started:
            return
        closed = await session.scalar(
            select(AgentTurn.stopped_at).where(AgentTurn.id == retired)
        )
    if closed is None:
        return
    if live.hook_work.pop((topic_id, retired), None) is not None:
        work_runner.close_turn_the_session_started(retired)


async def _bind_user_entry(
    sessions,
    live: LiveWork,
    work_runner: "SessionTurnEvents",
    *,
    topic_id: uuid.UUID,
    seat: str | None,
    event: AgentUserEntry,
) -> None:
    """Bind one native user entry to its platform input, and walk the owner.

    The entry is the one durable proof that an input was consumed (FB-56):
    its marker names the input in the platform's own ledger, the input names
    the work, and the owner walk retires exactly the predecessor's interval
    — never a page, never a position.

    The active-source check and the mutation share one transaction: the
    seat's pointer is read in the same session that binds and walks, so a
    reader that looked before the pointer moved — a new session, a new
    generation, or a detach — cannot write through (P2-1). A refusal moves
    nothing: ledger, owner, head, retirements and marks are all untouched.

    The memory side of a takeover is dropped only after the database side
    is durable, and re-derived from the durable rows rather than from the
    outcome in memory (P2-2): a commit that fails rolls the database back
    and leaves the memory in place; a caller cancelled between commit and
    cleanup is finished by the next replay, because the closed row is the
    predicate.
    """
    nonce = nonce_in(event.text)
    if nonce is None or seat is None or not event.generation:
        return
    generation = uuid.UUID(event.generation)
    # The whole mutation holds the seat's attachment lock: a detach/attach
    # can only land whole-before or whole-after it, and an event draining
    # from a superseded subscription is refused at the lock — including when
    # the seat has NO live subscription at all (`current` is None), where
    # anything arriving is by definition not from the active source (FB-56).
    seat_tuple = (topic_id, seat)
    async with attachments.lock(seat_tuple):
        if (
            event.attachment is not None
            and attachments.current(seat_tuple) != event.attachment
        ):
            return
        async with sessions() as session:
            if event.session_id and event.harness:
                # The seat's pointer row is LOCKED for the mutation's whole
                # transaction: a detach or a new session's attachment takes the
                # same row lock on its write side, so a pointer that moves has
                # either already moved (we see it and refuse) or waits for us
                # (FB-56 P2-1 — a read alone, even in this transaction, would
                # race the next write under READ COMMITTED).

                current = await session.scalar(
                    select(AgentSession.resume_token)
                    .where(
                        AgentSession.conversation_id == topic_id,
                        AgentSession.agent_handle == seat,
                        AgentSession.harness == event.harness,
                    )
                    .with_for_update()
                )
                if current is not None and current != event.session_id:
                    return
            row = await bind(
                session,
                nonce=nonce,
                entry_id=event.entry_id,
                pos=event.pos,
                generation=generation,
                session_id=event.session_id,
                at=datetime.now(UTC),
            )
            if row is not None:
                # The native entry IS consumption proof — acceptance evidence
                # a caller cancelling after the transport took the write
                # cannot take away (FB-56 P1): the parent interval and the
                # input are stamped delivered here, monotone, in the bind's
                # own transaction. An early Stop then has a delivered row to
                # close; nothing native is declared ended by it.
                await turn_inputs.stamp_delivered(
                    session, turn_id=row.turn_id, at=datetime.now(UTC)
                )
                await transition(
                    session,
                    topic_id=topic_id,
                    agent_handle=seat,
                    session_id=event.session_id or row.session_id,
                    generation=generation,
                    work_id=row.turn_id,
                    input_id=row.id,
                    entry_id=event.entry_id,
                    pos=event.pos,
                    at=datetime.now(UTC),
                )
            await session.commit()
    await _drop_takeover_marks(sessions, live, work_runner, topic_id, seat, generation)
