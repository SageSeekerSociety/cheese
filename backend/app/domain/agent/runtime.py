"""AgentWorkRunner: receive messages and run agent work as background jobs.

WebSocket connections subscribe and relay; they never own model work. A
disconnect drops only the subscriber while the background request or live
session continues and persists independently.

Today's Broker is in-process (single backend instance). Multi-instance needs a
cross-process broker (Valkey/PG) + a durable per-topic lease + a per-turn replay
buffer/cursor for seamless mid-turn reconnect — see design v2 R1/R3. The current
single-writer guarantee is ChatService's per-topic asyncio lock (process-local);
the durable lease is the documented multi-instance upgrade.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime

from sqlalchemy import select

from app.core.errors import AppError
from app.core.redis import get_redis_client
from app.core.sentences import listing, say
from app.domain.addressing import Addressed, addressed_to_agent
from app.domain.agent import dispatch_log, runtime_orphans
from app.domain.agent.admission import (
    queued,
)
from app.domain.agent.host_failure import handle_host_failure, record_host_success
from app.domain.agent.pending_messages import nudge_messages
from app.domain.agent.platform_failures import (
    PlatformFailure,
)
from app.domain.agent.platform_notices import tools_recovered_notice
from app.domain.agent.realtime import broker as realtime
from app.domain.agent.realtime import subscriber_queue

# Re-exported for `app.domain.delivery.agent`: its seam may not import the
# repository module itself (it would close a C3 domain cycle).
from app.domain.agent.repositories import AgentTurnRepository, TurnRecord  # noqa: F401
from app.domain.agent.turn.intake.messages import RunnerIntake
from app.domain.agent.turn.intake.status import WorkStatus
from app.domain.agent.turn.state.execution import ExecutionPolicy, ExecutionState
from app.domain.agent.turn.steps.execute import ExecutionEffects, execute
from app.domain.agent.turn.store.human import HumanHandoff
from app.domain.agent.turn.store.intervals import open_turn
from app.domain.agent.turn_ledger import (
    fire_on_done,
    project_pool,
    stamp_delivery,
    utcnow,
)
from app.domain.delivery.agent import dispatch_pending, run_attempt
from app.domain.delivery.mention import mentioned_handles
from app.domain.delivery.models import Delivery
from app.domain.identity.arrival import Arrival, how_it_arrives
from app.domain.identity.handles import names_a_person
from app.domain.notice_meta import (
    EVENT_DELIVERY_FALLBACK,
    EVENT_DEPLOY_INTERRUPTED,
    EVENT_DISPATCH_UNKNOWN,
    EVENT_TURN_QUEUED,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)

logger = logging.getLogger("cheesex.runtime")


def _a_turn_was_addressed(addressed: Addressed) -> bool:
    """这条事件点到的人里，有没有谁是**靠一轮**收到它的。

    这是「这一轮跑不跑」现在唯一的答案（不变量 I13）。以前它是每个调用点自己传进来
    的一个布尔：浏览器算一个 `summon` 发上来，平台自己的那些路径干脆写死 `True`。
    两者都是「点名的人同时替被点名的人决定他要动」，而点名和到达是两件事 ——
    `address()` 说谁被点到，`how_it_arrives()` 说他怎么收到，一轮只是 agent 那一档
    的物理形态。平台因此发不起一轮：它可以点谁的名，点到人就是一条站内信。
    """
    return any(how_it_arrives(r.handle) is Arrival.turn for r in addressed.recipients)


# What the room is told while a message waits for its turn. Neither is the
# turn's outcome, so neither means the turn happened.
_WAITING = frozenset({EVENT_TURN_QUEUED, EVENT_DELIVERY_FALLBACK})


class AgentWorkRunner:
    """Admit background work and publish its frames to the broker.

    Interactive session lifecycle is owned by its provider subscription. This
    runner owns only request admission, non-interactive request execution, and
    recovery bookkeeping. It is process-scoped so work survives subscribers.
    """

    def __init__(
        self,
        broker: realtime.InProcessBroker,
        *,
        turn_timeout_s: float = 900.0,
        first_output_timeout_s: float = 300.0,
        credential_expiry_of: Callable[[uuid.UUID], int | None] | None = None,
        credential_expired_fuse_s: float = 15.0,
        host_has_room: Callable[[uuid.UUID], Awaitable[bool]] | None = None,
    ) -> None:
        self._broker = broker
        self._host_has_room = host_has_room
        self._timeout = turn_timeout_s
        # 冷启动看门狗: how long a turn may produce NOTHING before it is called
        # dead. Separate from `turn_timeout_s` because it answers a different
        # question — that one asks "is this turn taking too long?", this one asks
        # "did this turn ever start?". 0 disables it. See `_execute`.
        self._first_output_timeout_s = first_output_timeout_s
        # #388 缺陷一: a lookup for "the model credential this topic's turn will run
        # with is already expired". When it answers yes, the backend KNOWS the turn
        # is doomed (the metering proxy / upstream rejects every request), so the
        # cold-start fuse is cut to `credential_expired_fuse_s` — a short grace that
        # still lets a credential refreshed between setup and now speak first (which
        # retires the fuse) — and the failure event says the TRUE reason instead of
        # guessing container/disk/network. `None` (the default, and every backend
        # with no such signal) leaves the fuse byte-for-byte unchanged.
        self._credential_expiry_of = credential_expiry_of
        self._credential_expired_fuse_s = credential_expired_fuse_s
        # Keep references so tasks aren't GC'd mid-flight (and for shutdown).
        self._tasks: set[asyncio.Task] = set()
        # 可 debug: lifecycle summaries of the last ~100 turns (/debug/turns).
        self._recent: deque[dict] = deque(maxlen=100)
        # Turn ids THIS process is actually executing right now → the task
        # running them. The durable registry on disk cannot answer that question
        # — it records every turn that ever started and was not cleaned up,
        # whether by this generation of the process or a dead one.
        #
        # It holds the TASK, not just the id, because "not running it" is only
        # half the orphan set: a turn can also be in here and wedged (the child
        # container died, the stream never ends). Claiming one of those means
        # cancelling it — a resume would otherwise queue behind the zombie on
        # ChatService's per-topic lock and never run. See sweep_orphans.
        self._live: dict[str, asyncio.Task] = {}
        # Monotonic timestamp of the last frame each live turn published. Frames
        # include tool calls, which persist no Block — so this sees activity the
        # DB cannot, and keeps a long tool-only stretch from looking dead.
        self._last_frame_at: dict[str, float] = {}
        # Which topic each live turn belongs to. Kept in memory rather than read
        # back off the on-disk registry because `live_work_for_topic` answers a
        # request (`/topics/{id}/status`), and that must not cost a file read.
        self._live_topics: dict[str, uuid.UUID] = {}
        # Topics whose last turn died of a host-scoped failure (#186). Clearing the
        # machine's failure streak costs a DB round-trip, and a turn must not wait
        # on bookkeeping to be released — `_live` is emptied only after `_execute`
        # returns, and `live_work_for_topic` is the heartbeat half of the stall
        # verdict, so a slow tail here reads as "still running" to every caller.
        # Remembering who actually failed keeps the happy path free of it entirely;
        # what this set cannot see (a failure recorded before a restart) is covered
        # by the staleness rule in `device.health` instead.
        self._host_failed_topics: set[str] = set()
        # Live turns whose prompt the session has accepted. The rest are still
        # being prepared or sent, and a process handing its work over waits for
        # them: a turn that got through is one the next process can pick up where
        # it stands, a turn cut off mid-send is one it can only send again.
        self._delivered: set[str] = set()
        # Whether this process may start turns right now. Only the owner of the
        # running work may (`app.core.ownership`): a turn is started against what
        # this process knows is already running in the room, and a process that
        # does not own that work does not know it. A turn asked for meanwhile is
        # not refused — it waits here until the process takes over, or is left
        # to its owner when this one is on its way out.
        self._may_start = True
        # Whether this process is the one listening to sessions. Set from the
        # moment it holds the owner lock, a little before turns may start (the
        # takeover itself listens), and cleared the moment it starts to leave.
        self._owns_sessions = True
        execution_state = ExecutionState(
            self._recent,
            self._live,
            self._last_frame_at,
            self._live_topics,
            self._delivered,
            self._host_failed_topics,
        )
        self._status = WorkStatus(broker, execution_state)
        self._intake = RunnerIntake(
            broker,
            execution_state,
            wait_to_start=self._wait_to_start,
            schedule_message=lambda service, topic, turn, **message: (
                self._receive_message(service, topic, turn, **message)
            ),
            execute=self._execute,
            nudge=self._nudge_after_work,
            host_has_room=host_has_room,
            address_agent=addressed_to_agent,
            is_addressed=_a_turn_was_addressed,
            resolve_initial_seat=self._resolve_initial_seat,
        )

    @property
    def accepting_turns(self) -> bool:
        """Is this process the one running the work right now — the owner, and
        not on its way out?"""
        return self._may_start

    @property
    def owns_sessions(self) -> bool:
        return self._owns_sessions

    def own_sessions(self, owns: bool) -> None:
        self._owns_sessions = owns

    def hold_turns(self) -> None:
        """Let no turn start in this process until `start_turns`."""
        self._may_start = False

    def start_turns(self) -> None:
        self._may_start = True

    async def _wait_to_start(self) -> None:
        while not self._may_start:
            await asyncio.sleep(0.2)

    async def settle_deliveries(self, timeout_s: float) -> set[str]:
        """Wait for every live turn's prompt to reach its session, up to
        `timeout_s`. Returns the turns still not delivered when it gave up."""
        deadline = time.monotonic() + timeout_s
        while True:
            waiting = set(self._live) - self._delivered
            if not waiting or time.monotonic() >= deadline:
                return waiting
            await asyncio.sleep(0.2)

    async def let_go(self, timeout_s: float = 5.0) -> None:
        """Stop every piece of work this process still has in hand, for a
        process on its way out.

        Stopping a turn here stops only this process waiting on it: the agent in
        the execution environment keeps going, and the turn's interval stays
        open for the next process to pick up. What must not happen is this
        process finishing a send after the lock is gone, when the next one may
        already have judged that turn undelivered and sent it again. A message
        still waiting to be admitted is left unanswered in its room, where the
        next process finds it.
        """
        pending = [task for task in self._tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=timeout_s)

    def recent_work(self) -> list[dict]:
        """Newest-first lifecycle summaries for /debug/turns."""
        return list(reversed(self._recent))

    def active_work_count(self) -> int:
        """How many turns are currently in flight — /health exposes this so a
        redeploy can drain (wait for running turns) instead of killing them."""
        return max(len(self._tasks), self._broker.active_count())

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Wait for every task this runner still has in flight — a turn, the
        tasks it starts as it ends — instead of guessing how long the tail takes.

        A turn's own coroutine keeps running after it has published its last
        frame (settling conclusion cards, closing the interval; see the tail of
        `_execute`), so a test that only waits for that frame and then returns
        races it: the per-test event loop closes under the still-running task,
        which freezes it mid-transaction holding a DB lock the next test's
        TRUNCATE then waits on. Awaiting this instead is what a test ends on.

        Best-effort: on timeout this just stops waiting rather than raising —
        it is on the caller to decide what that means (a test's own teardown
        check is what turns a task still pending here into a named failure).
        """
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout_s):
                while pending := {t for t in self._tasks if not t.done()}:
                    await asyncio.wait(pending)

    def topic_work(self, topic_id: uuid.UUID) -> dict | None:
        return self._status.topic_work(topic_id, self._timeout)

    def continuation_for(self, topic_id: uuid.UUID) -> uuid.UUID | None:
        """The logical unit of work this topic's CURRENT turn belongs to, or
        None when no turn of ours is running.

        This is how an HTTP handler — which is called by the sandbox over a
        plain request and knows nothing about turns — finds the key namespace to
        dedup against. None means "not inside an automatic turn": a human
        clicking a button twice means it twice, so the caller skips the check
        rather than inventing a namespace."""
        rec = self._status.current_turn_record(topic_id)
        raw = rec.get("continuation_id") if rec is not None else None
        return uuid.UUID(raw) if isinstance(raw, str) else None

    def turn_author_for(self, topic_id: uuid.UUID) -> str | None:
        """The HUMAN whose turn is running on this topic right now — who is
        actually driving the work — or None when nobody identifiable is.

        The answer a sandbox-side action cannot supply for itself: 芝士 opens a
        piece of work under her own agent handle, so the endpoint sees the robot
        and not the person who asked. That person is right here in the turn
        record, next to the continuation id the split endpoint already reads.

        None covers three cases the caller must treat identically — fall back to
        whatever it did before: no turn of ours is running; the turn was started
        by the platform itself (`author="system"` — gate verdicts, conflict
        nudges); or this room's agent started the turn itself, its `author`
        being an agent handle (`open_turn_the_session_started` — a worker's
        completion notice wakes the session and it runs a whole turn off that,
        which is the likeliest turn to open work of its own). Only a real
        person's handle comes back."""
        rec = self._status.current_turn_record(topic_id)
        author = rec.get("author") if rec is not None else None
        if not isinstance(author, str) or not names_a_person(author):
            return None
        return author

    def note_session_output(self, turn_id: uuid.UUID, *, tool: bool) -> None:
        """A live session produced something for this turn.

        The turn summary (`/debug/turns`) is stamped from frames crossing the
        request's own stream, and a session's output does not cross it — the
        call that started the turn returned before the agent said anything. So
        every turn read back as `first_output_s: null` / `tools: 0`, which is
        the exact signature of a sandbox whose hooks never arrive: the one
        failure the summary exists to make visible was indistinguishable from
        every healthy turn.
        """
        for rec in reversed(self._recent):
            if rec.get("turn_id") != str(turn_id):
                continue
            if tool:
                rec["tools"] = int(rec.get("tools") or 0) + 1
            if rec.get("first_output_s") is None and rec.get("started_at"):
                rec["first_output_s"] = round(time.time() - rec["started_at"], 2)
            self._last_frame_at[str(turn_id)] = time.monotonic()
            return

    def live_work_for_topic(self, topic_id: uuid.UUID) -> dict | None:
        return self._status.live_work_for_topic(topic_id)

    async def project_queue_depth(self, project_id: uuid.UUID | str) -> int:
        """Turns currently waiting for one of this project's slots."""
        return await queued(get_redis_client(), project_pool(project_id, 0))

    def submit(
        self,
        chat_service,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        addressed: Addressed,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        turn_id: uuid.UUID | None = None,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> uuid.UUID:
        """Start a turn in the background; return its turn_id immediately. Turns
        on the same topic serialize on ChatService's per-topic lock (so a second
        submit queues behind the first).

        ``addressed`` 是这条事件点到了谁（`addressing.py`）。**没有一个入参
        说「跑一轮」**：一轮是 agent 那一档收件人的到达形态，所以调用点能做的只有点
        名，跑不跑由 `_a_turn_was_addressed` 从寻址结果读出来。一个人都没点到，或者
        点到的全是人，这里就什么也不起 —— 平台因此没有一条起轮次的路径（I12）。

        ``nudge_event`` is the ONE LINE the room sees for a platform-initiated
        turn; ``nudge_meta`` is that event's structured payload (see
        `notice_meta.notice`), which is where the long text goes — the CI
        log, the check output, the provider's own words. ``content`` stays the
        agent's prompt either way, so what 芝士 receives never changes when this
        pair does.

        ``continuation_id`` names the logical unit of work. A fresh turn starts
        one (defaulting to its own turn id); a re-send INHERITS the interrupted
        turn's, which is what lets a side effect the first attempt already
        performed be recognised as done — see domain.idempotency.keys.

        ``on_done`` fires when this turn's task finishes, whatever the outcome.
        It exists for callers that COALESCE work onto a running turn and
        therefore need the moment the topic is free
        again; it is not an error channel and never sees the result. It runs on
        the event loop as a done-callback, so it must not block and must not
        raise — an exception there would only reach the loop's handler.

        ``turn_id`` 可以由调用点先分配：有些调用点要在这一轮还没开始之前，就把它的
        id 写进自己那条记录里（环境修复把它写进事故 meta，之后靠 `turn_pending` 判断
        这一轮是不是还在排队）。不传就当场分配一个。"""
        turn_id = turn_id or uuid.uuid4()
        work = self._intake._run(
            chat_service,
            topic_id,
            turn_id,
            author=author,
            content=content,
            addressed=addressed,
            reply_to=reply_to,
            attachments=attachments,
            is_resume=is_resume,
            resume_reason=resume_reason,
            nudge_event=nudge_event,
            nudge_meta=nudge_meta,
            continuation_id=continuation_id or turn_id,
            delivery_id=delivery_id,
            recipient_instance_id=recipient_instance_id,
        )
        if delivery_id is not None:
            work = run_attempt(chat_service.session_factory, delivery_id, turn_id, work)
        task = asyncio.create_task(
            work,
            name=f"turn:{turn_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if on_done is not None:
            task.add_done_callback(lambda _task: fire_on_done(on_done))
        return turn_id

    async def receive_message(
        self,
        chat_service,
        topic_id: uuid.UUID,
        *,
        author: str,
        content: str,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        client_id: str | None = None,
        quoted_context: dict | None = None,
        may_summon: bool = True,
        handoff: HumanHandoff | None = None,
        by_assistant: bool = False,
    ) -> uuid.UUID:
        return await self._intake.receive_message(
            chat_service,
            topic_id,
            author=author,
            content=content,
            reply_to=reply_to,
            attachments=attachments,
            client_id=client_id,
            quoted_context=quoted_context,
            may_summon=may_summon,
            handoff=handoff,
            by_assistant=by_assistant,
        )

    def _receive_message(self, chat_service, topic_id, turn_id, **message) -> None:
        # Named like `submit`'s, so `turn_pending` sees a message still waiting
        # to be admitted as the pending turn it is.
        task = asyncio.create_task(
            self._intake._consume_message(chat_service, topic_id, turn_id, **message),
            name=f"turn:{turn_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        if len(mentioned_handles(message.get("content") or "")) > 1:
            # 点到的第二位起是账本里的投递（`delivery/mention.py`），已经随消息一起
            # 提交了。现在就派，不等定时扫描那 30 秒：同一条消息点到的几位该一起醒。

            dispatch = asyncio.create_task(
                dispatch_pending(
                    chat_service.session_factory, chat=chat_service, runner=self
                ),
                name=f"mentions:{turn_id}",
            )
            self._tasks.add(dispatch)
            dispatch.add_done_callback(self._tasks.discard)

    def turn_pending(self, turn_id: uuid.UUID) -> bool:
        """Include admission queueing before the durable turn interval opens."""
        return any(
            not task.done() and task.get_name() == f"turn:{turn_id}"
            for task in self._tasks
        )

    async def open_turn_the_session_started(
        self,
        chat_service,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        agent_handle: str,
        session_id: str | None = None,
    ) -> None:
        """Register an interval for work the SESSION started on its own.

        A session works without being asked whenever one of its workers finishes:
        the completion notice wakes it and it runs a whole turn off that. Nothing
        was fed to it, but it is still a turn — and without a row here it is the
        one kind this table cannot see, so no sweep can ever find it wedged, the
        usage it burns has no interval to hang off, and a room it hangs in shows
        「正在思考」 forever (#604).

        ``author`` is the seat 芝士 holds in this room, and it is required: the
        wake is a note between two threads of the SAME handle, so the sender is
        right there. What retired with 结论 13 is the author value nobody ever
        wrote (the literal 「会话」), not the record.

        Opened DELIVERED, and that is not laziness: the Stop's close_one closes
        delivered intervals because 投喂 → Stop is what an interval means for a fed
        turn, so an undelivered row here would be one nothing could ever close.
        What delivery guards against — a Stop from the previous conversation
        closing a turn whose prompt is still in flight — cannot happen to this
        one: it is opened BY output from the very session whose Stop ends it.

        Registered in `_last_frame_at`/`_live_topics` but NOT in `_live`: no
        coroutine of ours is running it, and claiming otherwise would have the
        sweep try to cancel a task that does not exist. The frame stamp is what
        lets silence be judged at all — see `_wedged_turns`.
        """
        self._recent.append(
            {
                "turn_id": str(turn_id),
                "topic_id": str(topic_id),
                "continuation_id": str(turn_id),
                "author": author,
                "summon": False,
                "is_resume": False,
                "status": "running",
                "started_at": time.time(),
                "first_output_s": None,
                "tools": 0,
                "duration_s": None,
                "detail": None,
            }
        )
        self._last_frame_at[str(turn_id)] = time.monotonic()
        self._live_topics[str(turn_id)] = topic_id
        now = utcnow()
        await open_turn(
            chat_service.session_factory,
            turn_id=turn_id,
            conversation_id=topic_id,
            continuation_id=turn_id,
            author=author,
            content="",
            is_resume=False,
            # Nothing to re-send: there was no prompt. This is what stops the
            # sweep from ever picking one of these as a re-send candidate.
            resendable=False,
            started_at=now,
            session_id=session_id,
            # Stamped in the same write, not after it: a row that exists for even
            # a moment without it is a row a Stop landing in that moment cannot
            # close, and nothing would ever come back to close it.
            delivered_at=now,
            # The session goes on with its work while backends are replaced, so
            # its output can reach a process that never opened this turn after
            # one that did. That turn has its row already.
            exists_ok=True,
            agent_handle=agent_handle,
        )

    def close_turn_the_session_started(self, turn_id: uuid.UUID) -> None:
        """Drop the in-memory marks for such a turn once it has stopped.

        The durable row is closed by the Stop that ends it, like any other; these
        maps have no `finally` to fall out of, because no coroutine owns one.
        """
        self._forget_turn(turn_id)
        for rec in reversed(self._recent):
            if rec.get("turn_id") == str(turn_id):
                rec["status"] = "done"
                return

    def _forget_turn(self, turn_id: uuid.UUID) -> None:
        self._last_frame_at.pop(str(turn_id), None)
        self._live_topics.pop(str(turn_id), None)

    # Past this age an orphan is left for a person rather than acted on: a
    # deploy that stranded a prompt this long ago is one nobody still wants
    # re-sent unasked, so the sweep hands it over instead. Do NOT reach for this
    # number when an orphan goes unnoticed — the bug was never where the line
    # sits, it was that crossing it did nothing at all. Raising it only makes the
    # silence last longer.
    ORPHAN_STALE_S = 7200

    # A periodic sweep ignores registry entries younger than this. `_execute`
    # writes the disk registry and `_live` with no await between them, so there
    # is no window today — this is insurance against a refactor introducing one,
    # because the cost of getting it wrong is running a live turn twice.
    SWEEP_MIN_AGE_S = 60.0

    # How long a registered turn may emit NOTHING — no Block, no frame — before
    # the sweep calls it wedged. The floor is set by the longest a healthy turn
    # can legitimately stay quiet: one blocking tool call, whose own ceiling is
    # 10 minutes. 30 gives that 3x headroom, because the expensive mistake here
    # is the false positive (cancelling work that was fine), not the slow catch
    # — the incident this guards against ran for EIGHT HOURS.
    SILENT_TURN_S = 1800.0

    def _adopted(
        self,
        chat_service,
        record: TurnRecord,
        wedged: set[uuid.UUID],
    ) -> bool:
        """Is this turn still being worked, by something this process is not
        running? Then it is not an orphan and the sweep leaves it alone.

        Both halves have to hold. The screen must still be reachable — that is
        what a backend restart does NOT take with it, and what
        `recover_sessions` re-establishes on the way up. And the prompt
        must have reached it: a screen that is alive but never heard the task is
        not working on anything, and treating it as adopted would strand the
        message forever.

        A wedged turn is excluded by definition — its screen may well answer a
        `has_live_screen` probe while the thing behind it is dead, which is the
        whole reason silence is judged separately.
        """
        if record.turn_id in wedged or not record.delivered:
            return False
        try:
            # This turn's own seat: another teammate's session answering in the
            # same room is not this turn's work going on.
            return bool(
                chat_service.has_live_screen(record.topic_id, record.agent_handle)
            )
        except Exception:  # an unanswerable probe is not a yes
            logger.exception("live-screen probe failed for %s", record.topic_id)
            return False

    async def _wedged_turns(
        self,
        open_turns: dict[uuid.UUID, TurnRecord],
        last_activity: (
            Callable[[set[uuid.UUID]], Awaitable[dict[uuid.UUID, datetime]]] | None
        ),
        silence_s: float,
        now: datetime,
    ) -> set[uuid.UUID]:
        return await runtime_orphans._wedged_turns(
            self, open_turns, last_activity, silence_s, now
        )

    def _cancel_wedged(self, turn_id: str, topic_id: uuid.UUID) -> None:
        runtime_orphans._cancel_wedged(self, turn_id, topic_id)

    async def resume_orphans(self, chat_service) -> int:
        return await runtime_orphans.resume_orphans(self, chat_service)

    async def resume_lost_messages(self, chat_service, **scope) -> int:
        return await runtime_orphans.resume_lost_messages(self, chat_service, **scope)

    async def sweep_orphans(
        self,
        chat_service,
        *,
        min_age_s: float | None = None,
        last_activity: (
            Callable[[set[uuid.UUID]], Awaitable[dict[uuid.UUID, datetime]]] | None
        ) = None,
        silence_s: float | None = None,
    ) -> int:
        return await runtime_orphans.sweep_orphans(
            self,
            chat_service,
            min_age_s=min_age_s,
            last_activity=last_activity,
            silence_s=silence_s,
        )

    async def _settle_restart_orphans(
        self,
        chat_service,
        topic_id: uuid.UUID,
        entries: list[TurnRecord],
        now: datetime,
        *,
        since: datetime,
        allow_actions: bool = True,
    ) -> int:
        """One topic's remedy for turns that reached nobody.

        Returns how many remedial prompts were scheduled. Everything
        that got through has already been excluded upstream by `_adopted` — what
        arrives here is a topic whose screen is gone, or whose screen never
        heard the prompt.

        `entries` 可以是空的：一个话题里的孤儿轮次全都卡死、上面已经各自了结过时，
        这个话题照样要来一趟，因为平台侧执行记录是在这里读的（见下）。那一趟
        `allow_actions` 是假的，除了那份记录之外什么也不做。

        A re-send is for the newest re-sendable turn of each seat (see
        `_execute` for what re-sendable means): the pending-message mechanism
        re-hands its ORIGINAL text (an interrupted turn never stamps its inputs
        consumed), and that seat's others are folded into the same prompt.
        Teammates in one room each run their own conversation and a turn reads
        only what was addressed to its own agent, so one teammate's re-send
        cannot carry another's. A turn whose agent was never recorded could be
        anybody's; while one is among them the room gets a single re-send, as
        one conversation would.

        One narrow exception to "nobody heard it": a process can die between the
        transport accepting the write and the record of it, leaving a turn that
        DID arrive looking undelivered. `turns_that_produced_something` closes
        that window, and a failure to ask counts as "it arrived" — when we
        cannot know, sending again is the riskier side.

        Whose turn it was does not enter into it. A deploy that strands 平台's
        own work — a task's kickoff, 验收卡被驳回, CI 红了 — strands it just as
        permanently as a person's message, and the room shows nothing either
        way. Re-sending it is what keeps the platform working rather than merely
        quiet.

        重派之前先读平台侧的执行记录（结论 57，6.5），而且是在这个函数做任何别的事
        情之前 —— 这条顺序由 `tests/unit/test_retry_reads_the_dispatch_record.py`
        守着。悬着的那些调用说的是「发出去了，而结果永远不会回来了」：把它们重发
        一遍，是把一次可能已经落地的写操作再做一次，比什么都不做更坏。这类事情的
        下一步在人手上。

        「悬着」在这里不带年龄条件，而这个判断的整个重量压在**送到这个函数的话题是
        哪一种**上：屏幕没了，或者那条消息根本没送到屏幕，而且那几轮已经被上面的
        `close_turns` 关掉了。所以一次还没写回来的调用，结果再也到不了 agent 面前，
        无论那台机器此刻怎么样（`dispatch_log` 开头第二节）。这里也没有「先放着、
        下一次扫底再说」这个选项：轮次的区间已经关了，下一次扫底不会再看见这个话题。

        `since` 是这个话题里最早那个孤儿轮次的开始时刻，读只读那之后派出去的
        （`dispatch_log.unsettled`）。"""
        async with chat_service.session_factory() as ledger:
            unknown = await dispatch_log.unsettled(ledger, topic_id, since=since)

            delivery_attempts = set(
                await ledger.scalars(
                    select(Delivery.attempt_id).where(
                        Delivery.attempt_id.in_([record.turn_id for record in entries])
                    )
                )
            )
        delivered = {record.turn_id for record in entries if record.delivered}
        probe_ok = False
        try:
            delivered |= await chat_service.turns_that_produced_something(
                [record.turn_id for record in entries]
            )
            probe_ok = True
        except Exception:  # a failed probe must not kill the sweep
            logger.exception("orphan block probe failed for %s", topic_id)
        if probe_ok:  # what reached nobody, a delivery too, leaves no live turn
            chat_service.retire_unheard({r.turn_id for r in entries} - delivered)
        # Delivery owns retries and receipt uncertainty. A generic resend would
        # lose its recipient and attempt identity, and could duplicate a send.
        entries = [r for r in entries if r.turn_id not in delivery_attempts]
        delivered -= delivery_attempts
        attach = bool(delivered) or not probe_ok

        # Each re-send and the agent it goes back to (None: the room decides).
        resends: dict[str | None, TurnRecord] = {}
        if allow_actions and probe_ok and not unknown:
            candidates = [
                record
                for record in entries
                if record.turn_id not in delivered
                # Decided where the turn starts (see `_execute`): it holds for a
                # person's message and for every platform task alike.
                and record.resendable
                and record.age_s(now) <= self.ORPHAN_STALE_S
            ]
            for record in candidates:
                seat = record.agent_handle
                if seat not in resends or record.started_at > resends[seat].started_at:
                    resends[seat] = record
            if None in resends:
                resends = {None: max(candidates, key=lambda record: record.started_at)}

        # 一次部署把这个话题的轮次打断了，接下来会发生什么，决定要不要说话。
        #
        # 绝大多数情况平台自己就收拾干净了：送达过的，现场根本没死，订阅跟着屏幕
        # 活（#508），它送回的东西继续实时落回房间；没送达的，下面无条件原样重发
        # 一次。两种都不出声 —— 期望的就是它正常工作，正常工作没有可通报的。
        #
        # （这条事件原来每个被打断的轮次都发一次，理由是 #316：部署静默打断轮次、
        # 房间里不留痕迹，查的人只能猜，为此误诊过两次（#188）。那是取消 turn 之前
        # 的世界 —— 那时后端一死，会话的输出要等下一轮才浮出来，房间看着像停了。
        # 现在没有那个断裂，理由跟着不成立。）
        #
        # 剩下真正会伤到人的只有一种：消息卡在「已落库」和「已送进会话」中间，
        # 而且平台明确不会替他重发。这时候用户的话是真的消失了，芝士永远不会回，
        # 房间里也没有任何别的东西会显示这件事 —— 不说，没人知道要再问一次。
        # 两条路能走到这儿：
        #
        # - 搁得太久（越过 ORPHAN_STALE_S，2 小时）。扫描每 300 秒一轮、外加启动
        #   时一次，所以要越过它，平台得连着两小时没能扫 —— 那是一次宕机，不是一
        #   次部署。这时自动重发多半已经不是他要的了，得他自己决定还发不发。
        # - 这轮本身是一次重发（`resendable` 为假）。重发只把原始消息递一次，
        #   打断了就不连着再递，平台不会自动跑第二次。
        if unknown:
            # 5.2「通知他一次」：说清悬着的是哪几次调用，然后把这几行结清成
            # `unknown` —— 平台不再等它们了，问题在人手上。不写这一笔，同一个人会在
            # 这个房间此后每一次扫底里被问同一件事。
            #
            # 不受 `allow_actions` 管：那道门挡的是「平台还要不要替他做点什么」，而
            # 这条恰恰是平台做不了了才发的。关着门的那一档正是话题里有轮次卡死 ——
            # 机器死在手上，最需要说这句话的那一档。
            calls = (say("dispatchCall", tool=d.tool, id=d.key) for d in unknown)
            waiting = listing(calls)
            # No `turn_id`: `unknown` spans every turn of the topic whose
            # dispatch never wrote back, so no single turn can be named. Pinning
            # one would make the run-record page blame that turn alone for calls
            # that may belong to another; the topic is the honest scope here.
            await self._post_orphan_event(
                chat_service,
                topic_id,
                say("dispatchUnknown", count=len(unknown)),
                meta=notice(
                    EVENT_DISPATCH_UNKNOWN,
                    severity=SEVERITY_WARN,
                    # 平台到头了：做没做过只有那台机器知道，而它已经不说话了。
                    who=WHO_HUMAN,
                    detail=say("dispatchUnknownDetail", calls=waiting),
                    detail_label=say("labelDetails"),
                    retryable=True,
                ),
            )
            async with chat_service.session_factory() as ledger:
                for dispatch in unknown:
                    await dispatch_log.settle(
                        ledger, dispatch.id, dispatch_log.Outcome.unknown
                    )
                await ledger.commit()
        stranded = (
            allow_actions and probe_ok and not attach and not resends and not unknown
        )
        if stranded and entries:
            newest = max(entries, key=lambda record: record.started_at)
            age_s = newest.age_s(now)
            stale = age_s > self.ORPHAN_STALE_S
            # 平台提示统一契约: 房间里一行 `text`，展开才看的长文进 meta.detail。
            text = (
                say("messageUndeliveredStale", minutes=round(age_s / 60))
                if stale
                else say("resendInterrupted")
            )
            detail = (
                say("messageUndeliveredStaleDetail")
                if stale
                else say("resendInterruptedDetail")
            )
            await self._post_orphan_event(
                chat_service,
                topic_id,
                text,
                # The turn this is about: the newest stranded one, which is also
                # the one the stale/resendable verdict was read from.
                turn_id=newest.turn_id,
                meta=notice(
                    EVENT_DEPLOY_INTERRUPTED,
                    severity=SEVERITY_WARN,
                    # 平台不再自动做任何事了 —— 这条要人来。
                    who=WHO_HUMAN,
                    detail=detail,
                    detail_label=say("labelDetails"),
                    retryable=True,
                ),
            )
        if not allow_actions:
            return 0
        if attach:
            # What the surviving session said, and says next, lands through its
            # runtime's reader, which recovery already restarted.
            logger.info(
                "orphan turn(s) %s attached on topic %s (delivered=%d)",
                [record.turn_id for record in entries],
                topic_id,
                len(delivered),
            )
        for seat, resend in resends.items():
            self._schedule_resend(
                chat_service,
                topic_id,
                3.0,
                resend.content,
                continuation_id=resend.continuation_id,
                agent_handle=seat,
            )
            logger.info("orphan turn %s scheduled for re-send", resend.turn_id)
        return len(resends)

    async def _post_orphan_event(
        self,
        chat_service,
        topic_id: uuid.UUID,
        text: str,
        turn_id: uuid.UUID | None = None,
        meta: dict | None = None,
    ) -> None:
        await runtime_orphans._post_orphan_event(
            self, chat_service, topic_id, text, turn_id=turn_id, meta=meta
        )

    # The re-send opener's wording (#316): name the platform as the cause —
    # "被部署中断" — never "AI 服务返回错误" for a failure the deploy made.
    RESEND_REASON = say("resendAfterDeploy")

    #: Same contract for the other platform-caused silence: the room's tools
    #: vanished mid-turn, so 芝士 answered where nobody could hear it.
    TOOLS_REASON = say("resendAfterToolsLost")

    async def _recover_silent_turn(
        self,
        chat_service,
        topic_id: uuid.UUID,
        content: str,
        continuation_id: uuid.UUID | None,
    ) -> None:
        await runtime_orphans._recover_silent_turn(
            self,
            chat_service,
            topic_id,
            content,
            continuation_id,
            address_agent=addressed_to_agent,
        )

    def _schedule_resend(
        self,
        chat_service,
        topic_id: uuid.UUID,
        after_s: float,
        content: str,
        *,
        continuation_id: uuid.UUID | None = None,
        agent_handle: str | None = None,
    ):
        runtime_orphans._schedule_resend(
            self,
            chat_service,
            topic_id,
            after_s,
            content,
            continuation_id=continuation_id,
            agent_handle=agent_handle,
            address_agent=addressed_to_agent,
        )

    #: 工具断了是平台的事，平台自己接回来并重发；房间里的人不用动手。
    _TOOLS_RECOVERED_TEXT, _TOOLS_RECOVERED_META = tools_recovered_notice()

    def _credential_is_known_expired(self, topic_id: uuid.UUID) -> bool:
        """Does the backend already KNOW this topic's model credential is expired?
        (#388 缺陷一.) True only when the injected lookup returns an expiry in the
        past. A lookup failure is swallowed (never block a turn on it) and reads as
        "not known-expired" — the honest default, since a missing signal is not
        evidence of death."""
        if self._credential_expiry_of is None:
            return False
        try:
            exp = self._credential_expiry_of(topic_id)
        except Exception:  # a lookup must never break a turn
            logger.exception("credential-expiry lookup failed for topic %s", topic_id)
            return False
        return exp is not None and exp <= time.time()

    async def _execute(
        self,
        chat_service,
        topic_id: uuid.UUID,
        turn_id: uuid.UUID,
        *,
        author: str,
        content: str,
        addressed: Addressed,
        reply_to: str | None = None,
        attachments: list[dict] | None = None,
        is_resume: bool = False,
        resume_reason: str | None = None,
        nudge_event: str | None = None,
        nudge_meta: dict | None = None,
        continuation_id: uuid.UUID | None = None,
        # 已经准备好的帧流（`converse_prepared` 是现在唯一的来源）。None → 这里
        # 自己跑一轮 converse。
        frames: AsyncIterator[subscriber_queue.Frame] | None = None,
        lifecycle: dict[str, bool] | None = None,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> None:
        summon = _a_turn_was_addressed(addressed)
        state = ExecutionState(
            self._recent,
            self._live,
            self._last_frame_at,
            self._live_topics,
            self._delivered,
            self._host_failed_topics,
        )
        policy = ExecutionPolicy(
            self._timeout,
            self._first_output_timeout_s,
            self._credential_expired_fuse_s,
            self.RESEND_REASON,
            expected_error_types=(AppError,),
        )

        async def stamped(work_id: uuid.UUID) -> None:
            await stamp_delivery(chat_service.session_factory, work_id)

        async def silent(
            conversation_id: uuid.UUID, text: str, continuation: uuid.UUID
        ) -> None:
            await self._recover_silent_turn(
                chat_service, conversation_id, text, continuation
            )

        async def host_success(conversation_id: uuid.UUID) -> None:
            await record_host_success(topic_id=conversation_id)

        async def host_failure(
            conversation_id: uuid.UUID, work_id: uuid.UUID, failure: PlatformFailure
        ) -> None:
            verdict = await handle_host_failure(
                topic_id=conversation_id, failure=failure
            )
            if verdict.message:
                await self._intake._post_event(
                    chat_service,
                    conversation_id,
                    work_id,
                    verdict.message,
                    meta=verdict.event_meta,
                )

        async def system_event(
            conversation_id: uuid.UUID,
            text: str,
            work_id: uuid.UUID,
            *,
            meta: dict | None = None,
        ) -> dict | None:
            return await chat_service.post_system_event(
                conversation_id, text, work_id, meta=meta
            )

        def session_took_over(conversation_id: uuid.UUID, work_id: uuid.UUID) -> bool:
            return chat_service.session_took_over(conversation_id, work_id)

        effects = ExecutionEffects(
            system_event,
            stamped,
            silent,
            host_success,
            host_failure,
            self._credential_is_known_expired,
            session_took_over,
        )

        # The factory is called only at the old frame-source selection point
        # inside the executor's try, not before interval registration/commit.
        def frame_factory() -> AsyncIterator[dict]:
            return chat_service.converse(
                topic_id=topic_id,
                author=author,
                content=content,
                summon=summon,
                turn_id=turn_id,
                reply_to=reply_to,
                attachments=attachments,
                is_resume=is_resume,
                resume_reason=resume_reason,
                nudge_event=nudge_event,
                nudge_meta=nudge_meta,
                continuation_id=continuation_id or turn_id,
                **({"delivery_id": delivery_id} if delivery_id is not None else {}),
                **(
                    {"recipient_instance_id": recipient_instance_id}
                    if recipient_instance_id is not None
                    else {}
                ),
            )

        await execute(
            state,
            policy,
            effects,
            chat_service.session_factory,
            self._broker.publish,
            frame_factory,
            topic_id,
            turn_id,
            author=author,
            content=content,
            summon=summon,
            is_resume=is_resume,
            resume_reason=resume_reason,
            continuation_id=continuation_id,
            frames=frames,
            lifecycle=lifecycle,
        )

    async def _resolve_initial_seat(
        self,
        chat_service,
        topic_id: uuid.UUID,
        *,
        user_block_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
        recipient_handle: str | None = None,
    ) -> str:
        # Root composition: retain the real session owner's seat resolution,
        # and resolve it only at the original pre-lock admission point.
        return await chat_service._turn_seat_handle(
            topic_id,
            user_block_id=user_block_id,
            recipient_instance_id=recipient_instance_id,
            recipient_handle=recipient_handle,
        )

    def _nudge_after_work(self, chat_service, topic_id: uuid.UUID) -> None:
        # A pending-message scan belongs to this scheduler, including its tasks.
        nudge_messages(chat_service, topic_id, runner=self)
