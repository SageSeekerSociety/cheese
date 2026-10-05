"""Answer waiting out its own Ask conversation.

An Ask answer may enter only the exact native conversation that asked the
question (``ask_inputs``, ``receipts``), and the room refuses to start another
one for it (``room/sessions.py``). By the time the person answers, that
conversation can be gone — the machine was rebuilt, the session exited — and
then there is nothing to deliver into and no decision left to make.

Retrying is what made that expensive. Every attempt failed the same fence, the
ledger read the failure as transient and dispatched it again 30 s later with no
cap, and the unread answer also rode into the seat's next prompt, where it
failed the same fence — the room's other messages stopped being answerable
because of it.

So the delivery is marked here: the dispatcher looks again every
``ASK_SESSION_RETRY_SECONDS``, each attempt only asks whether the conversation
is back and starts no turn, and the answer's blocks are held out of every
prompt batch (:func:`waiting_ask_blocks`). The answer stays unread, so the
conversation that asked still gets it if it comes back.

A wait older than ``ASK_SESSION_WAIT_GIVE_UP_SECONDS`` ends instead: the row is
failed (:func:`abandon_conversation_wait`) and the room is told
(``ask_answer_undelivered_notice``). The marker stays — a failed answer's blocks
must not ride into a prompt either.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.block.models import Block, in_conversation
from app.domain.delivery.models import Delivery

#: Payload marker: the wait itself. ``conversation`` is the one the answer may
#: enter, ``since`` when this wait began — the deadline counts from there, not
#: from when the person answered, so a platform that was down for a day does not
#: spend the window it was not looking. The marker's presence is what keeps the
#: answer out of prompts and the attempt cheap.
ASK_SESSION_WAIT = "awaiting_ask_native_session"
#: How long a waiting answer sits out before the dispatcher looks again.
ASK_SESSION_RETRY_SECONDS = 300
#: How long that wait may last before the answer is abandoned. A day covers the
#: machine sleeping through the night and coming back in the morning; past it,
#: the conversation is not coming back and waiting only hides the truth.
ASK_SESSION_WAIT_GIVE_UP_SECONDS = 24 * 3600
#: What the ledger row says while it waits: the honest version of "retry later".
ASK_SESSION_WAIT_REASON = "Waiting for the Ask conversation that asked; it is not live"
#: And why the row stopped waiting.
ASK_SESSION_GIVEN_UP_REASON = (
    "The Ask conversation that asked never came back; the answer was not delivered"
)


def now() -> datetime:
    return datetime.now(UTC)


def pinned_conversation(delivery) -> tuple[str, str] | None:
    """The one conversation this answer may enter: its harness, and its id."""
    origin = (delivery.payload or {}).get("ask_origin") or {}
    harness = origin.get("harness")
    conversation = origin.get("native_session_id")
    if not harness or not conversation:
        return None
    return harness, conversation


def waiting_for_conversation(delivery) -> bool:
    return ASK_SESSION_WAIT in (delivery.payload or {})


def waiting_since(delivery) -> datetime | None:
    """When this wait began, or None when the answer is not waiting."""
    marker = (delivery.payload or {}).get(ASK_SESSION_WAIT)
    if not isinstance(marker, dict) or not marker.get("since"):
        return None
    try:
        return datetime.fromisoformat(str(marker["since"]))
    except ValueError:
        return None


def wait_expired(delivery, *, at: datetime | None = None) -> bool:
    """Has this answer waited past ``ASK_SESSION_WAIT_GIVE_UP_SECONDS``?"""
    since = waiting_since(delivery)
    if since is None:
        return False
    return (at or now()) - since >= timedelta(seconds=ASK_SESSION_WAIT_GIVE_UP_SECONDS)


async def hold_for_conversation(session_factory, delivery_id, conversation) -> None:
    """Mark one answer as waiting for its own conversation, and back off.

    Stands even if the process dies before the attempt settles: the row already
    carries the wait and the long retry. The first hold stamps ``since`` and
    every later one keeps it, so a retry cannot push the deadline forward.
    """
    async with session_factory() as session:
        row = await session.scalar(
            select(Delivery).where(Delivery.id == delivery_id).with_for_update()
        )
        # A failed answer stays failed: only waiting ones are held again.
        if row is None or row.sent_at is not None or row.state == "failed":
            return
        since = waiting_since(row) or now()
        row.payload = {
            **(row.payload or {}),
            ASK_SESSION_WAIT: {
                "conversation": conversation,
                "since": since.isoformat(),
            },
        }
        row.last_error = ASK_SESSION_WAIT_REASON
        row.retry_at = now() + timedelta(seconds=ASK_SESSION_RETRY_SECONDS)
        await session.commit()


async def abandon_conversation_wait(session_factory, delivery_id) -> bool:
    """Stop waiting: fail the row and say it stopped. True when it did.

    ``ASK_SESSION_WAIT`` stays on purpose. A failed answer's blocks must not ride
    into a prompt either — the fence refuses them wherever they land, which is
    the failure this whole module exists to keep off the seat's other turns. The
    caller tells the room (``ask_answer_undelivered_notice``), once: the return
    value is what keeps a second attempt from saying it again.
    """
    async with session_factory() as session:
        row = await session.scalar(
            select(Delivery).where(Delivery.id == delivery_id).with_for_update()
        )
        if row is None or row.sent_at is not None or row.state == "failed":
            return False
        row.state = "failed"
        row.last_error = ASK_SESSION_GIVEN_UP_REASON
        row.retry_at = None
        row.lease_until = None
        await session.commit()
    return True


async def release_conversation_wait(session_factory, delivery_id) -> None:
    """That conversation is live again: the answer may be dispatched into it."""
    async with session_factory() as session:
        row = await session.scalar(
            select(Delivery).where(Delivery.id == delivery_id).with_for_update()
        )
        if row is None or not waiting_for_conversation(row):
            return
        row.payload = {
            key: value for key, value in row.payload.items() if key != ASK_SESSION_WAIT
        }
        await session.commit()


async def waiting_ask_blocks(session, *, topic_id, recipient_handle) -> set[uuid.UUID]:
    """This seat's answer blocks that no prompt may carry yet.

    A prompt in the seat reads them into whichever conversation it runs in, and
    the fence refuses that — which fails the turn that carried them, the
    person's own message in the same batch included. Waiting and abandoned
    answers alike: neither may enter a prompt.
    """
    events = list(
        await session.scalars(
            select(Delivery.event_id).where(
                Delivery.topic_id == topic_id,
                Delivery.recipient_handle == recipient_handle,
                Delivery.sent_at.is_(None),
                Delivery.payload[ASK_SESSION_WAIT].as_string().is_not(None),
            )
        )
    )
    if not events:
        return set()
    return set(
        await session.scalars(
            select(Block.id).where(
                in_conversation(topic_id),
                Block.meta["delivery_event_id"]
                .as_string()
                .in_([str(event) for event in events]),
            )
        )
    )
