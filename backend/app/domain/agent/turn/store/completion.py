"""The session-stop transaction: usage, the read cursor and pending claims.

NativeInput/Delivery authority remains in delivery. Named same-session effects
are composed above this store; none can commit or publish ahead of this commit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.service import AgentResult, AgentUsage
from app.domain.agent.turn.state.inputs import _addressed_to, _pending_input_blocks
from app.domain.agent.turn.state.live import HookWorkState
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import CONSUMED_TURN_META_KEY, Block, prompted_turn
from app.domain.block.output_effects import consume_prompt, withdraw_prompt_claims
from app.domain.block.queries import turn_history


class RecordUsage(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        state: HookWorkState,
        usages: list[AgentUsage],
        *,
        gateway_charged: bool,
    ) -> None: ...


class OwnWaiting(Protocol):
    async def __call__(
        self, session: AsyncSession, state: HookWorkState, result: AgentResult
    ) -> str | None: ...


@dataclass(frozen=True, slots=True)
class CompletionEffects:
    record_usage: RecordUsage
    own_waiting: OwnWaiting


async def delivered_unread(
    session: AsyncSession, state: HookWorkState
) -> list[uuid.UUID]:
    """Delivered inputs for this exact agent, including a former process's batch.

    A clean Stop can settle a delivered prompt whichever process fed it. A
    prompt never delivered stays out; the session could not have heard it.
    """
    handle = state.agent_instance_handle
    if handle is None:
        return []
    history = await turn_history(session, state.topic_id)
    fed = {
        block.id: turn
        for block in _pending_input_blocks(history)
        if (turn := prompted_turn(block)) is not None and _addressed_to(block, handle)
    }
    delivered = await AgentTurnRepository(session).delivered(set(fed.values()))
    return [block_id for block_id, turn in fed.items() if turn in delivered]


async def forget_room_claims(
    sessions: async_sessionmaker[AsyncSession], topic_id: uuid.UUID
) -> None:
    """Withdraw claims after an identity-less session failure, in one transaction."""
    async with sessions() as session:
        history = await turn_history(session, topic_id)
        await withdraw_prompt_claims(
            session,
            [
                block.id
                for block in _pending_input_blocks(history)
                if prompted_turn(block) is not None
            ],
        )
        await session.commit()


async def settle_work(
    sessions: async_sessionmaker[AsyncSession],
    effects: CompletionEffects,
    state: HookWorkState,
    result: AgentResult,
    usages: list[AgentUsage],
    *,
    gateway_charged: bool,
    own_route: bool,
) -> str | None:
    """Commit all stop effects together, before any waiting or success notice."""
    async with sessions() as session:
        await effects.record_usage(
            session, state, usages, gateway_charged=gateway_charged
        )
        fed = list(state.pending_ids | set(await delivered_unread(session, state)))
        # What the seat has heard: for a native harness, what its runner said
        # the session read in this work — read is delivered, whatever came of
        # the work after (`delivery.receipts.record_receipt` consumed it then);
        # the non-native path has always taken a clean stop as delivery.
        heard = (
            result.inputs_read if result.harness is not None else not result.is_error
        )
        if not heard and result.is_error:
            # A prompt the session never took is not one it read.
            await withdraw_prompt_claims(session, fed)
        if result.harness is None and heard:
            # Non-native providers never register NativeInput batches.
            await consume_prompt(session, fed, state.work_id)
        if heard:
            # The seat has now read everything this prompt carried — including
            # other seats' messages, which it read but does not answer for and
            # so is not in `fed`. A turn recovered by this process has no
            # assembled window of its own — the process that assembled it is
            # gone — so what it read is taken from the blocks THIS turn
            # stamped.
            position = state.read_through or await _consumed_position(session, state)
            if position is not None:
                at, block_id = position
                await AgentSessionService(session).advance_read(
                    conversation_id=state.topic_id,
                    agent_handle=state.acting_agent,
                    at=at,
                    block_id=block_id,
                )
        waiting = (
            await effects.own_waiting(session, state, result) if own_route else None
        )
        await session.commit()
    return waiting


async def _consumed_position(
    session: AsyncSession, state: HookWorkState
) -> tuple[datetime, uuid.UUID] | None:
    """Where the newest block this turn stamped sits on the timeline, or None.

    The fallback for a turn this process did not assemble: its window is in the
    memory of a process that is gone, but the inputs it settled carry this
    turn's id on the timeline.
    """
    rows = await session.execute(
        select(Block.created_at, Block.id).where(
            Block.conversation_id == state.topic_id,
            Block.meta[CONSUMED_TURN_META_KEY].as_string() == str(state.work_id),
        )
    )
    return max(((at, block_id) for at, block_id in rows), default=None)
