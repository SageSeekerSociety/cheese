"""Resolve a native event and land its durable effects before turn execution.

Dirty room/seat/message preparation stays here. The execution owner receives
only the landed frame, exact work context and named completion/scheduling effects.
Neither layer receives ChatService or a many-method private Host protocol.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import alerting
from app.core.sentences import say
from app.domain.agent import turn_resume
from app.domain.agent.credits_notice import spent_reason
from app.domain.agent.gateway_usage import OWN_ROUTE
from app.domain.agent.models import TurnResume
from app.domain.agent.platform_failures import (
    TURN_TIMEOUT_MESSAGE,
    is_provider_out_of_credit,
)
from app.domain.agent.prompt import _compaction_notice
from app.domain.agent.room_events import post_system_event
from app.domain.agent.run_records import record_now
from app.domain.agent.service import (
    AgentCompacting,
    AgentEvent,
    AgentResult,
    AgentRetrying,
    AgentSessionInfo,
    AgentToolUse,
    AgentUserEntry,
    proves_output,
)
from app.domain.agent.session_turn_events import SessionTurnEvents
from app.domain.agent.turn.intake.assistant import AssistantMessages
from app.domain.agent.turn.intake.completion import TurnCompletion
from app.domain.agent.turn.intake.events import (
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
from app.domain.agent.turn.store.intervals import turn_credits_refused
from app.domain.agent.turn_inputs import mark_session_for_turn
from app.domain.block.models import Block
from app.domain.delivery.receipts import inputs_answered_inside
from app.domain.notice_meta import (
    EVENT_TURN_TIMEOUT,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.session_record.preview import _short_tool_name
from app.domain.usage.credits import (
    CREDITS_EXHAUSTED_META,
    credits_event,
    refused_for_credits,
)

logger = logging.getLogger(__name__)


async def _turn_ended_noticed(
    sessions: async_sessionmaker, turn_id: uuid.UUID, meta: dict
) -> bool:
    """Whether this turn's ending already has its line of this kind."""
    async with sessions() as session:
        return (
            await session.scalar(
                select(Block.id)
                .where(
                    Block.turn_id == turn_id,
                    Block.meta["event_type"].as_string() == meta.get("event_type"),
                )
                .limit(1)
            )
            is not None
        )


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
        except Exception:  # a failed read must not break the notice
            logger.exception("could not read credits-refused stamp for %s", turn_id)
            return False

    async def _resume_later(
        self,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        turn_id: uuid.UUID,
        result: AgentResult,
        *,
        seat: str | None,
        route: str | None,
    ) -> TurnResume | None:
        """What the platform decided for this failure: a resume owed, one it
        declined (``dropped_reason`` says why), or None when it never applied."""
        try:
            return await turn_resume.after_failure(
                self.sessions,
                project_id=project_id,
                conversation_id=conversation_id,
                seat=seat,
                failed_turn_id=turn_id,
                result=result,
                route=route,
            )
        except Exception:
            logger.exception(
                "could not schedule a resume after turn %s failed", turn_id
            )
            return None

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
        recovering = False
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
        elif isinstance(event, AgentToolUse):
            name = _short_tool_name(event.name)
            resource = _TOOL_ACTION.get(name)
            if state is not None and resource is not None:
                await announce_action(sessions, state, resource)
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
                # Only a failure the service reported: the watchdog's timeout
                # and a spent balance are not waits (`turn_resume`).
                resumable = False
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
                elif (braked := refused_for_credits(event.text)) or (
                    await self.credits_refused(turn_id)
                ):
                    # Admission already told the room WHY this turn is ending
                    # (#715): it refused every `/v1/messages` call for spent
                    # credits, and Claude Code's own reading of that refusal
                    # — "Invalid API key" — is wrong advice for a spent
                    # balance. Repeat the platform's own line, with the reason
                    # the credits give now, rather than Claude Code's text.
                    # The gateway's budget brake stops a turn admission let
                    # through, with no stamp; its refusal in the turn's own
                    # failure is the same news, and a retry cannot succeed.

                    policy = await self.policy(topic_id)
                    reason = (policy or {}).get("credits_exhausted")
                    if reason is None and braked:
                        # The brake fires before the turn's spend is settled,
                        # so admission does not know yet; the ledger says
                        # what ran out and when it comes back.
                        reason = await spent_reason(sessions, project_id)
                    line = credits_event(reason)
                    meta = CREDITS_EXHAUSTED_META
                else:
                    line, meta = _turn_failure_notice(
                        event.text,
                        event.failure_code,
                        log=event.log,
                        own=state is not None and state.route == OWN_ROUTE,
                    )
                    resumable = True
                    if is_provider_out_of_credit(event.text):
                        # 上游服务的余额用完了：项目里的人补团队额度也没有用，只有
                        # 平台管理员能处理。房间里那句已经说了，这里再叫一次管平台
                        # 的人（`alerting` 自带重复窗口，同一个上游不会每小时刷）。
                        alerting.send(
                            "AI 中继余额已用完",
                            [
                                f"项目 {project_id}",
                                "上游原文：" + (event.text or "").strip()[:400],
                                "重试没有作用；补团队额度也没有用。",
                            ],
                            key="ai-relay-out-of-credit",
                            when=time.time(),
                        )
                if resumable:
                    resume = await self._resume_later(
                        project_id,
                        topic_id,
                        turn_id,
                        event,
                        # Only the seat that failed: the room's last speaker
                        # is a guess, and with several seats a guess wakes the
                        # wrong agent. Not resuming is the safer miss.
                        seat=event.agent_handle
                        or (state.acting_agent if state is not None else None),
                        route=state.route if state is not None else None,
                    )
                    if resume is not None and resume.dropped_at is None:
                        # The room hears that the turn stopped and when the
                        # platform tries again; no error frame, nobody needs
                        # to step in (`execute_event`).
                        recovering = True
                        line, meta = turn_resume.scheduled_notice(resume)
                    elif (
                        resume is not None
                        and resume.dropped_reason == turn_resume.RECOVERS_LATE
                    ):
                        line, meta = turn_resume.late_notice(resume)
                error_line, error_code = line, meta.get("code")
                # Every failed turn says so in its conversation, once: a report
                # delivered again finds the line it already left.
                if not await _turn_ended_noticed(sessions, turn_id, meta):
                    room_meta = dict(meta)
                    if "error" in room_meta:
                        # Startup diagnostics are platform records. The room
                        # gets its notice; full logs belong to the trace view.
                        await record_now(
                            sessions,
                            conversation_id=conversation_id,
                            content=line,
                            meta=meta,
                            turn_id=turn_id,
                            seat=event.agent_handle
                            or (state.acting_agent if state is not None else None),
                        )
                        room_meta.pop("error")
                        room_meta.pop("failed", None)
                    payload = await post_system_event(
                        sessions,
                        room_id,
                        line,
                        turn_id,
                        meta=room_meta,
                        inner_id=inner_id,
                    )
                    if payload is not None:
                        frame = {"type": "event_block", "block": payload}
                        if inner_id is not None:
                            await self.thread_replied(inner_id)
        await execute_event(
            sessions,
            live,
            work_runner,
            project_id=project_id,
            topic_id=topic_id,
            turn_id=turn_id,
            event=event,
            state=state,
            landed=LandedEvent(frame, error_line, error_code, recovering),
            close_interval=self.close_interval,
            finish_work=self.completion.close,
            forget_claims=self.completion.forget_claims,
            inside_inputs=inputs_answered_inside,
            replay_event=self.accept,
            activity=self.activity,
        )
