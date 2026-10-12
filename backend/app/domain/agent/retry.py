"""An explicit retry of a reply that failed after its input was received.

Receipt is not completion. Do not erase either fact or redispatch a delivery:
this is a new attempt of the same work, addressed to the same teammate.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.domain.agent.models import AgentTurn
from app.domain.block.indexed_rows import FAILED_TURN_EVENTS, SHOWN_ROWS
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.delivery.retry import (
    can_retry_inputs,
    failed_work_inputs,
    release_for_explicit_retry,
)


@dataclass(frozen=True)
class FailedReply:
    content: str
    seat: str
    continuation_id: uuid.UUID
    reply_to: str | None


async def take_failed_reply(session, conversation_id, *, block_id=None, seats=()):
    """Claim the latest failure once, or retire an unusable retry entry.

    The failed card is locked until its retry flag is cleared. Concurrent clicks
    cannot revive the same failed attempt, and a later turn supersedes it even
    if its output has not arrived yet. The caller commits before starting work.
    """
    block = await session.scalar(
        select(Block)
        .where(Block.conversation_id == conversation_id, SHOWN_ROWS)
        .order_by(Block.seq.desc())
        .limit(1)
    )
    if (
        block is None
        or (block_id is not None and block.id != block_id)
        or block.kind != BlockKind.event
        or (block.meta or {}).get("event_type") not in FAILED_TURN_EVENTS
        or (block.meta or {}).get("retryable") is not True
    ):
        return None, None
    turn = await session.get(AgentTurn, block.turn_id) if block.turn_id else None
    candidate = None
    inputs = []
    if (
        turn is not None
        and turn.conversation_id == conversation_id
        and turn.agent_handle is not None
        and turn.stopped_at is not None
        and turn.delivered_at is not None
        and turn.agent_handle in seats
    ):
        newer = await session.scalar(
            select(AgentTurn.id)
            .where(
                AgentTurn.conversation_id == conversation_id,
                AgentTurn.agent_handle == turn.agent_handle,
                AgentTurn.started_at > turn.started_at,
            )
            .limit(1)
        )
        inputs = await failed_work_inputs(
            session,
            conversation_id=conversation_id,
            recipient_handle=turn.agent_handle,
            work_id=turn.id,
        )
        settled_failure = can_retry_inputs(inputs)
        source = turn
        if source.is_resume and source.continuation_id != source.id:
            source = await session.get(AgentTurn, source.continuation_id)
        if (
            newer is None
            and settled_failure
            and source is not None
            and source.conversation_id == conversation_id
            and source.resendable
            and source.content.strip()
        ):
            candidate = FailedReply(
                content=source.content,
                seat=turn.agent_handle,
                continuation_id=turn.continuation_id,
                reply_to=str(turn.reply_to) if turn.reply_to else None,
            )
    # Native inputs are locked before timeline blocks, like receipt settlement.
    block = await session.scalar(
        select(Block)
        .where(Block.id == block.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    latest = await session.scalar(
        select(Block.id)
        .where(Block.conversation_id == conversation_id, SHOWN_ROWS)
        .order_by(Block.seq.desc())
        .limit(1)
    )
    if (
        block is None
        or latest != block.id
        or (block.meta or {}).get("retryable") is not True
    ):
        return None, None
    if candidate is not None:
        release_for_explicit_retry(inputs)
    block.meta = {**block.meta, "retryable": False}
    await session.flush()
    return candidate, BlockOut.model_validate(block).model_dump(mode="json")
