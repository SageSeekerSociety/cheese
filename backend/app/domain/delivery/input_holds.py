"""Which registered inputs still hold their seat, and which no longer can."""

from sqlalchemy import and_, select

from app.domain.delivery.agent import work_interval_is_over
from app.domain.delivery.models import NativeInput


def unread_input_with_over_work():
    """No native receipt can settle this input any more, so it may not hold.

    Only an echoed input is journaled under its execution work, and that
    journal is the interval completion and termination both read. An input the
    session never took therefore has no interval a receipt could name.

    An input's OWN work is the turn opened to carry it (``input_id`` names the
    same work): a turn that existed only to deliver this batch, ended by the
    platform with the batch never read, has nothing left to wait for — keeping
    its hold would swallow a person's message, which is the one thing #416 does
    not allow. A batch that was an addition to another work (a mid-turn
    delivery) is different: a transport error does not prove the working
    session did not read it, so its outcome stays unknown and it keeps holding
    and blocking the seat. Whether that work is over is the turn interval's
    fact (:func:`app.domain.delivery.agent.work_interval_is_over`) — a work the
    platform has no row for is not over, silence is not a conclusion.
    """
    return and_(
        NativeInput.echoed_at.is_(None),
        NativeInput.input_id == NativeInput.work_id,
        work_interval_is_over(),
    )


async def seat_has_unfinished_input(session, topic_id, recipient_handle):
    """Missing native start or outstanding holds cannot authorize a new send.

    A work interval that is confirmed dead is not an outstanding hold. Its rows
    stay unfinished — nothing says the answer inside them was taken — but they
    stop standing between the seat and a NEW input, which is a different input
    with an identity of its own. An interval nobody confirmed still blocks. So
    does one whose batch nobody ever read, for as long as its work is alive:
    that is the interval a receipt is still on its way for. A batch nobody read
    whose work the platform has already ended cannot get one
    (:func:`unread_input_with_over_work`).
    """
    return (topic_id, recipient_handle) in await seats_with_unfinished_input(
        session, [(topic_id, recipient_handle)]
    )


async def seats_with_unfinished_input(session, pairs) -> set[tuple]:
    """`seat_has_unfinished_input` for many conversations and seats at once.

    One scan asks the same question of every message it is about to answer, and
    the messages come from a handful of conversations: one read of
    ``native_inputs`` covers them all instead of one per pair. The judgement is
    the single-pair one, row by row — the same ``unread_input_with_over_work``
    filter, and a row holds its seat only while neither its completion nor its
    termination is recorded.
    """
    wanted = {(topic_id, handle) for topic_id, handle in pairs if handle}
    if not wanted:
        return set()
    rows = await session.execute(
        select(
            NativeInput.conversation_id,
            NativeInput.recipient_handle,
            NativeInput.completed_at,
            NativeInput.terminated_at,
        ).where(
            NativeInput.conversation_id.in_({topic_id for topic_id, _ in wanted}),
            NativeInput.recipient_handle.in_({handle for _, handle in wanted}),
            ~unread_input_with_over_work(),
        )
    )
    return {
        (conversation_id, recipient_handle)
        for conversation_id, recipient_handle, completed_at, terminated_at in rows
        if completed_at is None and terminated_at is None
    } & wanted
