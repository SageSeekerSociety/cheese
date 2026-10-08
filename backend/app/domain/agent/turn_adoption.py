"""The turns a backend relays, against the turns the database has.

The broker (`runtime.InProcessBroker`) learns which turns are running from the
``turn_started`` frames it relays, and keeps that in this process's memory. The
database keeps the same fact durably: a turn's interval is open
(``AgentTurnRepository.open_on``) from the moment it is fed until whatever ends
it stamps ``stopped_at``.

Two processes share one database during a rollout — the next container comes up,
then the original is recreated — and each one relays only its own frames. So the
two books drift apart, in both directions, and every way they drift shows the
same way in the room: a turn that is over keeps 「正在思考」, or a turn that is
running is invisible.

* A turn this process never saw start: its interval is open, the broker has no
  record of it at all. Taken in when a page connects (``open_turns_on`` +
  ``adopt``), and again on every reconciliation tick.
* A turn this process took in that has since ended on the other container: the
  ``turn_finished`` went out on that process's channel and never reached this
  one, so nothing here ever ends it. Its agent is done and the room is stuck.
  Reconciliation ends it (``ended_on``).

The database is the only judge of which of the two it is, and both questions are
asked of it. Here rather than on the broker because `runtime.py` is over its
size cap.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.runtime import InProcessBroker

logger = logging.getLogger("cheesex.runtime")

#: How often a connected page re-reads its channel's books from the database.
#: This is the whole heal window: a turn that ended on the other container keeps
#: the room busy for at most this long. Matched to the chat socket's own
#: heartbeat (`useRoomSocket.ts`), which is the same order of staleness a page
#: already tolerates, and paid for with two indexed reads of one room's turns —
#: one when nothing is running here, two when something is.
RECONCILE_EVERY_S = 15.0


async def open_turns_on(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[tuple[str, float, str | None]]:
    """The delivered turns still open in the conversation."""
    return await AgentTurnRepository(session).open_on(conversation_id)


def adopt(
    broker: InProcessBroker,
    channel: str,
    turns: Iterable[tuple[str, float, str | None]],
) -> list[tuple[str, str | None]]:
    """Take in ``(turn id, started at, agent seat)`` the broker does not know.
    One it already knows is left as it is. The ``turn_finished`` that ends an
    adopted turn ends it like any other.

    Returns the ``(turn id, agent seat)`` of the turns it took in, so a caller
    can tell the room about them (``reconcile`` does).
    """
    taken: list[tuple[str, str | None]] = []
    for turn_id, started, agent in turns:
        if turn_id in broker._active.get(channel, ()):
            continue
        broker._active.setdefault(channel, set()).add(turn_id)
        broker._active_since.setdefault((channel, turn_id), started)
        taken.append((turn_id, agent))
        if agent and (
            followed := broker.activity.turn_started(channel, turn_id, agent)
        ):
            broker._fan_out(channel, followed)
    return taken


async def reconcile(
    session_factory,
    broker: InProcessBroker,
    channel: str,
    conversation_id: uuid.UUID,
) -> None:
    """Put one channel's books back in step with the database, once.

    Two readings of the turn table, and every change is published as the frame
    the owning process would have published, so a connected page needs to know
    nothing new: a turn the database has ended is ended here (``turn_finished``),
    and a turn running that this process has never heard of is taken in and
    announced (``turn_started``).

    What is deliberately NOT here: ending a turn merely because the database has
    no open interval for it. A turn's interval is opened a moment after the turn
    starts, so an id with no open row is as likely to be one still being born as
    one that is over — ``ended_on`` answers only for rows that actually carry an
    end, and silence stays not-answering. A turn's own ``turn_finished`` remains
    the only thing that ends one the database still holds open.
    """
    known = _turn_ids(broker.active_turn_ids(channel))
    async with session_factory() as session:
        turns = AgentTurnRepository(session)
        ended = await turns.ended_on(conversation_id, known)
        open_now = await turns.open_on(conversation_id)

    # Ends first: ending the last turn on a channel frees its replay buffer, and
    # a start would immediately fill it again for a channel that has nothing to
    # replay.
    ended_here = 0
    for turn_id in sorted(ended, key=str):
        # Read the books again rather than trust the ones the query was about: a
        # second socket on this channel, or the turn's own owner, can have ended
        # it while the query was in flight, and repeating an ending the room
        # already got is a frame nobody needed.
        if str(turn_id) not in broker.active_turn_ids(channel):
            continue
        await broker.publish(
            channel, {"type": "turn_finished", "turn_id": str(turn_id)}
        )
        ended_here += 1

    started_here = 0
    for turn_id, agent in adopt(broker, channel, open_now):
        # The owner's `turn_started` never reached this process, so a page here
        # was never told the turn exists: it shows a room that looks idle while
        # a teammate is at work. `adopt` has already put it in the books with
        # the start time the database recorded; this is the same frame the owner
        # sent.
        await broker.publish(
            channel, {"type": "turn_started", "turn_id": turn_id, "agent": agent}
        )
        started_here += 1

    if ended_here or started_here:
        logger.info(
            "turn_books_reconciled topic=%s ended=%d started=%d",
            channel,
            ended_here,
            started_here,
        )


async def watch_books(
    session_factory,
    broker: InProcessBroker,
    channel: str,
    conversation_id: uuid.UUID,
) -> None:
    """Keep one connected channel's books in step until its socket goes.

    Started by the chat route next to its relay task and cancelled with it, so a
    channel nobody is watching costs nothing. A database it cannot read is a
    later tick, never a broken socket: the relay is the room's feed and this is
    only the tidying behind it.
    """
    while True:
        # Read the interval each time round, so the constant stays the one
        # setting and a test can shrink it.
        await asyncio.sleep(RECONCILE_EVERY_S)
        try:
            await reconcile(session_factory, broker, channel, conversation_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the socket outlives a database blip
            logger.exception("reconciling turn books failed topic=%s", channel)


def _turn_ids(values: Iterable[str]) -> list[uuid.UUID]:
    """Those of these ids that can name a turn row.

    The broker's books are keyed by whatever a frame carried, and a turn row is
    named by a UUID. Anything else (a fixture's ``turn-a``, a frame from a
    server that keyed turns another way) has no row to be asked about, and
    asking would raise rather than answer.
    """
    parsed: list[uuid.UUID] = []
    for value in values:
        try:
            parsed.append(uuid.UUID(value))
        except ValueError:
            continue
    return parsed
