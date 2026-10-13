"""The session-stop transaction: exact input completion, usage and pending claims.

NativeInput/Delivery authority remains in delivery. Named same-session effects
are composed above this store; none can commit or publish ahead of this commit.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.service import AgentResult, AgentUsage
from app.domain.agent.turn.state.inputs import _addressed_to, _pending_input_blocks
from app.domain.agent.turn.state.live import HookWorkState
from app.domain.block.models import prompted_turn
from app.domain.block.output_effects import consume_prompt, withdraw_prompt_claims
from app.domain.block.queries import turn_history


class CompleteInputs(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        recipient_handle: str | None,
        harness: str,
        native_session_id: str,
        work_id: uuid.UUID,
    ) -> set[uuid.UUID]: ...


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
    complete_inputs: CompleteInputs
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
        # Exact native completion locks inputs BEFORE touching timeline blocks.
        # Synthetic error / identity-less results cannot release a held batch.
        if (
            not result.is_error
            and result.input_work_completed
            and result.session_id
            and result.harness
            and result.agent_handle == state.acting_agent
        ):
            await effects.complete_inputs(
                session,
                project_id=state.project_id,
                conversation_id=state.topic_id,
                recipient_handle=result.agent_handle,
                harness=result.harness,
                native_session_id=result.session_id,
                work_id=state.work_id,
            )
        await effects.record_usage(
            session, state, usages, gateway_charged=gateway_charged
        )
        fed = list(state.pending_ids | set(await delivered_unread(session, state)))
        if result.is_error:
            await withdraw_prompt_claims(session, fed)
        elif result.harness is None:
            # Non-native providers never register NativeInput batches.
            await consume_prompt(session, fed, state.work_id)
        waiting = (
            await effects.own_waiting(session, state, result) if own_route else None
        )
        await session.commit()
    return waiting
