"""Drive durable sessions and land their records in the shared room log.

The runtime does not hold the turn. The session lives in a runner on the session
machine that outlives this process; the runtime reads it from a cursor, one
poller per room, and ``recover`` finds it again after a restart. The iterator
``run_turn`` hands back reads that same session.

A harness supplies its handle, its subscription, and the three verbs its
protocol spells differently: what a runner's ``ping`` says about a turn in
flight, what words said mid-turn are sent as, and how a turn is taken away.

What ends a turn the session did not end itself is decided here, the same way
for every harness (``docs/agent-liveness.md``): the process is gone, or the
runner has not answered for ``RUNNER_GONE_S`` while a turn is open; the session
has been talking without working for ``no_progress_s``; or something said to it
has gone unread for ``unread_grace_s``. The last two read the clocks a
subscription keeps from what its records say (``subscription.marks_of``).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent import attachments
from app.domain.agent.device_hub import DeviceCallError, DeviceNotReady, DeviceOffline
from app.domain.agent.harness import (
    Backlog,
    MemoryConsumer,
    Opening,
    RoomReader,
    SessionRef,
    UnreadProbe,
)
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.harness.driven.subscription import (
    OUTPUT,
    PROGRESS,
    TOOL_RETURNED,
    TOOL_STARTED,
    Seat,
    Subscription,
)
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_CODE,
    PROMPT_UNDELIVERED_MESSAGE,
    TURN_TIMEOUT_CODE,
    TURN_TIMEOUT_MESSAGE,
)
from app.domain.agent.reads import (
    Completed,
    Reachable,
    Read,
    Received,
    Terminated,
    Working,
    Writing,
)
from app.domain.agent.service import AgentEvent, AgentResult, AgentSessionInfo
from app.domain.delivery.input_identity import (
    InputIdentity,
    InputOutcomeUnconfirmed,
    InputReceipt,
    InputRegistrar,
    WorkCompletion,
    WorkTermination,
)

# Every read of a room's journal is a call to its device. A runner holds a read
# until it has something to answer it with (``driven.runner``), so the poller
# asks again as soon as it is answered: a quiet seat costs one read per
# ``READ_WAIT_S``, and a record or a token the session writes is read the
# moment it is written. Far below every timeout on the way: the call's own
# (660 s), the connection owner's, and the connector's.
READ_WAIT_S = 25.0
# How long a runner may go unanswered while a turn is open before the turn is
# called dead. Longer than the connection owner takes to come back after a
# release, and than a device takes to reconnect after a network blip: those
# are waited out, and a runner that is still there answers again.
RUNNER_GONE_S = 120.0
# How often a room with no turn open looks again for a runner that is not there.
# A runner lets a session that has sat idle go (``driven.runner``), and nobody is
# waiting on it: the next message starts it again, and wakes this read at once.
IDLE_GONE_READ_S = 60.0
# Output this recent means the session is talking right now. Talking without a
# tool call or an ending for ``no_progress_s`` is a loop; a session that has
# gone quiet is judged by whether its process is alive, not by this.
TALKING_S = 300.0


class RunnerUnsupported(ScreenSetupError):
    """The runner a seat was greeted by cannot hold a read (``LONG_POLL``), and
    every read of a seat is one the runner holds until there is news."""


@dataclass
class Clock:
    """What one open turn has done lately, on the monotonic clock."""

    opened: float
    progressed: float
    said: float | None = None
    returned: float | None = None
    running: set[str] = field(default_factory=set)
    seen: bool = False

    def pulse(self, marks: frozenset[str], now: float) -> None:
        self.seen = True
        for mark in marks:
            if mark == PROGRESS:
                self.progressed = now
            elif mark == OUTPUT:
                self.said = now
            elif mark.startswith(TOOL_STARTED):
                self.running.add(mark.removeprefix(TOOL_STARTED))
            elif mark.startswith(TOOL_RETURNED):
                self.running.discard(mark.removeprefix(TOOL_RETURNED))
                self.returned = now


class Handle(Protocol):
    """Where one room's session runs, as the channel found it."""

    @property
    def session(self) -> SessionRef: ...

    @property
    def device_id(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def agent_handle(self) -> str: ...

    @property
    def mirror(self) -> Path: ...

    @property
    def capabilities(self) -> frozenset[str]: ...


class SessionChannel[H: Handle](Protocol):
    name: str
    provisions_machine: bool
    deferred_work: bool
    builds_model_env: bool

    def available(self) -> bool: ...

    async def prepare_topic(self, **kwargs) -> tuple[bool, str]: ...

    async def ensure(
        self, session: SessionRef, opening: Opening, live: H | None = None
    ) -> H:
        """The seat's session, started if it has to be.

        ``live`` is the seat's handle when this process started or confirmed it
        and its runner has answered alive on every read since. The channel
        hands it back without asking the machine again when nothing the
        session was started with has changed, and otherwise ensures it as if
        it were not given.
        """
        ...

    async def call(self, handle: H, method: str, params: dict) -> dict: ...

    async def discover(self, device_id: str | None) -> list[H]: ...

    async def images(self, handle: H, images: list[dict]) -> list: ...


class DrivenRuntime[H: Handle]:
    harness: str
    embeds_images = True
    #: Whether this harness's sessions keep memory as files and can reconcile
    #: them (``memory()`` below). False by default, and that default is the
    #: safe one: the system prompt's memory section says 「写进这里，平台下一轮
    #: 就有一份」, which is a lie for a harness with no way back.
    keeps_memory = False
    #: How messages a person may read name the harness.
    label: str
    #: How the poller's log lines name what it reads.
    records: str
    #: The poller's exception line, which alerts are already grouped by.
    read_failure: str
    #: The harness module's own logger, so a line names where it came from.
    logger: logging.Logger
    #: The runner method that takes words said to a session mid-turn.
    steer: str
    #: Whether the runner's acceptance of an input is the session reading it.
    #: False for a harness whose records say when an input was read; its
    #: subscription reports the receipt from there (``Subscription.receipt``).
    receipt_on_accept = True

    def __init__(
        self,
        channel: SessionChannel[H],
        *,
        hard_ceiling_s: float = 900,
        no_progress_s: float = 0.0,
        unread_grace_s: float = 0.0,
    ):
        self.channel = channel
        self.hard_ceiling_s = hard_ceiling_s
        self.no_progress_s = no_progress_s
        self.unread_grace_s = unread_grace_s
        # 一间房给每个 agent 各摆一个座位（Seat = (topic, agent_handle)）：会话、
        # 订阅、轮询任务、在跑的活和它的钟都按座位键住。同一间房里另一个 agent 的
        # 会话与这里互不相干——它开它的轮、它等它的设备，谁也不顶掉谁。按房间键住
        # 的那个版本里，第二个 agent 一进房就把第一个的会话停了（ensure）、订阅顶
        # 掉（_attach）、在跑的活盖掉（work[topic]），多 agent 同房间因此不可能。
        self.clocks: dict[Seat, Clock] = {}
        # Work a verdict already ended. What the session goes on saying still
        # lands; a second ending for it does not.
        self.closed: set[uuid.UUID] = set()
        self.unreachable: dict[Seat, float] = {}
        # The open work each seat was last told is waiting on its machine.
        self.told_waiting: dict[Seat, uuid.UUID] = {}
        self.unread: UnreadProbe | None = None
        self.live: dict[Seat, H] = {}
        # (seat, conversation) pairs this process saw die (`_died` / a
        # verdict): the only thing that makes "no live handle" a fact
        # rather than an unanswered question (FB-56 legacy③). A turn on
        # the same seat in a DIFFERENT conversation is not covered by it.
        self.dead: set[tuple[Seat, str]] = set()
        # Conversations this round's recovery found alive, and found
        # TERMINATED by an authority's own per-conversation answer (FB-56
        # legacy③): the channel that placed them reports each pointer's
        # outcome, and only a terminal answer bound to the stored resume
        # token lands here. "Not heard from" never does — that is unknown.
        self.found_conversations: set[tuple[Seat, str]] = set()
        self.terminal_conversations: set[tuple[Seat, str]] = set()
        self._owns_sessions_provider = None
        # Seats whose handle this process ensured and whose runner has answered
        # alive on every read since: what a send hands the channel as ``live``.
        # A read that fails or says the agent process is gone takes the seat
        # out, and only the next ensure puts it back.
        self.answering: set[Seat] = set()
        self.subscriptions: dict[Seat, Subscription] = {}
        self.tasks: dict[Seat, asyncio.Task] = {}
        self.work: dict[Seat, uuid.UUID] = {}
        # Messages sent while another turn held their seat, until the session
        # says which turn read them; and, once it has, the messages each
        # running turn took in. Those end when the turn that read them ends.
        self.riding: dict[Seat, set[uuid.UUID]] = {}
        self.taken: dict[uuid.UUID, list[tuple[Seat, uuid.UUID]]] = {}
        self.queues: dict[uuid.UUID, asyncio.Queue[AgentEvent]] = {}
        self.woken: dict[Seat, asyncio.Event] = {}
        #: Where the room hears what its sessions say and do.
        self.reader: RoomReader | None = None
        # 带下划线，因为它不能和下面那个 `memory()`（这一侧往会话里问一次对账）
        # 同名：`self.memory = None` 会把那个方法盖掉，而 `AgentRuntime` 是
        # `runtime_checkable` 的 Protocol，`isinstance` 拿不到方法就答否——
        # 于是每一个 runtime 都「跑不了 harness」。别的消费者没这个问题。
        self._memory: MemoryConsumer | None = None

    # --- what the harness supplies -------------------------------------------

    def conversation(self, handle: H) -> str:
        """The harness's own id for the session the handle points at."""
        raise NotImplementedError

    def subscribe(
        self, handle: H, call: Callable[[str, dict], Awaitable[dict]]
    ) -> Subscription:
        raise NotImplementedError

    def backlog(self, session: SessionRef) -> Backlog:
        raise NotImplementedError

    def working(self, status: dict) -> bool:
        """Whether a runner's ``ping`` answer says a turn is in flight."""
        raise NotImplementedError

    async def interrupt(self, session: SessionRef) -> bool:
        raise NotImplementedError

    # --- the runtime surface -------------------------------------------------

    @property
    def name(self) -> str:
        return self.channel.name

    @property
    def provisions_machine(self) -> bool:
        return self.channel.provisions_machine

    @property
    def deferred_work(self) -> bool:
        return self.channel.deferred_work

    @property
    def builds_model_env(self) -> bool:
        return self.channel.builds_model_env

    def available(self) -> bool:
        return self.channel.available()

    async def prepare_topic(self, **kwargs) -> tuple[bool, str]:
        return await self.channel.prepare_topic(**kwargs)

    def bind_reader(self, reader: RoomReader) -> None:
        self.reader = reader

    def bind_unread_probe(self, probe: UnreadProbe) -> None:
        self.unread = probe

    def bind_memory(self, consumer: MemoryConsumer) -> None:
        self._memory = consumer

    async def _hear(
        self, session: SessionRef, read: Read, *, required: bool = False
    ) -> None:
        """Hand the room one thing its session said or did. What the room has
        to hear for its books to be right (``required``) is an error to drop;
        the rest is only what it shows."""
        if self.reader is not None:
            await self.reader(session, read)
        elif required:
            raise RuntimeError(f"{self.label} room reader is not bound")

    def _room(
        self, project: uuid.UUID, topic: uuid.UUID, agent: str = ""
    ) -> SessionRef:
        return SessionRef(project, topic, agent, harness=self.harness)

    async def _hear_receipt(self, receipt: InputReceipt) -> None:
        identity = receipt.identity
        await self._hear(
            self._room(identity.project_id, identity.topic_id),
            Read(None, str(identity.work_id), Received(receipt)),
            required=True,
        )

    async def _hear_completion(self, completion: WorkCompletion) -> None:
        await self._hear(
            self._room(completion.project_id, completion.topic_id),
            Read(None, str(completion.work_id), Completed(completion)),
            required=True,
        )

    async def _hear_termination(self, termination: WorkTermination) -> None:
        await self._hear(
            self._room(termination.project_id, termination.topic_id),
            Read(None, str(termination.work_id), Terminated(termination)),
            required=True,
        )

    def _memory_hook(self, topic: uuid.UUID) -> Callable[[], Awaitable[None]]:
        """`reconcile_memory` 绑到这一间房，给订阅那一侧的一轮结束用（它不带参数）。

        三个 harness 都从这里取，所以「一轮结束时对一次账」是这套骨架的事实，
        而不是谁恰好写了一句：会话不存记忆文件的那几个问下去也会得到 None。
        """

        async def hook() -> None:
            await self.reconcile_memory(topic)

        return hook

    async def reconcile_memory(self, topic: uuid.UUID) -> None:
        """Ask the room to reconcile its memory tree, and never fail the turn on it.

        A memory tree that could not be reconciled is a memory that is a turn
        behind — the next moment asks again, with the same three sides. Letting
        the exception through would end a turn that was otherwise fine, over the
        room's notes.
        """
        if self._memory is None:
            return
        try:
            await self._memory(topic)
        except Exception:
            self.logger.exception("memory reconciliation failed topic=%s", topic)

    async def memory(self, topic_id: uuid.UUID, request: dict) -> dict | None:
        """A harness whose sessions keep memory files answers this; others cannot.

        The default is ``None`` — «这里没有记忆文件», which the caller reads as
        「这一轮不用对账」 and not as a failure.
        """
        return None

    @staticmethod
    def _seat_of(session: SessionRef) -> Seat:
        """The seat a session ref names: (topic, agent)."""
        return (session.topic_id, session.agent_handle)

    @staticmethod
    def _seat_of_handle(handle: "Handle") -> Seat:
        """The seat a live handle sits in — its session's, not its own.

        A handle carries two names for the agent: ``session.agent_handle`` keys
        the conversation (the session row, the resume token, every ref chat
        builds), while ``agent_handle`` is the acting seat the machine recorded
        (the token's `a` claim), and the two differ whenever a room addresses a
        teammate by instance. The runtime's seats key conversations — send and
        recover must land on the same key or one conversation gets two
        subscriptions reading the same mirror — so the session's name wins.
        """
        return (handle.session.topic_id, handle.session.agent_handle)

    def _room_seat(self, topic_id: uuid.UUID) -> Seat | None:
        """The room's only live seat, or None when there is none — or several.

        Room-scoped questions (the controls relay, the memory relay) predate
        seats; with two teammates live in one room they have no single answer,
        and None makes the caller say so rather than pick a teammate at random.
        """
        seats = [seat for seat in self.live if seat[0] == topic_id]
        return seats[0] if len(seats) == 1 else None

    def pulse(self, seat: Seat, marks: frozenset[str]) -> None:
        """What the subscription just read about the seat's open turn."""
        if clock := self.clocks.get(seat):
            clock.pulse(marks, time.monotonic())

    def verdict(self, seat: Seat) -> AgentResult | None:
        """Is the seat's open turn stuck in a way only its own clocks can show?"""
        topic = seat[0]
        clock = self.clocks.get(seat)
        if clock is None:
            return None
        now = time.monotonic()
        if (
            self.no_progress_s > 0
            and clock.said is not None
            and now - clock.said < TALKING_S
            and now - clock.progressed >= self.no_progress_s
        ):
            self.logger.warning(
                "%s output for %.0fs with no tool call or ending topic=%s",
                self.label,
                now - clock.progressed,
                topic,
            )
            return AgentResult(
                text=TURN_TIMEOUT_MESSAGE,
                session_id=None,
                is_error=True,
                failure_code=TURN_TIMEOUT_CODE,
            )
        # Input is read at tool boundaries, so while a tool runs the clock does
        # not run, and after one it runs from the return: a message that sat
        # behind a 40-minute command gets its grace from the first moment the
        # session could have read it. Before the turn said anything there is no
        # evidence of reading at all, which is the process's business.
        if (
            self.unread_grace_s > 0
            and self.unread is not None
            and clock.seen
            and not clock.running
        ):
            waiting = self.unread(topic)
            if waiting is not None:
                waiting = max(waiting, clock.returned or 0.0, clock.opened)
                if now - waiting >= self.unread_grace_s:
                    self.logger.warning(
                        "%s input unread for %.0fs topic=%s",
                        self.label,
                        now - waiting,
                        topic,
                    )
                    return AgentResult(
                        text=PROMPT_UNDELIVERED_MESSAGE,
                        session_id=None,
                        is_error=True,
                        failure_code=PROMPT_UNDELIVERED_CODE,
                    )
        return None

    async def _end_by_verdict(self, handle: H, result: AgentResult) -> None:
        """End the turn in the room's books, and take the work away."""
        topic = handle.session.topic_id
        seat = self._seat_of_handle(handle)
        work = self.work[seat]
        await self._consume(
            handle.session.project_id,
            topic,
            work,
            AgentResult(
                text=result.text,
                session_id=self.conversation(handle),
                is_error=result.is_error,
                failure_code=result.failure_code,
                agent_handle=handle.agent_handle,
                harness=self.harness,
            ),
            f"{self.harness}:{self.conversation(handle)}:verdict:{work}",
            False,
            False,
        )
        await self._activity(handle.session.project_id, seat, work, False)
        self.closed.add(work)
        try:
            await self.interrupt(handle.session)
        except Exception:  # noqa: BLE001 — the turn is already ended here
            self.logger.exception("%s interrupt after a verdict failed", self.label)

    async def _consume(self, project, topic, work, event, eid, seen, unsolicited):
        if isinstance(event, AgentResult) and work in self.closed:
            return
        if queue := self.queues.get(work):
            await queue.put(event)
        else:
            await self._hear(
                self._room(project, topic),
                Read(
                    None,
                    str(work),
                    event,
                    eid=eid,
                    text_seen=seen,
                    unsolicited=unsolicited,
                ),
                required=True,
            )
        if isinstance(event, AgentResult) and event.thread_label is None:
            await self._end_taken(project, topic, work, event)

    def _took(self, seat: Seat, input_id: str, work: uuid.UUID) -> None:
        """The session read an input inside the turn ``work`` was running."""
        riding = self.riding.get(seat, set())
        taken = next((sent for sent in riding if str(sent) == input_id), None)
        if taken is None or taken == work:
            return
        riding.discard(taken)
        self.taken.setdefault(work, []).append((seat, taken))

    async def _end_taken(self, project, topic, work, result: AgentResult) -> None:
        """End the messages ``work`` took in, the way ``work`` itself ended.

        Nothing the session writes ever names them again: they were answered
        inside this turn, and without an ending of their own the room would go
        on treating them as running — reminding the agent about a person it
        already answered, and leaving their turns open for good.
        """
        for seat, taken in self.taken.pop(work, ()):
            await self._consume(
                project,
                topic,
                taken,
                AgentResult(
                    text="",
                    session_id=result.session_id,
                    is_error=result.is_error,
                    failure_code=result.failure_code,
                    agent_handle=result.agent_handle,
                    harness=result.harness,
                    taken_into=work,
                ),
                f"{self.harness}:taken:{taken}",
                False,
                False,
            )
            await self._activity(project, seat, taken, False)

    async def _activity(self, project, seat: Seat, work, active):
        if work in self.closed:
            return
        if active:
            # Read only after the turn it was sent into ended: a turn of its own.
            self.riding.get(seat, set()).discard(work)
            self.work[seat] = work
            now = time.monotonic()
            self.clocks[seat] = Clock(opened=now, progressed=now)
        elif self.work.get(seat) == work:
            self.work.pop(seat, None)
            self.clocks.pop(seat, None)
        if work not in self.queues:
            await self._hear(
                self._room(project, seat[0], seat[1]),
                Read(None, str(work), Working(active)),
            )

    def bind_owns_sessions(self, provider) -> None:
        """Who answers ``owns_sessions`` at attach time (FB-56)."""
        self._owns_sessions_provider = provider

    async def _attach(self, handle: H) -> None:
        seat = self._seat_of_handle(handle)
        async with attachments.lock(seat):
            # Ownership is checked FIRST, and the idempotent fast path is
            # checked INSIDE the lock with it (FB-56): an early return ahead
            # of both would wave a same-handle recover through while the
            # platform is not owning sessions — the old maps still answer
            # "attached", and the caller goes on bookkeeping a recovery the
            # ownership gate never allowed.
            if self._owns_sessions_provider is not None and (
                not self._owns_sessions_provider().owns_sessions
            ):
                raise DeviceOffline(
                    f"platform is not owning sessions right now (topic={seat[0]})"
                )
            if self.live.get(seat) == handle and seat in self.subscriptions:
                return
        if LONG_POLL not in handle.capabilities:
            raise RunnerUnsupported(
                f"The {self.label} runner on {handle.device_id} at {handle.state} "
                f"cannot hold a read (it announced {sorted(handle.capabilities)}, "
                f"without {LONG_POLL!r}); it is not supported"
            )
        await self._detach(seat)
        handle.mirror.parent.mkdir(parents=True, exist_ok=True)

        async def call(method: str, params: dict) -> dict:
            return await self.channel.call(handle, method, params)

        # The subscription instance IS this seat's attachment (FB-56): mint
        # its id under the seat's lock, so an ownership mutation is either
        # whole-before or whole-after this swap, never interleaved with it.
        subscription = self.subscribe(handle, call)
        subscription.attachment_id = uuid.uuid4().hex
        async with attachments.lock(seat):
            # The flag is checked HERE, under the lock — not once at some
            # route's entry (FB-56): while a stop is being taken, an attach
            # can land between the flag flip and the swap, and only the lock
            # makes the order real. A refused attach registers nothing. The
            # provider is bound at wiring time (`api/deps.py`), never
            # imported here — the import is a cycle.
            if self._owns_sessions_provider is not None and (
                not self._owns_sessions_provider().owns_sessions
            ):
                raise DeviceOffline(
                    f"platform is not owning sessions right now (topic={seat[0]})"
                )
            self.live[seat] = handle
            self.dead = {pair for pair in self.dead if pair[0] != seat}
            self.subscriptions[seat] = subscription
            attachments.note(seat, subscription.attachment_id)

    def _listen(self, seat: Seat) -> None:
        if seat not in self.tasks or self.tasks[seat].done():
            self.tasks[seat] = asyncio.create_task(
                self._poll(seat), name=f"{self.records} {seat[0]}/{seat[1]}"
            )

    def _wake(self, seat: Seat) -> None:
        """Cut short the wait of a seat that has just been given something."""
        if event := self.woken.get(seat):
            event.set()

    def _wait_s(self, seat: Seat) -> float:
        """How long a read may be held: never so long that a turn's own
        clocks (``verdict``) are read late by more than a fraction of them."""
        if seat not in self.work:
            return READ_WAIT_S
        clocks = [s / 4 for s in (self.no_progress_s, self.unread_grace_s) if s > 0]
        return min([READ_WAIT_S, *clocks])

    async def _show(self, seat: Seat, live: dict) -> None:
        """Hand on what the seat's agent is in the middle of writing."""
        handle = self.live.get(seat)
        if handle is None:
            return
        work = live.get("work_id") or self.work.get(seat)
        await self._hear(
            handle.session,
            Read(
                None,
                str(work) if work else None,
                Writing(tuple(live.get("blocks") or ()), author=handle.agent_handle),
            ),
        )

    async def _wait(self, seat: Seat, delay: float) -> None:
        event = self.woken.setdefault(seat, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), delay)
        except TimeoutError:
            return
        event.clear()

    async def _poll(self, seat: Seat) -> None:
        topic = seat[0]
        # Waiting for a device to come back is this loop's job, not a failure of
        # it, so the wait is said once and the return is said once. Per-task
        # state: one of these runs per seat.
        waiting = False
        while seat in self.subscriptions:
            subscription = self.subscriptions[seat]
            try:
                await subscription.drain(wait=self._wait_s(seat))
                if not subscription.heard.get("alive", True):
                    self.answering.discard(seat)
                self.unreachable.pop(seat, None)
                if live := subscription.heard.get("live"):
                    await self._show(seat, live)
                # The runner that held the read says with its answer whether
                # its agent is still there.
                if seat in self.work:
                    handle = self.live[seat]
                    if not subscription.heard.get("alive", True):
                        await self._died(handle)
                        return
                    if verdict := self.verdict(seat):
                        await self._end_by_verdict(handle, verdict)
            except DeviceOffline:
                self.answering.discard(seat)
                # A room whose machine is switched off is the ordinary state of
                # a platform nobody is using this minute, and this loop exists
                # to wait it out. Logged as an exception it was two ERROR lines
                # a second per topic, every one of them an alert: 37 of the 53
                # messages in the alert channel on 2026-09-16 were this, under
                # a name that described a fault.
                if not waiting:
                    waiting = True
                    self.logger.warning(
                        "%s waiting for the device topic=%s", self.records, topic
                    )
                await self._say_waiting(seat, "device offline")
                if await self._gone(seat):
                    return
                await asyncio.sleep(2)
            except DeviceCallError as exc:
                self.answering.discard(seat)
                # The machine is there and said no — the runner's socket is not
                # up yet (a cold one can take about a minute) or its home is gone.
                # Either way the next read is what tells, and the machine's
                # own words are the fact worth writing down, once.
                if seat not in self.work and not isinstance(exc, DeviceNotReady):
                    # Nothing open: the runner let an idle session go.
                    if not waiting:
                        waiting = True
                        self.logger.info(
                            "%s runner gone with no turn open topic=%s: %s",
                            self.records,
                            topic,
                            exc,
                        )
                    await self._wait(seat, IDLE_GONE_READ_S)
                    continue
                if not waiting:
                    waiting = True
                    self.logger.warning(
                        "%s waiting for the runner topic=%s: %s",
                        self.records,
                        topic,
                        exc,
                    )
                await self._say_waiting(seat, f"runner not answering: {exc}")
                if await self._gone(seat):
                    return
                await asyncio.sleep(2)
            except httpx.TransportError as exc:
                self.answering.discard(seat)
                # The connection owner is being replaced, or the socket to it
                # went while this read was in flight. Retrying is what this loop
                # is for, and the owner is back within seconds — but at ERROR
                # every release of it wrote a read failure into the alert
                # channel, as it did at 01:47 UTC on 2026-09-20.
                if not waiting:
                    waiting = True
                    self.logger.warning(
                        "%s waiting for the connection owner topic=%s: %s",
                        self.records,
                        topic,
                        exc,
                    )
                await self._say_waiting(seat, f"device connection lost: {exc}")
                if await self._gone(seat):
                    return
                await asyncio.sleep(2)
            except Exception:
                self.answering.discard(seat)
                # The runner outlives a backend or connector outage. Re-reading
                # is safe because the landing cursor only moves after the
                # persistence callback returned.
                self.logger.exception("%s topic=%s", self.read_failure, topic)
                await asyncio.sleep(2)
            else:
                if waiting:
                    waiting = False
                    self.logger.info("%s resumed topic=%s", self.records, topic)
                    await self._say_resumed(seat)
                if not subscription.heard.get("alive", True):
                    # Its agent process is gone with no turn open: the runner
                    # goes with it, and the next message starts it again.
                    await self._wait(seat, IDLE_GONE_READ_S)

    async def _say_waiting(self, seat: Seat, reason: str) -> None:
        """Tell the room this seat's open turn is waiting on the machine — once
        per turn per outage. A seat with no turn open is not waiting for
        anything: its machine being off is the ordinary state of a platform
        nobody is using."""
        work = self.work.get(seat)
        handle = self.live.get(seat)
        if work is None or handle is None or self.told_waiting.get(seat) == work:
            return
        self.told_waiting[seat] = work
        await self._hear(
            handle.session, Read(None, str(work), Reachable(False, reason))
        )

    async def _say_resumed(self, seat: Seat) -> None:
        work = self.told_waiting.pop(seat, None)
        handle = self.live.get(seat)
        if work is None or handle is None or self.work.get(seat) != work:
            return
        await self._hear(handle.session, Read(None, str(work), Reachable(True)))

    async def _gone(self, seat: Seat) -> bool:
        """Has the runner of an open turn been out of reach for too long?

        A turn nobody can reach is not one that will ever report an ending,
        and the room waits on it until somebody says so.
        """
        if seat not in self.work or seat not in self.live:
            self.unreachable.pop(seat, None)
            return False
        since = self.unreachable.setdefault(seat, time.monotonic())
        if time.monotonic() - since < RUNNER_GONE_S:
            return False
        self.unreachable.pop(seat, None)
        await self._died(self.live[seat], out_of_reach=True)
        return True

    async def _died(self, handle: H, *, out_of_reach: bool = False) -> None:
        """The turn is over with nobody able to answer it. Say so where the turn
        is, or the room waits on something that will never answer.

        Two things end a turn this way and the room is told which: a runner that
        answered and said its process was gone, and one that has not answered for
        ``RUNNER_GONE_S`` at all. The second is not the same fact as the first —
        a runner that never came up is the shape of a machine still being
        prepared, and saying its process exited then is a sentence the room reads
        as an unexplained crash. Whoever reads either one has the socket, the
        machine and the runner's log to go on; the platform does not, so it says
        only what it saw.
        """
        topic = handle.session.topic_id
        seat = self._seat_of_handle(handle)
        work = self.work[seat]
        await self._consume(
            handle.session.project_id,
            topic,
            work,
            AgentResult(
                text=(
                    say(
                        "runnerUnresponsive",
                        harness=self.label,
                        seconds=f"{RUNNER_GONE_S:.0f}",
                    )
                    if out_of_reach
                    else f"{self.label} session process exited"
                ),
                session_id=self.conversation(handle),
                is_error=True,
                agent_handle=handle.agent_handle,
                harness=self.harness,
            ),
            f"{self.harness}:{self.conversation(handle)}:exit:{work}",
            False,
            False,
        )
        await self._activity(handle.session.project_id, seat, work, False)
        self.closed.add(work)
        self.live.pop(seat, None)
        if not out_of_reach:
            # A verdict/exit the runner itself reported is death. Merely
            # being out of reach is not — a machine still being prepared
            # looks exactly the same, and that one stays "unknown".
            self.dead.add((seat, self.conversation(handle)))
        self.answering.discard(seat)

    async def ensure(self, session, opening, *, work_id=None) -> H:
        seat = self._seat_of(session)
        previous = self.live.get(seat)
        if previous and opening.agent_handle != previous.agent_handle:
            # A different teammate is taking this seat over; the conversation
            # that belonged to the last one does not carry over to them. Other
            # seats in the same room are not this call's business. A runner
            # that let its idle session go has nothing to interrupt.
            with contextlib.suppress(DeviceCallError):
                await self.interrupt(session)
            await self.close(session)
        task = self.tasks.get(seat)
        live = (
            self.live.get(seat)
            if seat in self.answering and task is not None and not task.done()
            else None
        )
        handle = await self.channel.ensure(session, opening, live)
        await self._attach(handle)
        self.answering.add(seat)
        return handle

    async def check_input_protocol(self, handle: H) -> None:
        """A harness may refuse registration before any external input attempt."""

    async def send(
        self,
        session: SessionRef,
        message: str,
        opening: Opening,
        *,
        work_id: uuid.UUID,
        on_mark: Callable[[uuid.UUID], None],
        register_input: InputRegistrar,
        images: list[dict] | None = None,
        owes_reply: bool = False,
    ) -> bool:
        if opening.expected_native_session is not None:
            handle = self.live.get(self._seat_of(session))
            if (
                handle is None
                or self.conversation(handle) != opening.expected_native_session
                or handle.session.project_id != session.project_id
            ):
                raise ValidationError(
                    "The original Ask session is not live; no replacement started"
                )
        else:
            handle = await self.ensure(session, opening, work_id=work_id)
        await self.check_input_protocol(handle)
        payload = await self.channel.images(handle, images or [])
        on_mark(work_id)
        await self._consume(
            session.project_id,
            session.topic_id,
            work_id,
            AgentSessionInfo(
                session_id=self.conversation(handle),
                agent_handle=handle.agent_handle,
                harness=self.harness,
            ),
            f"{self.harness}:{self.conversation(handle)}:opening:{work_id}",
            False,
            False,
        )
        seat = self._seat_of(session)
        # A message sent while the session is in the middle of a turn is read at
        # that turn's next tool boundary and answered inside it: its records,
        # its result and its end all carry the running turn's work, and none
        # ever names this one. Taking the seat over would leave it holding a
        # turn that never ends, and the next time the session goes away (the
        # runner lets an idle one go after ``IDLE_EXIT_S``) that turn would be
        # failed as a crash. A platform turn (``run_turn``) takes the seat all
        # the same: its caller waits on its own queue for an ending, and the
        # session going away is the only one it can get.
        if seat not in self.clocks or work_id in self.queues:
            self.work[seat] = work_id
        else:
            self.riding.setdefault(seat, set()).add(work_id)
        self._wake(seat)
        # 记忆先落到会话目录里，输入后写进去：agent 这一轮一睁眼读到的应当是平台
        # 现在这一份（别人刚改的也在里面），而不是它上一次看见的那一份。
        await self.reconcile_memory(session.topic_id)
        identity = InputIdentity(
            session.project_id,
            session.topic_id,
            handle.agent_handle,
            self.harness,
            self.conversation(handle),
            work_id,
            work_id,
        )
        await register_input(identity)
        try:
            await self._submit_registered(
                handle,
                identity,
                "send",
                {
                    "input_id": str(work_id),
                    "work_id": str(work_id),
                    "text": message,
                    "images": payload,
                    **({"owes_reply": True} if owes_reply else {}),
                },
            )
        finally:
            # A lost acknowledgement does not mean the session stopped working.
            self._listen(self._seat_of(session))
        return True

    async def deliver(
        self,
        topic_id,
        text,
        images=None,
        *,
        register_input: InputRegistrar,
        expected_work_id=None,
        agent_handle=None,
        owes_reply=False,
    ) -> bool:
        """A person talking to a session that is already working.

        ``agent_handle`` names the seat when the caller knows whose turn this
        belongs to; without it the room must have exactly one working seat,
        or the expected work id must pick it out — a guess between two
        working teammates would inject the words into the wrong conversation.
        """
        seat = self._deliver_seat(topic_id, expected_work_id, agent_handle)
        if seat is None:
            return False
        handle = self.live.get(seat)
        work = self.work.get(seat)
        if handle is None or work is None:
            return False
        status = await self.channel.call(handle, "ping", {})
        if not self.working(status):
            return False
        if self.live.get(seat) is not handle or self.work.get(seat) != work:
            return False
        await self.check_input_protocol(handle)
        identity = InputIdentity(
            handle.session.project_id,
            topic_id,
            handle.agent_handle,
            self.harness,
            self.conversation(handle),
            uuid.uuid4(),
            work,
        )
        images_payload = await self.channel.images(handle, images or [])
        await register_input(identity)
        await self._submit_registered(
            handle,
            identity,
            self.steer,
            {
                "input_id": str(identity.input_id),
                "work_id": str(work),
                "text": text,
                "images": images_payload,
                **({"owes_reply": True} if owes_reply else {}),
            },
        )
        return True

    async def _submit_registered(
        self, handle: H, identity: InputIdentity, method: str, params: dict
    ) -> None:
        accepted = False
        try:
            await self.channel.call(handle, method, params)
            accepted = True
            # Only a harness whose acceptance *is* the read reports here. A
            # harness that says when its session read the input (Claude Code)
            # leaves this to the echo, so a consumer still holding the receipt
            # must never gate the send that admits it.
            if self.receipt_on_accept:
                await self._hear_receipt(InputReceipt(identity, "accepted"))
        except Exception as exc:
            # Even a transport error can follow admission at the remote end.
            # Keep the committed identity; the caller must not queue a new UUID.
            raise InputOutcomeUnconfirmed(identity, accepted=accepted) from exc

    def _deliver_seat(self, topic_id, expected_work_id, agent_handle) -> Seat | None:
        """Which seat a topic-addressed delivery means, or None when ambiguous."""
        if agent_handle is not None:
            return (topic_id, agent_handle)
        working = [seat for seat in self.work if seat[0] == topic_id]
        if expected_work_id is not None:
            working = [seat for seat in working if self.work[seat] == expected_work_id]
        return working[0] if len(working) == 1 else None

    async def ask_origin(self, project_id, topic_id, agent_handle):
        """Read this seat's exact live native identity without starting work."""
        seat = (topic_id, agent_handle)
        handle = self.live.get(seat)
        work = self.work.get(seat)
        if handle is None or work is None or handle.session.project_id != project_id:
            return None
        status = await self.channel.call(handle, "ping", {})
        if (
            not self.working(status)
            or status.get("work_id") != str(work)
            or self.live.get(seat) is not handle
            or self.work.get(seat) != work
        ):
            return None
        return {
            "harness": self.harness,
            "native_session_id": self.conversation(handle),
            "work_id": str(work),
            "recipient_handle": agent_handle,
        }

    def seat_state(self, seat: Seat) -> str:
        """ "live" if this process holds the seat, "dead" if it watched the
        seat die, else "unknown" — the sweep's third answer (FB-56 legacy③)."""
        if seat in self.live:
            return "live"
        if any(pair[0] == seat for pair in self.dead):
            return "dead"
        return "unknown"

    def dead_conversations(self, seat: Seat) -> set[str]:
        """The conversations on this seat this process saw die (FB-56)."""
        return {
            conversation for pair_seat, conversation in self.dead if pair_seat == seat
        }

    def holds(self, topic_id, agent_handle=None) -> bool:
        if agent_handle is not None:
            return (topic_id, agent_handle) in self.live
        return any(seat[0] == topic_id for seat in self.live)

    def work_in_flight(self, topic_id, agent_handle=None) -> uuid.UUID | None:
        """The work this runtime's live seat in the room is running, if one is.

        The runtime-side companion of ``ComputePool.work_in_flight``: with the
        agent named the answer is exact; without it only an unambiguous room
        gets one.
        """
        seats = [
            seat
            for seat in self.work
            if seat[0] == topic_id and (agent_handle is None or seat[1] == agent_handle)
        ]
        return self.work[seats[0]] if len(seats) == 1 else None

    async def _detach(self, seat: Seat) -> None:
        task = self.tasks.pop(seat, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        subscription = self.subscriptions.pop(seat, None)
        if subscription is not None:
            async with attachments.lock(seat):
                attachments.note(seat, None)
        self.live.pop(seat, None)
        self.answering.discard(seat)
        self.work.pop(seat, None)
        self.riding.pop(seat, None)
        self.woken.pop(seat, None)
        self.clocks.pop(seat, None)
        self.unreachable.pop(seat, None)
        self.told_waiting.pop(seat, None)
        if subscription is not None:
            await subscription.release()

    async def stop_listening(self) -> None:
        """Stop reading every session, and leave every session running.

        A read already under way finishes first. The landing cursor only moves
        once a record is persisted, so whatever this process had not landed is
        still unread for the process that listens next.

        The swap is per seat and by INSTANCE (FB-56): each seat's registry
        note and map removal happen under its own attachment lock, and only
        for the exact subscription this stop started with — an attach that
        won the seat in the meantime keeps its subscription, its registry
        entry and every map, because the swap it made was complete. The old
        instances are cancelled, awaited and released outside the lock.
        """
        # Stop targets are pinned at the START, as (subscription, poll)
        # pairs from the same map moment: the cleanup below waits on,
        # cancels and releases exactly these, whatever the maps say by then
        # — a re-attach cannot take the old poll's source away, and the new
        # source's poll is not in the snapshot to begin with (FB-56).
        targets = [
            (seat, subscription, self.tasks.get(seat))
            for seat, subscription in self.subscriptions.items()
        ]
        for seat, subscription, _poll in targets:
            async with attachments.lock(seat):
                # Identity, not position: clear this seat's CURRENT state
                # only while it is still the instance this stop took. A
                # newer attach is a complete swap and owns everything from
                # here on — and the registry note is part of that state:
                # clearing it before the identity check would wipe the NEW
                # attachment's entry, and its events would then be refused
                # as source-less.
                if self.subscriptions.get(seat) is subscription:
                    attachments.note(seat, None)
                    self.subscriptions.pop(seat, None)
                    self.tasks.pop(seat, None)
                    self.answering.discard(seat)
                    for held in (
                        self.live,
                        self.work,
                        self.woken,
                        self.clocks,
                        self.unreachable,
                        self.told_waiting,
                    ):
                        held.pop(seat, None)
            self._wake(seat)
            subscription.unpark()
        pending = [
            poll for _, _, poll in targets if poll is not None and not poll.done()
        ]
        if pending:
            _, stuck = await asyncio.wait(pending, timeout=5)
            for task in stuck:
                task.cancel()
            await asyncio.gather(*stuck, return_exceptions=True)
        await asyncio.gather(*(each.release() for _, each, _ in targets))

    async def close(self, session: SessionRef) -> None:
        seat = self._seat_of(session)
        if subscription := self.subscriptions.get(seat):
            # Nothing more is read from a runner that is not there; what it
            # left is read when its session is next started.
            with contextlib.suppress(DeviceCallError, DeviceOffline):
                await subscription.drain()
        await self._detach(seat)

    async def recover(self, device_id=None) -> list[SessionRef]:
        # These answer for THIS round only — a conversation the last round
        # reached says nothing about this one. (The cumulative witness is
        # `dead`: a death once seen stays seen.)
        self.found_conversations.clear()
        self.terminal_conversations.clear()
        handles = await self.channel.discover(device_id)
        # The channel's own per-conversation observations (FB-56 legacy③):
        # a terminal answer bound to the stored resume token is death;
        # anything the channel cannot place stays unknown by absence here.
        # The probe is structural: a channel that cannot report outcomes —
        # including one that answers every attribute — has none, and none
        # means unknown, never dead.
        outcomes = getattr(self.channel, "last_outcomes", None)
        if not isinstance(outcomes, dict):
            outcomes = {}
        for (room_id, agent, resume_token), outcome in outcomes.items():
            if outcome == "dead":
                self.terminal_conversations.add(((room_id, agent), resume_token))
        recovered = []
        for handle in handles:
            try:
                async with asyncio.timeout(15):
                    status = await self.channel.call(handle, "ping", {})
            except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
                self.logger.warning(
                    "%s recovery failed topic=%s device=%s: %s",
                    self.label,
                    handle.session.topic_id,
                    handle.device_id,
                    exc,
                )
                continue
            self.found_conversations.add(
                (self._seat_of_handle(handle), self.conversation(handle))
            )
            try:
                await self._attach(handle)
            except DeviceOffline:
                continue
            except RunnerUnsupported as exc:
                self.logger.warning(
                    "%s recovery refused topic=%s: %s",
                    self.label,
                    handle.session.topic_id,
                    exc,
                )
                continue
            if self.working(status) and status.get("work_id"):
                seat = self._seat_of_handle(handle)
                self.work[seat] = uuid.UUID(status["work_id"])
                now = time.monotonic()
                self.clocks[seat] = Clock(opened=now, progressed=now)
            recovered.append(handle.session)
        # Chat restores room bookkeeping before replay starts consumption.
        return recovered

    async def replay(self, session: SessionRef, *, known_texts: set[str]) -> None:
        seat = self._seat_of(session)
        if subscription := self.subscriptions.get(seat):
            await subscription.reconcile_history()
            await subscription.drain()
            self._listen(seat)

    async def run_turn(
        self,
        *,
        project_id,
        topic_id,
        prompt,
        system_prompt,
        resume_session_id,
        register_input: InputRegistrar,
        model=None,
        env=None,
        memory_scope=None,
        owner=None,
        turn_id=None,
        images=None,
        agent_handle=None,
        session_agent: str,
    ) -> AsyncIterator[AgentEvent]:
        if topic_id is None:
            yield AgentResult(
                text=f"A {self.label} session requires a room",
                session_id=None,
                is_error=True,
            )
            return
        work = turn_id or uuid.uuid4()
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self.queues[work] = queue
        try:
            await self.send(
                SessionRef(project_id, topic_id, session_agent, harness=self.harness),
                prompt,
                Opening(
                    system_prompt=system_prompt,
                    resume_token=resume_session_id,
                    model=model,
                    env=env,
                    memory_scope=memory_scope,
                    owner=owner,
                    agent_handle=agent_handle,
                    # 平台自己起的那几轮走这条入口，今天照旧租手：它们跑在项目工
                    # 作机的根话题沙箱里。写出来是为了让这条老路看得见，改不改是
                    # 另一件事 (P21 只动房间里的那一轮)。
                    needs_place=True,
                ),
                work_id=work,
                on_mark=lambda _: None,
                register_input=register_input,
                images=images,
            )
            while True:
                event = await queue.get()
                yield event
                if isinstance(event, AgentResult):
                    return
        finally:
            self.queues.pop(work, None)
