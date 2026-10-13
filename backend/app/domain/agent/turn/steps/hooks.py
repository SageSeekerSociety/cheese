"""A native event's publication and Stop execution, without the intake host.

The intake supplies a landed frame and narrow completion/scheduling effects.
This owner preserves the order and exception scopes of the real event path.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.sentences import error_frame
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.service import AgentEvent, AgentResult, AgentToolUse
from app.domain.agent.session_turn_events import SessionTurnEvents
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from app.domain.agent.turn.store.intervals import answered_open_turns

logger = logging.getLogger(__name__)


class FinishWork(Protocol):
    async def __call__(
        self, state: HookWorkState, result: AgentResult
    ) -> list[dict]: ...


class HookActivity(Protocol):
    async def __call__(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        active: bool,
        *,
        agent_handle: str | None = None,
    ) -> None: ...


class InsideInputs(Protocol):
    async def __call__(
        self, session: AsyncSession, topic_id: uuid.UUID, work_id: uuid.UUID
    ) -> list[uuid.UUID]: ...


class ReplayEvent(Protocol):
    async def __call__(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        event: AgentEvent,
        eid: str | None,
        result_text_seen: bool,
        platform_unsolicited: bool,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class LandedEvent:
    frame: dict | None = None
    error_line: str = ""
    error_code: str | None = None


async def execute_event(
    sessions: async_sessionmaker[AsyncSession],
    live: LiveWork,
    work_runner: SessionTurnEvents,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    event: AgentEvent,
    state: HookWorkState | None,
    landed: LandedEvent,
    close_interval: Callable[[uuid.UUID, uuid.UUID], Awaitable[None]],
    finish_work: FinishWork,
    forget_claims: Callable[[uuid.UUID], Awaitable[None]],
    inside_inputs: InsideInputs,
    replay_event: ReplayEvent,
    activity: HookActivity,
) -> None:
    broker = get_broker()
    if landed.frame is not None:
        await broker.publish(str(topic_id), landed.frame)
        if landed.frame["type"] in ("assistant_block", "event_block"):
            work_runner.note_session_output(
                turn_id, tool=isinstance(event, AgentToolUse)
            )
    if not isinstance(event, AgentResult):
        return
    # Stop ends the interval before settling input/usage or clearing its live
    # context. The coroutine that fed it may later close the same durable row.
    await close_interval(topic_id, turn_id)
    if state is not None:
        if event.taken_into is not None:
            state.known_commits = None
        try:
            for close_frame in await finish_work(state, event):
                await broker.publish(str(topic_id), close_frame)
        except Exception:
            logger.exception(
                "hook work close failed (topic=%s, work=%s)", topic_id, turn_id
            )
            # Neither a durable hold nor its completion context can disappear
            # before commit; a failed result remains replayable.
            raise
        else:
            live.hook_work.pop((topic_id, turn_id), None)
            if state.self_started:
                work_runner.close_turn_the_session_started(turn_id)
    elif event.is_error and event.taken_into is None:
        await forget_claims(topic_id)
    if event.taken_into is not None:
        return
    read_inside, open_turns = await answered_open_turns(
        sessions, inside_inputs, topic_id, turn_id
    )
    running = live.active_turn_ids.get(topic_id, set())
    taken = [
        work
        for work in read_inside
        if work in open_turns or work in running or (topic_id, work) in live.hook_work
    ]
    for input_turn in taken:
        await replay_event(
            project_id,
            topic_id,
            input_turn,
            AgentResult(
                text="",
                session_id=event.session_id,
                is_error=event.is_error,
                failure_code=event.failure_code,
                agent_handle=event.agent_handle,
                harness=event.harness,
                taken_into=turn_id,
            ),
            None,
            False,
            False,
        )
        await activity(
            project_id, topic_id, input_turn, False, agent_handle=event.agent_handle
        )
    if event.is_error and not event.late:
        frame = error_frame(
            landed.error_line or event.text, type="error", persisted=True
        )
        if landed.error_code:
            frame["code"] = landed.error_code
        await broker.publish(str(topic_id), frame)
    await broker.publish(str(topic_id), {"type": "done"})
