"""Send one prepared turn, with explicit transport and lifecycle effects."""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.sentences import exception_text, say
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.prompt import SessionOpening, opening_changes
from app.domain.agent.nonce import new_nonce
from app.domain.agent.platform_notices import (
    EVENT_PROMPT_REPLAYED,
    EVENT_TURN_QUEUED,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_PLATFORM,
    delivery_checking_notice,
    notice,
)
from app.domain.agent.service import AgentEvent, AgentResult
from app.domain.agent.turn.state.live import HookWorkState, LiveWork
from app.domain.agent.turn.steps.prepared import PreparedSend
from app.domain.agent.turn.store.intervals import note_turn_context
from app.domain.agent.turn.store.session_inputs import (
    DeliveryStamp,
    commit_delivery,
    remember_opening,
)
from app.domain.delivery.input_identity import (
    InputEffects,
    InputOutcomeUnconfirmed,
    InputRegistrar,
)

logger = logging.getLogger(__name__)
_SESSION_ROUTES_KEPT = 512


class SystemEvent(Protocol):
    async def __call__(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        *,
        meta: dict | None = None,
    ) -> dict | None: ...


class HookEventConsumer(Protocol):
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


class InputRegistration(Protocol):
    def __call__(
        self,
        effects: InputEffects,
        *,
        probe_unread: bool = False,
        fence_delivery: bool = False,
    ) -> InputRegistrar: ...


class IntervalOpening(Protocol):
    async def __call__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        /,
        *,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        author: str,
        content: str,
        is_resume: bool,
        continuation_id: uuid.UUID | None,
        harness: str,
        nonce: str,
        at: datetime,
    ) -> None: ...


class FailedRetirement(Protocol):
    async def __call__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        /,
        *,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        at: datetime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SendEffects:
    post_system_event: SystemEvent
    consume_hook_event: HookEventConsumer
    known_commits: Callable[[uuid.UUID, uuid.UUID], Awaitable[set[str] | None]]
    register_input: InputRegistration
    open_interval: IntervalOpening
    retire_failed: FailedRetirement
    stamp_delivery: DeliveryStamp


