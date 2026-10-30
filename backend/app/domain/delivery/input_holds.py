"""Which registered inputs still hold their seat, and which no longer can."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.domain.delivery.agent import work_has_interval, work_interval_is_over
from app.domain.delivery.models import NativeInput


def unread_input_with_over_work():
    """No runner will report this input any more, so it may not hold.

    An input's OWN work is the turn opened to carry it (``input_id`` names the
    same work): a turn that existed only to deliver this batch, ended by the
    platform with the batch never read, has nothing left to wait for — keeping
    its hold would swallow a person's message, which is the one thing #416 does
    not allow.

    A batch that was an addition to another work (a mid-turn delivery) is
    different at first: a transport error does not prove the working session
    did not read it, and a session may still pick up what it was given as its
    work ends, so for a while its outcome is unknown and it keeps holding the
    seat. That while has an end. Once its work has been over for
    ``turn_silence_timeout_s`` — as long as a working turn may produce nothing
    at all — a read would long since have been reported, and nothing will
    settle it any more: a machine released under it (before #4783 ended those
    inputs at the release) left one holding a task's seat from 2026-10-23,
    while every later message to that teammate waited behind it. The same
    bound applies to an input whose work the platform never recorded an
    interval for: right after registering, its interval may still be on its
    way; long after, it never will be.

    Whether a work is over is the turn interval's fact
    (:func:`app.domain.delivery.agent.work_interval_is_over`). An input that no
    longer holds also stops hiding its batch (``receipts.held_blocks``), so
    the next prompt carries what it held.
    """
    settled_by = datetime.now(UTC) - timedelta(seconds=settings.turn_silence_timeout_s)
    return and_(
        NativeInput.echoed_at.is_(None),
        or_(
            and_(NativeInput.input_id == NativeInput.work_id, work_interval_is_over()),
            work_interval_is_over(before=settled_by),
            and_(NativeInput.registered_at <= settled_by, ~work_has_interval()),
        ),
    )


async def seat_has_unfinished_input(session, topic_id, recipient_handle):
    """Whether the seat holds an input whose fate is unknown.

    That is an input written to the session that it has neither read nor been
    reported never to read: until one of those is known, sending the seat a new
    input could put two answers to one message in the room. A read input holds
    nothing — it is delivered, and a session that is working takes whatever it
    is given next at its next tool boundary. One given up on (its work dead,
    ``terminated_at``) holds the seat no longer either, though its batch stays
    held. Nor does an unread input past the point its outcome could still be
    reported (:func:`unread_input_with_over_work`).
    """
    return (topic_id, recipient_handle) in await seats_with_unfinished_input(
        session, [(topic_id, recipient_handle)]
    )


async def seats_with_unfinished_input(session, pairs) -> set[tuple]:
    """`seat_has_unfinished_input` for many conversations and seats at once.

    One scan asks the same question of every message it is about to answer, and
    the messages come from a handful of conversations: one read of
    ``native_inputs`` covers them all instead of one per pair.
    """
    wanted = {(topic_id, handle) for topic_id, handle in pairs if handle}
    if not wanted:
        return set()
    rows = await session.execute(
        select(NativeInput.conversation_id, NativeInput.recipient_handle)
        .where(
            NativeInput.conversation_id.in_({topic_id for topic_id, _ in wanted}),
            NativeInput.recipient_handle.in_({handle for _, handle in wanted}),
            NativeInput.echoed_at.is_(None),
            NativeInput.terminated_at.is_(None),
            ~unread_input_with_over_work(),
        )
        .distinct()
    )
    return {tuple(row) for row in rows} & wanted
