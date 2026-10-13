"""Compose turn-stop accounting and post-commit room effects.

Gateway reads, task/room affinity and changesets are dirty upper-layer facts.
The store owns the one encompassing stop transaction, not those collaborators.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.event_lines import _Changeset
from app.domain.agent.gateway_usage import OWN_ROUTE
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.platform_notices import (
    EVENT_TURN_FAILED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.agent.service import AgentResult, AgentUsage
from app.domain.agent.turn.state.live import HookWorkState
from app.domain.agent.turn.steps.send import SystemEvent
from app.domain.agent.turn.store.completion import (
    CompletionEffects,
    forget_room_claims,
    settle_work,
)

logger = logging.getLogger(__name__)


def reported_usage(route: str, result: AgentResult) -> AgentUsage | None:
    """What the session says it used, where that is what counts.

    Claude Code's turns are metered where their requests go — the metering
    proxy for a subscription turn (`usage/subscription_ingest`), the gateway
    for the rest. What the session reports of itself counts only for a
    member's own Claude Code, whose requests reach neither; counted anywhere
    else it would charge the turn a second time."""
    usage = result.usage
    if result.harness == CLAUDE_CODE and route != OWN_ROUTE:
        return None
    if usage is None or not (
        usage.input_tokens or usage.output_tokens or usage.cost_usd
    ):
        return None
    return usage


class ChangeSummary(Protocol):
    async def __call__(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID | None,
        changeset: _Changeset,
    ) -> dict | None: ...


class TurnCompletion:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        gateway_enabled: bool,
        charge_spend: Callable[
            [uuid.UUID, uuid.UUID | None, uuid.UUID],
            Awaitable[list[AgentUsage] | None],
        ],
        defer_drain: Callable[[uuid.UUID, uuid.UUID, uuid.UUID], None],
        effects: CompletionEffects,
        post_event: SystemEvent,
        room_of_conversation: Callable[
            [uuid.UUID], Awaitable[tuple[uuid.UUID, uuid.UUID | None]]
        ],
        turn_changeset: Callable[
            [uuid.UUID, uuid.UUID, set[str] | None], Awaitable[_Changeset | None]
        ],
        persist_summary: ChangeSummary,
    ) -> None:
        self.sessions = sessions
        self.gateway_enabled = gateway_enabled
        self.charge_spend = charge_spend
        self.defer_drain = defer_drain
        self.effects = effects
        self.post_event = post_event
        self.room_of_conversation = room_of_conversation
        self.turn_changeset = turn_changeset
        self.persist_summary = persist_summary

    async def forget_claims(self, topic_id: uuid.UUID) -> None:
        """An unknown failed work can cost a replay, never a lost message."""
        try:
            await forget_room_claims(self.sessions, topic_id)
        except Exception:  # noqa: BLE001 — the failure notice matters more
            logger.exception("could not withdraw delivered claims (topic=%s)", topic_id)

    async def close(self, state: HookWorkState, result: AgentResult) -> list[dict]:
        usage = reported_usage(state.route, result)
        # One row PER MODEL: collapsing gateway spend loses the actual by-model
        # attribution. An empty delayed gateway drain is not evidence of zero.
        usages: list[AgentUsage] = []
        if self.gateway_enabled and state.route == "gateway":
            drained = await self.charge_spend(
                state.project_id, state.topic_id, state.work_id
            )
            if drained:
                usages = drained
            else:
                self.defer_drain(state.project_id, state.topic_id, state.work_id)
        elif usage is not None:
            usages = [usage]

        action_frames: list[dict] = []
        waiting = await settle_work(
            self.sessions,
            self.effects,
            state,
            result,
            usages,
            gateway_charged=state.route == "gateway" and self.gateway_enabled,
            own_route=state.route == OWN_ROUTE,
        )
        if waiting is not None:
            await self.post_event(
                state.topic_id,
                waiting,
                state.work_id,
                meta=notice(EVENT_TURN_FAILED, severity=SEVERITY_INFO, who=WHO_HUMAN),
            )

        # Task changes belong to its branch; the room reads its own checkout.
        _room, inner_id = await self.room_of_conversation(state.topic_id)
        changeset = (
            await self.turn_changeset(
                state.project_id,
                state.topic_id,
                None if state.known_commits is None else await state.known_commits,
            )
            if inner_id is None
            else None
        )
        if changeset is not None:
            payload = await self.persist_summary(
                project_id=state.project_id,
                topic_id=state.topic_id,
                turn_id=state.work_id,
                changeset=changeset,
            )
            if payload is not None:
                action_frames.append({"type": "event_block", "block": payload})
        return action_frames
