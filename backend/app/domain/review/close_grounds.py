"""What a task's own AI teammate needs before it may close the task.

A merge no longer closes a task (`task_landing`): the teammate judges whether
the task is done and closes it itself. A close it gets wrong is the one thing
that ends a task its owner still wants to work in — a prototype shown and the
task closed, a PR merged that was one step of several — so the close route asks
it for one of two grounds the platform can check, and refuses it without one:

* its work is in: the task's last delivery landed (`Task.accepted_at`, and no
  card filed after it is waiting or was returned), and every item of the
  task's checklist is completed, or the teammate says why the rest is not
  being done — that reason is appended to the conclusion, where the owner
  reads it;
* its owner said it is over: the teammate names the message in the task where
  the owner said so, and the platform checks the message is the owner's,
  there.

The platform does not read the task document to judge what is left: free text
cannot be checked. A task being written up after its deploy went live
(`Task.closing_since`) already passed this when its teammate first closed it,
and is not asked again. People close a task with no grounds asked of them.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task
from app.domain.topic.models import TopicProgress

#: How long the reason for unchecked items may be: it goes into the conclusion.
UNCHECKED_REASON_CHARS = 500


@dataclass(frozen=True, slots=True)
class Grounds:
    """Why the close is allowed. ``unchecked``: the checklist items left
    undone, with the teammate's reason, to append to the conclusion."""

    owner_said: bool = False
    unchecked: str | None = None


async def grounds_to_close(
    session: AsyncSession,
    task: Task,
    *,
    owner_message: uuid.UUID | None,
    unchecked_reason: str | None,
) -> Grounds:
    """The grounds ``task``'s own session has to close it, or a refusal that
    says which two it could have (module docstring)."""
    if task.closing_since is not None:
        return Grounds()
    if owner_message is not None:
        said = await session.get(Block, owner_message)
        if (
            said is None
            or said.conversation_id != task.id
            or said.kind != BlockKind.message
            or not task.owner_handle
            or said.author != task.owner_handle
        ):
            raise ConflictError(say("taskCloseOwnerMessageNotFound"))
        return Grounds(owner_said=True)
    if not await _last_delivery_landed(session, task):
        raise ConflictError(say("taskCloseNeedsGrounds"))
    left = await _unchecked(session, task.id)
    if not left:
        return Grounds()
    reason = (unchecked_reason or "").strip()[:UNCHECKED_REASON_CHARS]
    if not reason:
        raise ConflictError(say("taskCloseChecklistOpen", items="；".join(left)))
    return Grounds(
        unchecked=say("taskClosedUnchecked", items="；".join(left), reason=reason)
    )


async def _last_delivery_landed(session: AsyncSession, task: Task) -> bool:
    """Whether ``task``'s last delivery landed: one did, and no card filed
    after it is still open or was sent back. A card voided (``revoked``) was
    never decided, and does not count either way."""
    if task.accepted_at is None:
        return False
    latest = await session.scalar(
        select(AcceptCard.status)
        .where(
            AcceptCard.task_id == task.id,
            AcceptCard.status != AcceptStatus.revoked,
        )
        .order_by(AcceptCard.created_at.desc())
        .limit(1)
    )
    return latest is None or latest == AcceptStatus.accepted


async def _unchecked(session: AsyncSession, task_id: uuid.UUID) -> list[str]:
    """The subjects of ``task``'s checklist items not completed."""
    items = await session.scalar(
        select(TopicProgress.items).where(TopicProgress.conversation_id == task_id)
    )
    return [
        str(item.get("subject") or item.get("content") or "").strip() or "（未命名）"
        for item in items or []
        if isinstance(item, dict) and item.get("status") != "completed"
    ]
