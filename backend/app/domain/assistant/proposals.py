"""What a 芝士 drafts for the person to send.

A person's 芝士 does not speak in a project for them: what it would say there
— a message to a channel or a task, a task to open with what it is for — it
drafts, and the person sends it or puts it aside. A draft is one row here
(``propose``), shown in the panel as a card. Sending goes through the routes
the person would use by hand, as the person; the card is then marked
(``settle``) with what was made.

Kinds and what their payload says:

- ``message``: ``topic`` (a channel or a task), ``topicName``, ``text``.
- ``task``: ``channel``, ``channelName``, ``title``, ``brief`` (said as the
  task's first message, which starts its AI teammate on it).

Each carries a ``request`` id, which the message sent for it is posted with: a
send tried twice posts once.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.assistant.models import AssistantProposal

KINDS = ("message", "task", "act")
#: What a pending draft can become.
SETTLED = ("sent", "dismissed")


async def propose(
    db: AsyncSession, *, user_id: int, work: str, kind: str, payload: dict
) -> AssistantProposal:
    """Keep a draft of the answer ``work``, for ``user_id`` to send. Flushed,
    so it has its id; the caller commits."""
    row = AssistantProposal(
        user_id=user_id,
        work=work,
        kind=kind,
        payload={**payload, "request": str(uuid.uuid4())},
        state="pending",
    )
    db.add(row)
    await db.flush()
    return row


def view(row: AssistantProposal) -> dict:
    """A draft as the panel takes it."""
    return {
        **row.payload,
        "id": row.id,
        "kind": row.kind,
        "state": row.state,
        **({"result": row.result} if row.result else {}),
    }


async def now_of(
    db: AsyncSession, user_id: int, ids: list[int]
) -> dict[int, tuple[str, dict | None]]:
    """Where these drafts of the person's stand now: a kept answer carries them
    as they were drafted."""
    if not ids:
        return {}
    rows = await db.execute(
        select(
            AssistantProposal.id, AssistantProposal.state, AssistantProposal.result
        ).where(AssistantProposal.id.in_(ids), AssistantProposal.user_id == user_id)
    )
    return {pid: (state, result) for pid, state, result in rows.all()}


async def settle(
    db: AsyncSession,
    *,
    user_id: int,
    proposal_id: int,
    state: str,
    result: dict | None = None,
) -> AssistantProposal:
    """The person sent the draft (``result``: what was made) or put it aside.
    Only once: a settled draft stays as it was settled."""
    row = await db.get(AssistantProposal, proposal_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError.for_resource("proposal", proposal_id)
    if state not in SETTLED:
        raise BadRequestError(say("assistantProposalBadState"))
    if row.state != "pending":
        raise BadRequestError(say("assistantProposalSettled"))
    row.state = state
    row.result = result
    await db.commit()
    return row
