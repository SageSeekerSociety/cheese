"""轮次挂在订阅上的那一条线：一条事件进来，该落什么、该说什么，当场做完。

``ChatService._consume_hook_event`` 回答的是同一个问题：**屏幕订阅送来一条事件，
这一轮现在要做什么**。它是一道 ``isinstance`` 的长梯子 —— 会话信息、分身起停、
助手的输出、工具调用、步骤的判决、整理、重试、子代理的结论，最后是整轮结束 ——
每一格都当场落块、当场广播，不等整轮结束才攒成一条。

搬出来时按原样搬 —— 入参出参就是它们与调用方之间全部的约定，行为一格没动。形状
变化只有两类，都是「没有 `self` 可用了」：

- ``self._sessions`` 与五张现场状态的表（``_hook_work``、``_retry_notes``、
  ``_waiting_notes``、``_compact_notes``、``_room_session_agents``、
  ``_active_turn_ids``）→ 同名入参：一个 sessionmaker 与那几个 dict，逐字不变；
- 留在 ``ChatService`` 上、这条路回头要问的那些事（开一轮、落一条助手消息、关
  一轮的书、谁在做哪条活……）→ ``service``，见 ``_HookStream``：它是这条路的
  收件人，本模块只声明自己会问什么，pyright 在调用点核对 ``ChatService`` 答不答
  得上来。

现场状态 ``_HookWorkState`` 也跟着搬来了：``_close_hook_work`` 与
``_announce_action`` 收的就是它，``chat.py`` 重新导出这个名字，那两处的签名一格
没动。``_TOOL_ACTION``、``_turn_failure_notice``、``_is_out_of_credit`` 只被这条
线用到，一并搬走，``chat.py`` 仍然导得出来。
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.sentences import NoticeText, error_frame, say
from app.domain.agent import attachments, turn_inputs
from app.domain.agent.event_lines import _is_platform_tool, _short_tool_name
from app.domain.agent.nonce import nonce_in
from app.domain.agent.platform_failures import (
    SESSION_START_CODES,
    TURN_TIMEOUT_MESSAGE,
    classify_platform_failure,
)
from app.domain.agent.platform_notices import (
    EVENT_API_RETRY,
    EVENT_CONTEXT_COMPACT,
    EVENT_DEVICE_WAITING,
    EVENT_TURN_FAILED,
    EVENT_TURN_TIMEOUT,
    SEVERITY_ERROR,
    SEVERITY_WARN,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent.prompt import _compaction_notice
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.room_events import (
    _mark_step_failed,
    _persist_subagent_result,
    _persist_tool_event,
    _record_step_output,
    post_system_event,
)
from app.domain.agent.run_records import record_now, restate_now
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
from app.domain.agent.step_output import without_output
from app.domain.agent.turn_inputs import bind, mark_session_for_turn, transition
from app.domain.delivery.receipts import inputs_answered_inside
from app.domain.memory.models import MemoryScope
from app.domain.room_task.place import PlaceResolver
from app.domain.run_record import service as run_records

logger = logging.getLogger(__name__)

#: 现场状态查表的键：一间房 + 这一轮。
TurnKey = tuple[uuid.UUID, uuid.UUID]


@dataclass
class _HookWorkState:
    """Persistence context for work whose events arrive on a subscription."""

    project_id: uuid.UUID
    topic_id: uuid.UUID
    work_id: uuid.UUID
    pending_ids: set[uuid.UUID]
    reply_to: uuid.UUID | None
    # None where no prompt was assembled to read one — `_persist_assistant_message`
    # then loads it, which is NOT the same as passing []: [] means 私聊 (no member
    # list at all), and conflating the two flags every @ as a non-member.
    roster: list[dict] | None
    topic_refs: list[dict]
    continuation_id: uuid.UUID | None
    route: str
    acting_agent: str
    # Attribution and memory part ways here, deliberately: `acting_agent` is
    # the seat of the agent that ran this turn (who did it), while the pool
    # belongs to that agent across rooms (whose memory it is). Resolved at turn
    # start and carried, because the hook path reaches turn end with no session
    # left open to ask.
    agent_pool: tuple[MemoryScope, str] | None
    user_text: str
    started_at: datetime
    agent_instance_handle: str | None = None
    # The model this turn's session was launched on, for the usage row a turn
    # with no reported usage still writes. "" where this process never
    # assembled a turn for the session (a screen recovered on the way up).
    model: str = ""
    assistant_count: int = 0
    last_chat_at: datetime | None = None
    # When this turn was last told it had gone quiet — NOT whether it has been.
    # A flag meant one reminder per silent stretch, so a turn that worked for
    # three hours without publishing was asked once, at the ten-minute mark, and
    # then left alone for the remaining two hours and fifty minutes. The room
    # showing nothing for that long is the complaint this reminder exists for.
    last_progress_reminder_at: datetime | None = None
    #: 这一轮每次工具调用落在哪个现场块上，按 harness 自己的调用 id。结果回来时要
    #: 写上输出、挂了要标红的就是那一块。只在内存里、只活这一轮：重启丢掉的只是几
    #: 截输出和几个红点，不是记录。
    steps: dict[str, uuid.UUID] = field(default_factory=dict)

    # The topic branch's commits as of turn start — what makes "this turn's
    # changes" answerable at turn end. A task rather than a value, because the
    # read shells out to git and creates the repo on first use; see where it is
    # started. `None` (or a read that failed) means the turn lands NO change
    # summary rather than a wrong one: with no baseline, every commit looks new.
    known_commits: asyncio.Task[set[str] | None] | None = None
    # Did the SESSION open this work rather than the platform? Then its
    # bookkeeping has no coroutine to fall out of, and turn end is the only
    # place the marks it left in the runner can be dropped.
    self_started: bool = False


class _WorkRunner(Protocol):
    """轮次运行器里本模块用到的面（``runtime.AgentWorkRunner``）。

    「这条会话又出活了」和「会话自己开的那一轮，把它在运行器里留的记号收掉」是
    这条路要说的两句话。类型写在这里而不是 import ``runtime``：那个实例由
    ``app/api/deps.py`` 建出来，本模块够不着也不该够着 ``app.api`` —— 它从调用
    方手上拿（``_compute`` 那两处也是这么绑的）。
    """

    def note_session_output(self, turn_id: uuid.UUID, *, tool: bool) -> None: ...

    def close_turn_the_session_started(self, turn_id: uuid.UUID) -> None: ...


class _HookStream(Protocol):
    """这条路的收件人：``ChatService`` 上留在原地的那一族方法。

    ``_consume_hook_event`` 一条事件走完，要开一轮（屏幕上自己跑起来的那种）、要
    落助手消息、要记会话指针、要署名、要关这一轮的书 —— 那些方法都还在
    ``ChatService`` 上（它们各自还有别的调用方）。本模块声明自己会问哪些，类型
    在调用点核对；这里只列签名，不写实现。
    """

    #: 一间房最后由哪个坐席开的会话 —— 事件自己没带坐席时的退路。
    _room_session_agents: dict[uuid.UUID, str]

    async def _begin_self_started_turn(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        opened: bool = False,
        agent_handle: str | None = None,
        session_id: str | None = None,
    ) -> _HookWorkState | None: ...

    async def _room_of_conversation(
        self, conversation_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID | None]: ...

    async def _save_session_pointer(
        self,
        topic_id: uuid.UUID,
        session_id: str,
        *,
        agent_handle: str | None = None,
        harness: str | None = None,
    ) -> None: ...

    async def _persist_assistant_message(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        text: str,
        turn_id: uuid.UUID | None,
        reply_to: uuid.UUID | None,
        roster: list[dict] | None,
        topic_refs: list[dict],
        eid: str | None = None,
        eids: tuple[str, ...] = (),
        platform_unsolicited: bool = False,
        continuation_id: uuid.UUID | None = None,
        at: datetime | None = None,
        inner_id: uuid.UUID | None = None,
        publish: bool = False,
        author: str | None = None,
        publication_id: str | None = None,
        own_output: bool = False,
        extra_meta: dict | None = None,
        closing: bool = False,
    ) -> dict | None: ...

    async def _announce_action(self, state: _HookWorkState, resource: str) -> None: ...

    async def _turn_credits_refused(self, turn_id: uuid.UUID) -> bool: ...

    async def work_policy(self, topic_id: uuid.UUID) -> dict | None: ...

    async def thread_replied(self, conversation_id: uuid.UUID) -> None: ...

    async def _close_open_turns(
        self, topic_id: uuid.UUID, turn_id: uuid.UUID
    ) -> None: ...

    async def _close_hook_work(
        self, state: _HookWorkState, result: AgentResult
    ) -> list[dict]: ...

    async def _forget_room_claims(self, topic_id: uuid.UUID) -> None: ...

    async def _set_hook_activity(
        self,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        work_id: uuid.UUID,
        active: bool,
        *,
        agent_handle: str | None = None,
    ) -> None: ...


# What an exhausted relay balance looks like coming back from newapi. It arrives
# as HTTP 429, the same status as a rate limit, but the two need opposite advice:
# a rate limit clears on its own, a spent balance never does.
_OUT_OF_CREDIT_MARKERS = (
    "余额不足",
    "请充值",
    "insufficient balance",
    "insufficient_quota",
    "quota exceeded",
    "billing",
)


def _is_out_of_credit(detail: str | None) -> bool:
    if not detail:
        return False
    lowered = detail.lower()
    return any(m.lower() in lowered for m in _OUT_OF_CREDIT_MARKERS)


# A platform tool → the action card 芝士 files for it when it calls it
# (`_ACTION_LABEL`, `_announce_action`). Only the card: telling the room a panel
# went stale is the job of the API handler that changed it (`announce_stale`),
# which knows the change happened whoever called it. Keyed by the tool's short name
# (`mcp__native__cheese_task` → `cheese_task`).
_TOOL_ACTION = {
    "cheese_task": "topics",
    "cheese_notify": "notify",
}


def _turn_failure_notice(
    text: str, code: str | None, *, log: str | None = None
) -> tuple[str, dict]:
    """A failed turn's room line and the structured card behind it.

    Every failure gets one, classified or not. 平台提示统一契约: the room line
    is ONE line and the service's own words go in `meta.detail` — pasting them
    into the line is what made a plain system row run to seven or eight, and
    only a classified failure used to get meta at all, so the three most common
    ones (座位限流 / 余额用尽 / HTTP 错误) carried no structure whatsoever.

    An unclassified failure says only that the turn did not finish, with a
    retry: the service's own words are often English and say nothing to the
    people in the room. They are kept whole, one click away, under a label that
    says they are the service's words — not dropped: the reason buried behind
    a misleading label sent a whole room hunting a mystery bug twice in one
    night (2026-08-16, topic ee17b136 — the real text was the delivery timeout
    all along). The platform's own one-line sentences (a runner that stopped
    answering) are already written for people and stay on the line.
    """
    failure = classify_platform_failure(text, code=code)
    if failure is not None:
        if failure.code in SESSION_START_CODES and text.strip():
            # The sentence it was raised with can name what was found.
            line = text if isinstance(text, NoticeText) else text.strip()
            return line, _with_log(failure.meta, log)
        return failure.content, _with_log(failure.meta, log)
    detail = (text or "").strip()
    if _is_out_of_credit(detail):
        # A spent balance is not a wait — no amount of retrying refills it, and
        # telling someone to try again later sends them into a loop that cannot
        # succeed. Say what actually has to happen.
        line = say("turnFailedOutOfCredit")
        hint = say("turnFailedOutOfCreditHint")
        retryable = False
    else:
        first = detail.splitlines()[0].strip() if detail else ""
        line = (
            # The platform's own one-line sentence: nested whole, so it keeps
            # its key.
            say("turnFailedWith", reason=text)
            if isinstance(text, NoticeText) and first == text
            else say("turnFailedService")
        )
        hint = say("turnFailedRetryLater")
        retryable = True
    return line, notice(
        EVENT_TURN_FAILED,
        severity=SEVERITY_ERROR,
        who=WHO_HUMAN,
        # 原话是唯一的一份——它没有第二个副本可以「去别处看」，所以原样收进
        # detail，不截、不摘要。
        detail=(say("hintAndServiceWords", hint=hint, said=detail) if detail else hint),
        detail_label=say("labelDetails"),
        retryable=retryable,
    )


def _with_log(meta: dict, log: str | None) -> dict:
    """What the failing process printed, on the fields only 现场 shows: a
    failed row there, with the text as its error. The room reads neither."""
    if not log or not log.strip():
        return meta
    return {**meta, "failed": True, "error": log.strip()}


async def _drop_takeover_marks(
    sessions,
    hook_work,
    work_runner: "_WorkRunner",
    topic_id: uuid.UUID,
    seat: str,
    generation: uuid.UUID,
) -> None:
    """Drop the memory side of exactly the predecessor this seat's owner
    record names as retired (FB-56 P2-2).

    The target comes from the durable owner record — the takeover wrote it
    there in the same transaction as the crown — never from scanning the
    topic: another seat's self-started work, an earlier generation's, and
    anything whose interval is still open is left alone, and a caller
    cancelled between commit and cleanup is finished by the next replay,
    because the closed row plus the recorded id is the whole predicate.
    """
    from sqlalchemy import select

    from app.domain.agent.models import AgentTurn
    from app.domain.agent.turn_inputs import AgentSeatOwner

    async with sessions() as session:
        owner = await session.get(AgentSeatOwner, (topic_id, seat, generation))
        retired = owner.retired_turn_id if owner is not None else None
        if retired is None:
            return
        state = hook_work.get((topic_id, retired))
        if state is None or not state.self_started:
            return
        closed = await session.scalar(
            select(AgentTurn.stopped_at).where(AgentTurn.id == retired)
        )
    if closed is None:
        return
    if hook_work.pop((topic_id, retired), None) is not None:
        work_runner.close_turn_the_session_started(retired)


async def _bind_user_entry(
    sessions,
    hook_work,
    work_runner: "_WorkRunner",
    *,
    topic_id: uuid.UUID,
    seat: str | None,
    event: AgentUserEntry,
) -> None:
    """Bind one native user entry to its platform input, and walk the owner.

    The entry is the one durable proof that an input was consumed (FB-56):
    its marker names the input in the platform's own ledger, the input names
    the work, and the owner walk retires exactly the predecessor's interval
    — never a page, never a position.

    The active-source check and the mutation share one transaction: the
    seat's pointer is read in the same session that binds and walks, so a
    reader that looked before the pointer moved — a new session, a new
    generation, or a detach — cannot write through (P2-1). A refusal moves
    nothing: ledger, owner, head, retirements and marks are all untouched.

    The memory side of a takeover is dropped only after the database side
    is durable, and re-derived from the durable rows rather than from the
    outcome in memory (P2-2): a commit that fails rolls the database back
    and leaves the memory in place; a caller cancelled between commit and
    cleanup is finished by the next replay, because the closed row is the
    predicate.
    """
    nonce = nonce_in(event.text)
    if nonce is None or seat is None or not event.generation:
        return
    generation = uuid.UUID(event.generation)
    # The whole mutation holds the seat's attachment lock: a detach/attach
    # can only land whole-before or whole-after it, and an event draining
    # from a superseded subscription is refused at the lock — including when
    # the seat has NO live subscription at all (`current` is None), where
    # anything arriving is by definition not from the active source (FB-56).
    seat_tuple = (topic_id, seat)
    async with attachments.lock(seat_tuple):
        if (
            event.attachment is not None
            and attachments.current(seat_tuple) != event.attachment
        ):
            return
        async with sessions() as session:
            if event.session_id and event.harness:
                # The seat's pointer row is LOCKED for the mutation's whole
                # transaction: a detach or a new session's attachment takes the
                # same row lock on its write side, so a pointer that moves has
                # either already moved (we see it and refuse) or waits for us
                # (FB-56 P2-1 — a read alone, even in this transaction, would
                # race the next write under READ COMMITTED).
                from sqlalchemy import select

                from app.domain.agent_session.models import AgentSession

                current = await session.scalar(
                    select(AgentSession.resume_token)
                    .where(
                        AgentSession.conversation_id == topic_id,
                        AgentSession.agent_handle == seat,
                        AgentSession.harness == event.harness,
                    )
                    .with_for_update()
                )
                if current is not None and current != event.session_id:
                    return
            row = await bind(
                session,
                nonce=nonce,
                entry_id=event.entry_id,
                pos=event.pos,
                generation=generation,
                session_id=event.session_id,
                at=datetime.now(UTC),
            )
            if row is not None:
                # The native entry IS consumption proof — acceptance evidence
                # a caller cancelling after the transport took the write
                # cannot take away (FB-56 P1): the parent interval and the
                # input are stamped delivered here, monotone, in the bind's
                # own transaction. An early Stop then has a delivered row to
                # close; nothing native is declared ended by it.
                await turn_inputs.stamp_delivered(
                    session, turn_id=row.turn_id, at=datetime.now(UTC)
                )
                await transition(
                    session,
                    topic_id=topic_id,
                    agent_handle=seat,
                    session_id=event.session_id or row.session_id,
                    generation=generation,
                    work_id=row.turn_id,
                    input_id=row.id,
                    entry_id=event.entry_id,
                    pos=event.pos,
                    at=datetime.now(UTC),
                )
            await session.commit()
    await _drop_takeover_marks(
        sessions, hook_work, work_runner, topic_id, seat, generation
    )


async def _consume_hook_event(
    service: _HookStream,
    sessions: async_sessionmaker,
    hook_work: dict[TurnKey, _HookWorkState],
    retry_notes: dict[uuid.UUID, uuid.UUID],
    waiting_notes: dict[uuid.UUID, uuid.UUID],
    compact_notes: dict[uuid.UUID, uuid.UUID],
    room_session_agents: dict[uuid.UUID, str],
    active_turn_ids: dict[uuid.UUID, set[uuid.UUID]],
    work_runner: _WorkRunner,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    event: AgentEvent,
    eid: str | None,
    result_text_seen: bool,
    platform_unsolicited: bool,
) -> None:
    """Persist and broadcast one event from a live screen subscription."""
    from app.domain.agent.runtime import get_broker

    broker = get_broker()
    frame: dict | None = None
    state = hook_work.get((topic_id, turn_id))
    if state is None and platform_unsolicited and proves_output([event]):
        # Nobody fed this session anything and it is producing output anyway
        # — one of its workers finished and the completion notice woke it.
        # That is a whole turn, and it gets a turn's bookkeeping from here:
        # an interval a sweep can find, and the context its Stop needs to
        # close the books. Opened on OUTPUT rather than on the first hook of
        # any kind, because only output guarantees the Stop that closes it.
        state = await service._begin_self_started_turn(
            project_id,
            topic_id,
            turn_id,
            # The event's own seat first (stamped by the subscription that
            # read it): with several seats live in one room, the
            # room-keyed fallback below answers "who spoke LAST", not
            # "whose session this output came from".
            agent_handle=getattr(event, "agent_handle", None)
            or room_session_agents.get(topic_id),
            session_id=getattr(event, "session_id", None),
        )
    # `topic_id` names the session's conversation: a room's, or a task's own.
    # What it says lands in the room's table, beside its task, and goes out on
    # the conversation's channel — the one its view subscribes to.
    conversation_id = topic_id
    room_id, inner_id = await service._room_of_conversation(conversation_id)
    channel = str(conversation_id)
    if not isinstance(event, AgentRetrying):
        # Anything else the turn does ends a streak of retries: the request
        # went through. The next retry is news of its own.
        retry_notes.pop(turn_id, None)
    if isinstance(event, AgentResult):
        waiting_notes.pop(turn_id, None)
        # The turn ended with the compaction still open (it was stopped, or
        # the session died): the line must not go on saying it is compacting.
        # Asked of the room, not only of this process: the line may have been
        # landed by the backend this one replaced.
        await _note_compaction(
            sessions,
            compact_notes,
            turn_id,
            AgentCompacting(done=True, error=say("contextCompactSessionEnded")),
            channel=channel,
        )
    if isinstance(event, AgentSessionInfo):
        if event.agent_handle:
            room_session_agents[topic_id] = event.agent_handle
        await service._save_session_pointer(
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
            or room_session_agents.get(topic_id)
        )
        await _bind_user_entry(
            sessions,
            hook_work,
            work_runner,
            topic_id=topic_id,
            seat=seat,
            event=event,
        )
    elif isinstance(event, AgentMessage):
        payload = await service._persist_assistant_message(
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
            hook_work,
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
            await service._announce_action(state, resource)
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
            await _note_compaction(
                sessions, compact_notes, turn_id, event, channel=channel
            )
        else:
            if turn_id not in compact_notes:
                # The backend this one replaced may have landed the line while
                # this compaction (or an earlier attempt nobody heard end) was
                # under way. It is the same news: say it on that line.
                running = await _running_compactions(sessions, turn_id)
                if running:
                    compact_notes[turn_id] = running[-1]
            content, meta = _compaction_notice(event)
            await _keep_note(
                sessions,
                compact_notes,
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
            retry_notes,
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
            hook_work,
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
    elif isinstance(event, AgentResult) and event.taken_into is None and not event.late:
        error_line, error_code = "", None
        if event.session_id:
            await service._save_session_pointer(
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
            elif await service._turn_credits_refused(turn_id):
                # Admission already told the room WHY this turn is ending
                # (#715): it refused every `/v1/messages` call for spent
                # credits, and Claude Code's own reading of that refusal
                # — "Invalid API key" — is wrong advice for a spent
                # balance. Repeat the platform's own line, with the reason
                # the credits give now, rather than Claude Code's text.
                from app.domain.usage.credits import (
                    CREDITS_EXHAUSTED_META,
                    credits_event,
                )

                policy = await service.work_policy(topic_id)
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
                    await service.thread_replied(inner_id)
        elif event.text.strip():
            # Terminal output, the final response included, stays in
            # activity: a reply reaches the room only through chat_send,
            # in a private chat exactly as in any other room.
            payload = await service._persist_assistant_message(
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
                continuation_id=(state.continuation_id if state is not None else None),
                closing=result_text_seen,
                inner_id=inner_id,
            )
            if payload is not None:
                if state is not None:
                    state.assistant_count += 1
                frame = {"type": "event_block", "block": payload}
    if frame is not None:
        await broker.publish(channel, frame)
        if frame["type"] in ("assistant_block", "event_block"):
            work_runner.note_session_output(
                turn_id, tool=isinstance(event, AgentToolUse)
            )
    if isinstance(event, AgentResult):
        # 投喂 → Stop is the interval. Closing it HERE, rather than where the
        # turn's own coroutine ends, is what lets a turn survive the backend
        # being replaced under it: the screen kept working, the subscription
        # reattached, and its Stop closes the books exactly as it would have
        # if nothing had happened. A turn whose coroutine is alive closes the
        # same row a moment later and finds it already closed, which is the
        # correct answer either way.
        await close_on_stop(service, topic_id, turn_id)
        if state is not None:
            if event.taken_into is not None:
                # Its commits are the turn's that read it, which reports them.
                state.known_commits = None
            try:
                for close_frame in await service._close_hook_work(state, event):
                    await broker.publish(str(topic_id), close_frame)
            except Exception:
                logger.exception(
                    "hook work close failed (topic=%s, work=%s)",
                    topic_id,
                    turn_id,
                )
                # Keep this result replayable. Neither the input's durable hold
                # nor the completion context may be discarded before commit.
                raise
            else:
                hook_work.pop((topic_id, turn_id), None)
                if state.self_started:
                    # No coroutine owns this one, so there is no `finally`
                    # anywhere else to drop the marks it left in the runner.
                    work_runner.close_turn_the_session_started(turn_id)
        elif event.is_error and event.taken_into is None:
            await service._forget_room_claims(topic_id)
        if event.taken_into is not None:
            # The room was told when the turn that read it ended.
            return
        await _end_inputs_answered_inside(
            service,
            sessions,
            hook_work,
            retry_notes,
            waiting_notes,
            compact_notes,
            room_session_agents,
            active_turn_ids,
            work_runner,
            project_id,
            topic_id,
            turn_id,
            event,
        )
        if event.is_error and not event.late:
            frame_out = error_frame(
                error_line or event.text, type="error", persisted=True
            )
            if error_code:
                frame_out["code"] = error_code
            await broker.publish(str(topic_id), frame_out)
        await broker.publish(str(topic_id), {"type": "done"})


async def _end_inputs_answered_inside(
    service: _HookStream,
    sessions: async_sessionmaker,
    hook_work: dict[TurnKey, _HookWorkState],
    retry_notes: dict[uuid.UUID, uuid.UUID],
    waiting_notes: dict[uuid.UUID, uuid.UUID],
    compact_notes: dict[uuid.UUID, uuid.UUID],
    room_session_agents: dict[uuid.UUID, str],
    active_turn_ids: dict[uuid.UUID, set[uuid.UUID]],
    work_runner: _WorkRunner,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    result: AgentResult,
) -> None:
    """End the turns whose input the session read inside ``turn_id``.

    A message sent while the session is mid-turn is read at that turn's next
    tool boundary and answered inside it. Nothing the session writes names its
    own turn again, so it ends here, the way ``turn_id`` ended: consumed, or
    left to be re-sent if it failed, each as its own Stop would have closed it.
    Which inputs those were is the delivery ledger's, written when the session
    echoed them: dev replaces its backend on every merge, and the one that
    hears this turn end is often not the one that saw them read.

    One is still to end if its interval is open, or if this process still
    counts it as running. The two come apart: the call that sent it returns as
    soon as the session takes it over and closes its interval then, while the
    room's running marks for it stay until something ends it.
    """
    async with sessions() as session:
        read_inside = await inputs_answered_inside(session, topic_id, turn_id)
        open_turns = set(
            await AgentTurnRepository(session).still_open(topic_id, read_inside)
        )
    running = active_turn_ids.get(topic_id, set())
    taken = [
        work
        for work in read_inside
        if work in open_turns or work in running or (topic_id, work) in hook_work
    ]
    for input_turn in taken:
        await _consume_hook_event(
            service,
            sessions,
            hook_work,
            retry_notes,
            waiting_notes,
            compact_notes,
            room_session_agents,
            active_turn_ids,
            work_runner,
            project_id,
            topic_id,
            input_turn,
            AgentResult(
                text="",
                session_id=result.session_id,
                is_error=result.is_error,
                failure_code=result.failure_code,
                agent_handle=result.agent_handle,
                harness=result.harness,
                taken_into=turn_id,
            ),
            None,
            False,
            False,
        )
        await service._set_hook_activity(
            project_id, topic_id, input_turn, False, agent_handle=result.agent_handle
        )


async def close_on_stop(service, topic_id: uuid.UUID, turn_id: uuid.UUID) -> None:
    """A Stop ends the interval it names, and with it what may hold back a
    message queued for the same seat. The Stop can land after the turn's
    completion and its idle frame, whose own nudges then found the interval
    still open, so the queue is looked at once more here."""
    from app.domain.agent.pending_messages import nudge_messages

    await service._close_open_turns(topic_id, turn_id)
    nudge_messages(service, topic_id)


async def _note_retry(
    sessions: async_sessionmaker,
    retry_notes: dict[uuid.UUID, uuid.UUID],
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    event: AgentRetrying,
    *,
    author: str | None,
    inner_id: uuid.UUID | None,
    channel: str,
) -> None:
    """Say the turn is retrying a failed request, on one line per streak.

    The first retry of a streak lands a notice; every later one restates
    that same line with the new count, so ten retries read as one line that
    says 10 rather than ten lines. Not session output (`note_session_output`
    is not told): nothing the session produced crossed here."""
    count = (
        f"{event.attempt}/{event.max_attempts}"
        if event.attempt and event.max_attempts
        else str(event.attempt or "")
    )
    content = say("apiRetryAttempt", attempt=count) if count else say("apiRetry")
    # Claude Code files a failure it cannot classify as `unknown`, and with no
    # HTTP status that means the request got no response at all. Shown as-is,
    # "unknown" tells a reader nothing; say what it means instead.
    if event.status is None and event.error in ("", "unknown"):
        detail = (
            say("apiRetryNoResponseWaited", seconds=round(event.no_response_ms / 1000))
            if event.no_response_ms
            else say("apiRetryNoResponse")
        )
        label = say("labelDetails")
    else:
        said = " ".join(
            part
            for part in (
                event.error,
                f"HTTP {event.status}" if event.status is not None else "",
            )
            if part
        )
        detail = said or None
        label = say("labelServiceWords") if said else None
    meta = {
        **notice(
            EVENT_API_RETRY,
            severity=SEVERITY_WARN,
            who=WHO_PLATFORM,
            detail=detail,
            detail_label=label,
        ),
        "attempt": event.attempt,
        "max_attempts": event.max_attempts,
        "delay_ms": event.delay_ms,
        "at": datetime.now(UTC).isoformat(),
    }
    await _keep_note(
        sessions,
        retry_notes,
        topic_id,
        turn_id,
        content,
        meta,
        author=author,
        inner_id=inner_id,
        channel=channel,
    )


async def _note_compaction(
    sessions: async_sessionmaker,
    compact_notes: dict[uuid.UUID, uuid.UUID],
    turn_id: uuid.UUID,
    event: AgentCompacting,
    *,
    channel: str,
) -> None:
    """Restate the turn's compaction line as over, if it has one.

    Which line that is comes from the room, not from this process's memory:
    dev replaces its backend on every merge, and a compaction that started
    under one backend ends under the next, which never saw the line land."""
    remembered = compact_notes.pop(turn_id, None)
    lines = await _running_compactions(sessions, turn_id)
    if remembered is not None and remembered not in lines:
        lines.append(remembered)
    content, meta = _compaction_notice(event)
    for block_id in lines:
        await _restate_note(sessions, block_id, content, meta, channel)


async def _running_compactions(
    sessions: async_sessionmaker, turn_id: uuid.UUID
) -> list[uuid.UUID]:
    """The turn's compaction records that still say it is compacting."""
    try:
        async with sessions() as session:
            return await run_records.running(session, turn_id, EVENT_CONTEXT_COMPACT)
    except Exception:  # noqa: BLE001 — a status line is not worth a turn
        logger.exception("could not read compaction records of turn %s", turn_id)
        return []


