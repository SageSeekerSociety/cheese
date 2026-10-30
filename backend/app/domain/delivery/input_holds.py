"""Which registered inputs still hold their seat, and which no longer can."""

from sqlalchemy import and_, or_, select

from app.domain.delivery.agent import receiver_gone, work_interval_is_over
from app.domain.delivery.models import NativeInput


def unread_input_with_over_work():
    """No runner will report this input any more, so it may not hold.

    An input's OWN work is the turn opened to carry it (``input_id`` names the
    same work): a turn that existed only to deliver this batch, ended by the
    platform with the batch never read, has nothing left to wait for — keeping
    its hold would swallow a person's message, which is the one thing #416 does
    not allow.

    A batch that was an addition to another work (a mid-turn delivery) is
    different: a transport error does not prove the working session did not
    read it, and a runner keeps an addition it has not read past the end of the
    work it was added to, so it may still be read and answered later. Its hold
    ends when the platform has recorded that nothing can read it any more
    (:func:`app.domain.delivery.agent.receiver_gone`): the process it was last
    handed to exited. Elapsed time is not that knowledge, an exit of the seat
    that names no process is not, and neither is the session's work host being
    given up: the session process may run elsewhere and still hold it. Without it, a machine released under such an input before
    anything ended it (before #4783 did so at the release) left it holding a
    task's seat from 2026-10-23, while every later message to that teammate
    waited behind it.

    Whether a work is over is the turn interval's fact
    (:func:`app.domain.delivery.agent.work_interval_is_over`). An input that no
    longer holds also stops hiding its batch (``receipts.held_blocks``), so
    the next prompt carries what it held.
    """
    return and_(
        NativeInput.echoed_at.is_(None),
        or_(
            and_(NativeInput.input_id == NativeInput.work_id, work_interval_is_over()),
            receiver_gone(),
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
