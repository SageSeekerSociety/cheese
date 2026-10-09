"""Resolve a native event and land its durable effects before turn execution.

Dirty room/seat/message preparation stays here. The execution owner receives
only the landed frame, exact work context and named completion/scheduling effects.
Neither layer receives ChatService or a many-method private Host protocol.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.sentences import say
from app.domain.agent.event_lines import _is_platform_tool, _short_tool_name
from app.domain.agent.platform_failures import (
    TURN_TIMEOUT_MESSAGE,
)
from app.domain.agent.platform_notices import (
    EVENT_TURN_TIMEOUT,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.agent.prompt import _compaction_notice
from app.domain.agent.room_events import post_system_event
from app.domain.agent.service import (
    AgentCompacting,
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentRetrying,
    AgentSessionInfo,
    AgentStepFailed,
    AgentStepOutput,
    AgentToolResult,
    AgentToolUse,
    AgentUserEntry,
    proves_output,
)
from app.domain.agent.session_turn_events import SessionTurnEvents
from app.domain.agent.step_output import without_output
from app.domain.agent.turn.intake.assistant import AssistantMessages
from app.domain.agent.turn.intake.completion import TurnCompletion
from app.domain.agent.turn.intake.events import (
    _persist_subagent_result,
    _persist_tool_event,
    announce_action,
)
from app.domain.agent.turn.intake.native_entries import _bind_user_entry
from app.domain.agent.turn.intake.notes import (
    _keep_note,
    _note_compaction,
    _note_retry,
    _running_compactions,
)
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from app.domain.agent.turn.state.notices import _TOOL_ACTION, _turn_failure_notice
from app.domain.agent.turn.steps.hooks import HookActivity, LandedEvent, execute_event
from app.domain.agent.turn.store.events import _mark_step_failed, _record_step_output
from app.domain.agent.turn.store.intervals import turn_credits_refused
from app.domain.agent.turn_inputs import mark_session_for_turn
from app.domain.delivery.receipts import inputs_answered_inside
from app.domain.usage.credits import (
    CREDITS_EXHAUSTED_META,
    credits_event,
)

logger = logging.getLogger(__name__)


class BeginTurn(Protocol):
    async def __call__(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        opened: bool = False,
        agent_handle: str | None = None,
        session_id: str | None = None,
    ) -> HookWorkState | None: ...


class SaveSession(Protocol):
    async def __call__(
        self,
        topic_id: uuid.UUID,
        session_id: str,
        *,
        agent_handle: str | None = None,
        harness: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class HookEvents:
    sessions: async_sessionmaker
    live: LiveWork
    work_runner: SessionTurnEvents
    messages: AssistantMessages
    completion: TurnCompletion
    begin_turn: BeginTurn
    save_session: SaveSession
    resolve_room: Callable[[uuid.UUID], Awaitable[tuple[uuid.UUID, uuid.UUID | None]]]
    close_interval: Callable[[uuid.UUID, uuid.UUID], Awaitable[None]]
    activity: HookActivity
    thread_replied: Callable[[uuid.UUID], Awaitable[None]]
    policy: Callable[[uuid.UUID], Awaitable[dict | None]]

    async def credits_refused(self, turn_id: uuid.UUID) -> bool:
        try:
            return await turn_credits_refused(self.sessions, turn_id)
        except Exception:  # noqa: BLE001 — a failed read must not break the notice
            logger.exception("could not read credits-refused stamp for %s", turn_id)
            return False

    async def accept(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        event: AgentEvent,
        eid: str | None,
        result_text_seen: bool,
        platform_unsolicited: bool,
    ) -> None:
        sessions, live, work_runner = self.sessions, self.live, self.work_runner
        frame: dict | None = None
        error_line, error_code = "", None
        state = live.hook_work.get((topic_id, turn_id))
        if state is None and platform_unsolicited and proves_output([event]):
            # Nobody fed this session anything and it is producing output anyway
            # — one of its workers finished and the completion notice woke it.
            # That is a whole turn, and it gets a turn's bookkeeping from here:
            # an interval a sweep can find, and the context its Stop needs to
            # close the books. Opened on OUTPUT rather than on the first hook of
            # any kind, because only output guarantees the Stop that closes it.
            state = await self.begin_turn(
                project_id,
                topic_id,
                turn_id,
                # The event's own seat first (stamped by the subscription that
                # read it): with several seats live in one room, the
                # room-keyed fallback below answers "who spoke LAST", not
                # "whose session this output came from".
                agent_handle=getattr(event, "agent_handle", None)
                or live.room_session_agents.get(topic_id),
                session_id=getattr(event, "session_id", None),
            )
        # `topic_id` names the session's conversation: a room's, or a task's own.
        # What it says lands in the room's table, beside its task, and goes out on
        # the conversation's channel — the one its view subscribes to.
        conversation_id = topic_id
        room_id, inner_id = await self.resolve_room(conversation_id)
        channel = str(conversation_id)
        if not isinstance(event, AgentRetrying):
            # Anything else the turn does ends a streak of retries: the request
            # went through. The next retry is news of its own.
            live.retry_notes.pop(turn_id, None)
        if isinstance(event, AgentResult):
            live.waiting_notes.pop(turn_id, None)
            # The turn ended with the compaction still open (it was stopped, or
            # the session died): the line must not go on saying it is compacting.
            # Asked of the room, not only of this process: the line may have been
            # landed by the backend this one replaced.
            await _note_compaction(
                sessions,
                live,
                turn_id,
                AgentCompacting(done=True, error=say("contextCompactSessionEnded")),
                channel=channel,
            )
        if isinstance(event, AgentSessionInfo):
            if event.agent_handle:
                live.room_session_agents[topic_id] = event.agent_handle
            await self.save_session(
                topic_id,
                event.session_id,
                agent_handle=event.agent_handle,
                harness=event.harness,
            )
            async with sessions() as session:
                await mark_session_for_turn(
                    session, turn_id=turn_id, session_id=event.session_id
                )
                await session.commit()
        elif isinstance(event, AgentUserEntry):
            seat = (
                event.agent_handle
                or (state.acting_agent if state is not None else None)
                or live.room_session_agents.get(topic_id)
            )
            await _bind_user_entry(
                sessions,
                live,
                work_runner,
                topic_id=topic_id,
                seat=seat,
                event=event,
            )
        elif isinstance(event, AgentMessage):
            payload = await self.messages.persist_assistant_message(
                project_id=project_id,
                topic_id=room_id,
                text=event.text,
                turn_id=turn_id,
                reply_to=(
                    state.reply_to
                    if state is not None and state.assistant_count == 0
                    else None
                ),
                roster=state.roster if state is not None else None,
                topic_refs=state.topic_refs if state is not None else [],
                eid=eid or event.eid,
                eids=event.eids,
                platform_unsolicited=platform_unsolicited,
                continuation_id=(state.continuation_id if state is not None else None),
                at=event.at,
                author=event.agent_handle
                or (state.acting_agent if state is not None else None),
                inner_id=inner_id,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
        elif isinstance(event, AgentToolUse):
            name = _short_tool_name(event.name)
            args = event.input or {}
            payload = await _persist_tool_event(
                sessions,
                live,
                project_id=project_id,
                topic_id=room_id,
                name=name,
                tool_input=args,
                platform=_is_platform_tool(event.name, args),
                turn_id=turn_id,
                eid=eid or event.eid,
                platform_unsolicited=platform_unsolicited,
                inner_id=inner_id,
                author=event.agent_handle,
                at=event.at,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
                if state is not None and event.call_id:
                    state.steps[event.call_id] = uuid.UUID(payload["id"])
            resource = _TOOL_ACTION.get(name)
            if state is not None and resource is not None:
                await announce_action(sessions, state, resource)
        elif isinstance(event, AgentStepFailed):
            # It changes a line that is already on the timeline rather than
            # adding one, so it goes out as that line, restated. A step whose
            # call we never saw (a restart mid-turn) is simply not marked — the
            # timeline is still true, just less helpful.
            block_id = state.steps.get(event.call_id) if state is not None else None
            if block_id is not None:
                payload = await _mark_step_failed(sessions, block_id, event.text)
                if payload is not None:
                    frame = {"type": "block_updated", "block": without_output(payload)}
        elif isinstance(event, AgentStepOutput):
            # Written onto the step, like a failure. The frame says only that
            # the step now has output: the text is read when somebody opens it.
            block_id = state.steps.get(event.call_id) if state is not None else None
            if block_id is not None:
                payload = await _record_step_output(sessions, block_id, event.text)
                if payload is not None:
                    frame = {"type": "block_updated", "block": without_output(payload)}
        elif isinstance(event, AgentCompacting):
            if event.done:
                await _note_compaction(sessions, live, turn_id, event, channel=channel)
            else:
                if turn_id not in live.compact_notes:
                    # The backend this one replaced may have landed the line while
                    # this compaction (or an earlier attempt nobody heard end) was
                    # under way. It is the same news: say it on that line.
                    running = await _running_compactions(sessions, turn_id)
                    if running:
                        live.compact_notes[turn_id] = running[-1]
                content, meta = _compaction_notice(event)
                await _keep_note(
                    sessions,
                    live.compact_notes,
                    room_id,
                    turn_id,
                    content,
                    meta,
                    author=state.acting_agent if state is not None else None,
                    inner_id=inner_id,
                    channel=channel,
                )
        elif isinstance(event, AgentRetrying):
            await _note_retry(
                sessions,
                live,
                room_id,
                turn_id,
                event,
                author=state.acting_agent if state is not None else None,
                inner_id=inner_id,
                channel=channel,
            )
        elif isinstance(event, AgentToolResult):
            payload = await _persist_subagent_result(
                sessions,
                live,
                project_id=project_id,
                topic_id=room_id,
                event=event,
                turn_id=turn_id,
                eid=eid,
                platform_unsolicited=platform_unsolicited,
                inner_id=inner_id,
            )
            if payload is not None:
                frame = {"type": "event_block", "block": payload}
        elif (
            isinstance(event, AgentResult)
            and event.taken_into is None
            and not event.late
        ):
            error_line, error_code = "", None
            if event.session_id:
                await self.save_session(
                    topic_id,
                    event.session_id,
                    agent_handle=event.agent_handle,
                    harness=event.harness,
                )
            if event.is_error:
                if event.text.strip() == TURN_TIMEOUT_MESSAGE:
                    # The watchdog's own verdict, and the only failure whose
                    # session may still be alive — everything downstream of it
                    # is different, so it keeps its own card. Recognised by the
                    # message because that is what the watchdog emits; nothing
                    # overrides it today.
                    # The constant rather than `event.text`: the same words,
                    # and the constant is the one that carries its key.
                    line, meta = (
                        TURN_TIMEOUT_MESSAGE,
                        notice(
                            EVENT_TURN_TIMEOUT,
                            severity=SEVERITY_WARN,
                            who=WHO_HUMAN,
                            detail=say("turnTimeoutDetail"),
                            detail_label=say("labelDetails"),
                        ),
                    )
                elif await self.credits_refused(turn_id):
                    # Admission already told the room WHY this turn is ending
                    # (#715): it refused every `/v1/messages` call for spent
                    # credits, and Claude Code's own reading of that refusal
                    # — "Invalid API key" — is wrong advice for a spent
                    # balance. Repeat the platform's own line, with the reason
                    # the credits give now, rather than Claude Code's text.

                    policy = await self.policy(topic_id)
                    line = credits_event((policy or {}).get("credits_exhausted"))
                    meta = CREDITS_EXHAUSTED_META
                else:
                    line, meta = _turn_failure_notice(
                        event.text, event.failure_code, log=event.log
                    )
                error_line, error_code = line, meta.get("code")
                payload = await post_system_event(
                    sessions, room_id, line, turn_id, meta=meta, inner_id=inner_id
                )
                if payload is not None:
                    frame = {"type": "event_block", "block": payload}
                    # In a 支线 nobody has replied to yet, this line is the only
                    # thing in it: the main line reads its 支线 again, so the
                    # message it hangs under says the reply failed.
                    if inner_id is not None:
                        await self.thread_replied(inner_id)
            elif event.text.strip():
                # Terminal output, the final response included, stays in
                # activity: a reply reaches the room only through chat_send,
                # in a private chat exactly as in any other room.
                payload = await self.messages.persist_assistant_message(
                    project_id=project_id,
                    topic_id=room_id,
                    text=event.text,
                    turn_id=turn_id,
                    reply_to=state.reply_to if state is not None else None,
                    roster=state.roster if state is not None else None,
                    topic_refs=state.topic_refs if state is not None else [],
                    author=event.agent_handle
                    or (state.acting_agent if state is not None else None),
                    eid=eid,
                    platform_unsolicited=platform_unsolicited,
                    continuation_id=(
                        state.continuation_id if state is not None else None
                    ),
                    closing=result_text_seen,
                    inner_id=inner_id,
                )
                if payload is not None:
                    if state is not None:
                        state.assistant_count += 1
                    frame = {"type": "event_block", "block": payload}
        await execute_event(
            sessions,
            live,
            work_runner,
            project_id=project_id,
            topic_id=topic_id,
            turn_id=turn_id,
            event=event,
            state=state,
            landed=LandedEvent(frame, error_line, error_code),
            close_interval=self.close_interval,
            finish_work=self.completion.close,
            forget_claims=self.completion.forget_claims,
            inside_inputs=inputs_answered_inside,
            replay_event=self.accept,
            activity=self.activity,
        )