async def _note_reachability(
    sessions: async_sessionmaker,
    hook_work: dict[TurnKey, _HookWorkState],
    waiting_notes: dict[uuid.UUID, uuid.UUID],
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    work_id: uuid.UUID,
    reachable: bool,
    reason: str,
) -> None:
    """Say the turn is waiting for its machine, and later that the wait is
    over — the same line both times."""
    del project_id
    if reachable:
        block_id = waiting_notes.pop(work_id, None)
        if block_id is None:
            return
        await _restate_note(
            sessions,
            block_id,
            say("deviceReconnected"),
            {"state": "over", "at": datetime.now(UTC).isoformat()},
            str(topic_id),
        )
        return
    state = hook_work.get((topic_id, work_id))
    meta = {
        **notice(
            EVENT_DEVICE_WAITING,
            severity=SEVERITY_WARN,
            who=WHO_PLATFORM,
            detail=reason or None,
            detail_label=say("labelReason") if reason else None,
        ),
        "state": "waiting",
        "at": datetime.now(UTC).isoformat(),
    }
    # The line lands in the conversation the turn runs in: a task's or a
    # 支线's own, under its room.
    async with sessions() as session:
        place = await PlaceResolver(session).conversation(topic_id)
    await _keep_note(
        sessions,
        waiting_notes,
        place.room_id if place is not None else topic_id,
        work_id,
        say("deviceWaiting"),
        meta,
        author=state.acting_agent if state is not None else None,
        inner_id=place.inner_id if place is not None else None,
        channel=str(topic_id),
    )


async def _keep_note(
    sessions: async_sessionmaker,
    notes: dict[uuid.UUID, uuid.UUID],
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    content: str,
    meta: dict,
    *,
    author: str | None,
    inner_id: uuid.UUID | None,
    channel: str,
) -> None:
    """Keep the turn's record of this kind, or restate the one it has. A run
    record, not a line in the conversation: the 现场 and the status above the
    composer read it."""
    record_id = notes.get(turn_id)
    if record_id is not None:
        await _restate_note(sessions, record_id, content, meta, channel)
        return
    payload = await record_now(
        sessions,
        conversation_id=inner_id or topic_id,
        content=content,
        meta=meta,
        turn_id=turn_id,
        # The agent whose turn this is: the record is about its work, and the
        # 现场 files it under whoever did the work.
        seat=author,
        channel=channel,
    )
    if payload is not None:
        notes[turn_id] = uuid.UUID(payload["id"])


async def _restate_note(
    sessions: async_sessionmaker,
    record_id: uuid.UUID,
    content: str,
    meta: dict,
    channel: str,
) -> None:
    await restate_now(
        sessions, record_id, content=content, meta=meta, channel=channel or None
    )