async def send_prepared(
    sessions: async_sessionmaker[AsyncSession],
    live: LiveWork,
    effects: SendEffects,
    prepared: PreparedSend,
    *,
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    author: str,
    content: str,
    user_block_id: uuid.UUID | None,
    is_resume: bool,
    platform_turn: bool,
    continuation_id: uuid.UUID | None,
    delivery_id: uuid.UUID | None,
    model_kwargs: dict,
    route: str,
    prompt_text: str,
    system_prompt: str,
    opening: SessionOpening,
    told: dict[str, str] | None,
) -> AsyncIterator[dict]:
    acting_agent = prepared.acting_agent
    agent_pool = prepared.agent_pool
    project_id = prepared.project_id
    runtime = prepared.sender
    roster = prepared.roster
    topic_refs = prepared.topic_refs
    consumed_ids = prepared.consumed_ids
    replay_notice = prepared.replay_notice
    resume_session_id = prepared.resume_session_id
    needs_place = prepared.needs_place
    turn_images = prepared.turn_images
    live.session_route.pop(topic_id, None)
    live.session_route[topic_id] = route
    while len(live.session_route) > _SESSION_ROUTES_KEPT:
        del live.session_route[next(iter(live.session_route))]
    live.session_model.pop(topic_id, None)
    live.session_model[topic_id] = model_kwargs["model"]
    while len(live.session_model) > _SESSION_ROUTES_KEPT:
        del live.session_model[next(iter(live.session_model))]

    # Internal: the screen subscription, not this request, owns timeout and
    # thinking lifecycle. Runtime consumes this frame and disables its
    # request-scoped lifecycle before provider setup begins.
    yield {"type": "session_lifecycle"}

    # 重放可见 (#416): say out loud that this turn is re-sending a batch that
    # earlier turns already failed on. Posted BEFORE the stream, because the
    # whole point is that this turn may produce nothing either — a notice
    # written afterwards is exactly the one that never gets written.
    if replay_notice is not None:
        await effects.post_system_event(
            topic_id,
            replay_notice,
            turn_id,
            meta=notice(
                EVENT_PROMPT_REPLAYED,
                severity=SEVERITY_WARN,
                who=WHO_PLATFORM,
            ),
        )

    # Baseline for 「这一轮改了哪些文件」, started BEFORE 芝士 can write anything
    # but deliberately NOT awaited here: git_log ensures the repo exists, and
    # on a cold project that is a git init plus a base commit. Awaited in
    # front of the provider, that delay is charged to the start of every
    # turn, and a turn cancelled inside the window dies before it can store
    # its session id. What it measures only becomes commits at the
    # checkpoint, so finishing the read any time before turn end is soon
    # enough. Both backends need it, and the hooks backend returns from this
    # function long before its turn ends — so it is started once here and
    # carried on the work state rather than read twice in two places.
    known_commits = asyncio.ensure_future(
        effects.known_commits(project_id, prepared.room_id)
    )
    marked_work_ids: list[uuid.UUID] = []

    def _register_work(marked_work_id: uuid.UUID) -> None:
        marked_work_ids.append(marked_work_id)
        key = (topic_id, marked_work_id)
        # A self-started predecessor's state and runner marks end where the
        # takeover is PROVEN — the bound native user entry's transition
        # (hook_stream's AgentUserEntry branch) — not here: registering a
        # send proves nothing about the session, and this loop used to
        # scan the whole topic for it (FB-56).
        state = live.hook_work.get(key)
        if state is None:
            live.hook_work[key] = HookWorkState(
                project_id=project_id,
                topic_id=topic_id,
                work_id=marked_work_id,
                pending_ids=set(consumed_ids),
                reply_to=user_block_id,
                roster=roster,
                topic_refs=topic_refs,
                continuation_id=continuation_id,
                route=route,
                model=model_kwargs["model"],
                acting_agent=acting_agent,
                agent_pool=agent_pool,
                user_text=prompt_text,
                started_at=datetime.now(UTC),
                agent_instance_handle=prepared.agent_handle,
                known_commits=known_commits,
            )
            return
        state.pending_ids.update(consumed_ids)
        if state.reply_to is None:
            state.reply_to = user_block_id
        if prompt_text not in state.user_text:
            state.user_text = f"{state.user_text}\n{prompt_text}"

    # 这一轮的提示词写下去之前先登记：会话说「收下了」的时候，记号落在召唤它
    # 的那条人类消息上。登记在 send 之前，因为回执可能比 send 返回还快。
    # 同一个条件也是「这一轮欠人一句回话」：召唤它的是人，会话就得先在房间里
    # 回一句，再做别的（`driven/runner.py`）。
    # FB-56: the interval and ledger open after every fallible preparation
    # (a failure there leaves no interval); the nonce binds the entry back.
    nonce = new_nonce()
    prompt_text = f"{prompt_text}\n{nonce}"
    await effects.open_interval(
        sessions,
        topic_id=topic_id,
        turn_id=turn_id,
        author=author,
        content=content,
        is_resume=is_resume,
        continuation_id=continuation_id,
        harness=prepared.harness,
        nonce=nonce,
        at=datetime.now(UTC),
    )
    # From the interval opening (``started_at``) to the input stamped
    # delivered: where the platform's own time goes on every turn.
    delivery_ms: dict[str, float] = {}
    delivery_mark = time.monotonic()

    def _delivery_step(name: str) -> None:
        nonlocal delivery_mark
        now = time.monotonic()
        delivery_ms[name] = round((now - delivery_mark) * 1000, 1)
        delivery_mark = now

    # And on the turn itself: the backend that ends this turn may not be
    # this one (`_begin_self_started_turn`), and it remembers neither.
    await note_turn_context(
        sessions,
        turn_id,
        route=route,
        reply_to=user_block_id,
        agent_handle=prepared.agent_handle,
    )
    _delivery_step("note_context")
    summoned = user_block_id is not None and not is_resume and not platform_turn
    input_effects = InputEffects(
        held_block_ids=tuple(consumed_ids),
        # Initial prompt consumption remains tied to the clean turn ending;
        # native echo settles the delivery and its summoning read marker.
        seen_block_ids=(user_block_id,)
        if user_block_id is not None and summoned
        else (),
        seen_by=acting_agent if summoned else None,
        delivery_id=delivery_id,
        attempt_id=turn_id if delivery_id is not None else None,
    )
    try:
        # The same key `_assemble_turn` read this turn's resume token under
        # — where this conversation runs is recorded under it too, and a ref
        # built from anything else resolves somebody else's machine.
        session_ref = SessionRef(
            project_id,
            prepared.room_id,
            prepared.agent_handle,
            harness=prepared.harness,
            inner_id=prepared.inner_id,
        )
        await prepared.activate(session_ref)
        _delivery_step("activate")
        ready = await runtime.send(
            session_ref,
            prompt_text,
            system_prompt=system_prompt,
            resume_token=resume_session_id,
            session_opening=opening.text,
            opening_changes=opening_changes(opening, told),
            model=model_kwargs.get("model"),
            env=model_kwargs.get("env"),
            acting=acting_agent,
            needs_place=needs_place,
            keeps_nothing=prepared.keeps_nothing,
            work_id=turn_id,
            images=turn_images or None,
            on_mark=_register_work,
            register_input=effects.register_input(
                input_effects,
                fence_delivery=delivery_id is not None,
            ),
            owes_reply=summoned,
        )
        _delivery_step("send")
        # 这一轮把现状说到了：下一轮只补在这之后变了的。
        await remember_opening(
            sessions,
            topic_id=topic_id,
            agent_handle=prepared.agent_handle,
            harness=prepared.harness,
            digests=opening.digests(),
        )
        _delivery_step("remember_told")
    except InputOutcomeUnconfirmed as exc:
        # The session still owns this work. Its structured echo can settle
        # the committed identity even after this ChatService is replaced.
        logger.exception(
            "initial input requires reconciliation (topic=%s, input=%s)",
            topic_id,
            exc.identity.input_id,
        )
        checking, checking_meta = delivery_checking_notice()
        await effects.post_system_event(topic_id, checking, turn_id, meta=checking_meta)
        return
    except Exception as exc:  # noqa: BLE001 — a failed write must be SAID
        # Nothing else will close this turn. `session_lifecycle` above told
        # the runner that the session owns the ending, and the session this
        # was going to be never started — so without this the room shows
        # 正在思考 until the orphan sweep hours later, with no error, which
        # is indistinguishable from an agent thinking hard.
        #
        # Reported as the same error result a dead session's watchdog
        # produces, through the same consumer: the turn row closes, the
        # room gets its message, and the pending batch stays unconsumed so
        # the next turn re-sends it.
        logger.exception("session write failed (topic=%s, turn=%s)", topic_id, turn_id)
        failure_code = getattr(exc, "failure_code", None)
        await effects.consume_hook_event(
            project_id,
            topic_id,
            turn_id,
            AgentResult(
                text=exception_text(exc) or "本轮没能把消息送进机器上的会话",
                session_id=resume_session_id,
                is_error=True,
                failure_code=failure_code,
                log=getattr(exc, "log", None),
            ),
            None,
            False,
            False,
        )
        # The write never reached the transport, so the Stop consumer's
        # close_one rightly refuses this interval (undelivered). Its own
        # coroutine retires it HERE, by exact id — the same end the
        # runner's `_execute` gives a runner-driven turn (FB-56).
        await effects.retire_failed(
            sessions,
            topic_id=topic_id,
            turn_id=turn_id,
            at=datetime.now(UTC),
        )
        return
    # Internal frame: `send` returned, so the transport accepted
    # the write — which IS delivery (#563, per #487's contract that a
    # write either reaches the process or errors). The runtime records
    # that as a fact against the durable in-flight registry. Without it
    # the orphan sweep has to infer arrival after a restart, from
    # whether 芝士 happened to produce a block before the process died,
    # and so calls a prompt that landed two seconds earlier undelivered
    # and re-sends it. Nothing but the runtime acts on this, so it never
    # reaches the broker.
    # Delivery is recorded AT the source (FB-56): the transport accepted
    # the write, so the interval and its input are stamped delivered in
    # the same commit — a converse driven without the work runner leaves
    # the same fact a runner-driven one does. Monotone, so the runner's
    # own stamp on the frame below is a no-op second write.
    await commit_delivery(sessions, stamp=effects.stamp_delivery, turn_id=turn_id)
    _delivery_step("stamp_delivered")
    logger.info(
        "chat_delivery_timing topic=%s turn=%s elapsed_ms=%.1f phases_ms=%s",
        topic_id,
        turn_id,
        sum(delivery_ms.values()),
        delivery_ms,
    )
    yield {"type": "prompt_delivered"}
    if ready is False:
        marked_work_id = marked_work_ids[-1] if marked_work_ids else turn_id
        await effects.post_system_event(
            topic_id,
            say("sessionStartingMessageQueued"),
            marked_work_id,
            meta=notice(EVENT_TURN_QUEUED, severity=SEVERITY_INFO, who=WHO_PLATFORM),
        )
    return
