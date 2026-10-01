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
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import (
    ActivityConsumer,
    Backlog,
    CompletionConsumer,
    EventConsumer,
    MemoryConsumer,
    Opening,
    ReachabilityConsumer,
    ReceiptConsumer,
    SessionRef,
    UnreadProbe,
)
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
from app.domain.agent.service import AgentEvent, AgentResult, AgentSessionInfo
from app.domain.delivery.input_identity import (
    InputIdentity,
    InputOutcomeUnconfirmed,
    InputReceipt,
    InputRegistrar,
)

# Every read of a room's journal is a call to its device, and an idle room
# answers it with nothing. Read at the floor while there is anything to read;
# let the wait grow towards the ceiling once the journal has gone quiet.
READ_FLOOR_S = 0.1
READ_CEILING_S = 1.0
# How long a runner may go unanswered while a turn is open before the turn is
# called dead. Longer than the connection owner takes to come back after a
# release, and than a device takes to reconnect after a network blip: those
# are waited out, and a runner that is still there answers again.
RUNNER_GONE_S = 120.0
# Output this recent means the session is talking right now. Talking without a
# tool call or an ending for ``no_progress_s`` is a loop; a session that has
# gone quiet is judged by whether its process is alive, not by this.
TALKING_S = 300.0


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


class SessionChannel[H: Handle](Protocol):
    name: str
    provisions_machine: bool
    deferred_work: bool
    builds_model_env: bool

    def available(self) -> bool: ...

    async def prepare_topic(self, **kwargs) -> tuple[bool, str]: ...

    async def ensure(self, session: SessionRef, opening: Opening) -> H: ...

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
        self.subscriptions: dict[Seat, Subscription] = {}
        self.tasks: dict[Seat, asyncio.Task] = {}
        self.work: dict[Seat, uuid.UUID] = {}
        self.queues: dict[uuid.UUID, asyncio.Queue[AgentEvent]] = {}
        self.woken: dict[Seat, asyncio.Event] = {}
        self.consumer: EventConsumer | None = None
        self.activity: ActivityConsumer | None = None
        self.receipts: ReceiptConsumer | None = None
        self.completions: CompletionConsumer | None = None
        self.reachability: ReachabilityConsumer | None = None
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

    def bind_events(self, consumer: EventConsumer) -> None:
        self.consumer = consumer

    def bind_activity(self, consumer: ActivityConsumer) -> None:
        self.activity = consumer

    def bind_receipts(self, consumer: ReceiptConsumer) -> None:
        self.receipts = consumer

    def bind_completions(self, consumer: CompletionConsumer) -> None:
        self.completions = consumer

    def bind_unread_probe(self, probe: UnreadProbe) -> None:
        self.unread = probe

    def bind_reachability(self, consumer: ReachabilityConsumer) -> None:
        self.reachability = consumer

    def bind_memory(self, consumer: MemoryConsumer) -> None:
        self._memory = consumer

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
        elif self.consumer:
            await self.consumer(project, topic, work, event, eid, seen, unsolicited)
        else:
            raise RuntimeError(f"{self.label} room persistence is not bound")

    async def _activity(self, project, seat: Seat, work, active):
        if work in self.closed:
            return
        if active:
            self.work[seat] = work
            now = time.monotonic()
            self.clocks[seat] = Clock(opened=now, progressed=now)
        elif self.work.get(seat) == work:
            self.work.pop(seat, None)
            self.clocks.pop(seat, None)
        if self.activity and work not in self.queues:
            await self.activity(project, seat[0], work, active, agent_handle=seat[1])

    async def _attach(self, handle: H) -> None:
        seat = self._seat_of_handle(handle)
        if self.live.get(seat) == handle and seat in self.subscriptions:
            return
        await self._detach(seat)
        handle.mirror.parent.mkdir(parents=True, exist_ok=True)

        async def call(method: str, params: dict) -> dict:
            return await self.channel.call(handle, method, params)

        self.live[seat] = handle
        self.subscriptions[seat] = self.subscribe(handle, call)

    def _listen(self, seat: Seat) -> None:
        if seat not in self.tasks or self.tasks[seat].done():
            self.tasks[seat] = asyncio.create_task(
                self._poll(seat), name=f"{self.records} {seat[0]}/{seat[1]}"
            )

    def _wake(self, seat: Seat) -> None:
        """Cut short the wait of a seat that has just been given something."""
        if event := self.woken.get(seat):
            event.set()

    async def _wait(self, seat: Seat, delay: float) -> None:
        event = self.woken.setdefault(seat, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), delay)
        except TimeoutError:
            return
        event.clear()

    async def _poll(self, seat: Seat) -> None:
        topic = seat[0]
        checked_at = 0.0
        # Waiting for a device to come back is this loop's job, not a failure of
        # it, so the wait is said once and the return is said once. Per-task
        # state: one of these runs per seat.
        waiting = False
        delay = READ_FLOOR_S
        while seat in self.subscriptions:
            try:
                delivered = await self.subscriptions[seat].drain()
                self.unreachable.pop(seat, None)
                if seat in self.work and time.monotonic() - checked_at >= 1:
                    handle = self.live[seat]
                    status = await self.channel.call(handle, "ping", {})
                    checked_at = time.monotonic()
                    if not status.get("alive", True):
                        await self._died(handle)
                        return
                    if verdict := self.verdict(seat):
                        await self._end_by_verdict(handle, verdict)
            except DeviceOffline:
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
                # The machine is there and said no — the runner's socket is not
                # up yet (a cold one can take about a minute) or its home is gone.
                # Either way the next read is what tells, and the machine's
                # own words are the fact worth writing down, once.
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
                # A room being worked reads at the floor, and so does one whose
                # journal just gave us something — the next record of a stream
                # is due immediately. A room nobody is talking to costs a call
                # every 100ms for an empty page, and the cost is per room:
                # eleven of them idling held a core between them. Sending wakes
                # the wait, so nothing a person does is served at the
                # backed-off rate.
                if delivered or seat in self.work:
                    delay = READ_FLOOR_S
                else:
                    delay = min(delay * 2, READ_CEILING_S)
                await self._wait(seat, delay)

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
        if self.reachability:
            await self.reachability(
                handle.session.project_id, seat[0], work, False, reason
            )

    async def _say_resumed(self, seat: Seat) -> None:
        work = self.told_waiting.pop(seat, None)
        handle = self.live.get(seat)
        if work is None or handle is None or self.work.get(seat) != work:
            return
        if self.reachability:
            await self.reachability(handle.session.project_id, seat[0], work, True, "")

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
                    f"{self.label} 的运行程序连续 {RUNNER_GONE_S:.0f} 秒没有应答"
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

    async def ensure(self, session, opening, *, work_id=None) -> H:
        seat = self._seat_of(session)
        previous = self.live.get(seat)
        if previous and opening.agent_handle != previous.agent_handle:
            # A different teammate is taking this seat over; the conversation
            # that belonged to the last one does not carry over to them. Other
            # seats in the same room are not this call's business.
            await self.interrupt(session)
            await self.close(session)
        handle = await self.channel.ensure(session, opening)
        await self._attach(handle)
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
        self.work[self._seat_of(session)] = work_id
        self._wake(self._seat_of(session))
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
            if self.receipts is None:
                raise RuntimeError("Receipt consumer is not bound")
            await self.receipts(InputReceipt(identity, "accepted"))
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
        self.live.pop(seat, None)
        self.work.pop(seat, None)
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
        """
        subscriptions = dict(self.subscriptions)
        self.subscriptions.clear()
        for seat in subscriptions:
            self._wake(seat)
        polls = [task for task in self.tasks.values() if not task.done()]
        if polls:
            _, stuck = await asyncio.wait(polls, timeout=5)
            for task in stuck:
                task.cancel()
            await asyncio.gather(*stuck, return_exceptions=True)
        await asyncio.gather(*(each.release() for each in subscriptions.values()))
        for held in (self.tasks, self.live, self.work, self.woken, self.clocks):
            held.clear()
        self.unreachable.clear()
        self.told_waiting.clear()

    async def close(self, session: SessionRef) -> None:
        seat = self._seat_of(session)
        if subscription := self.subscriptions.get(seat):
            await subscription.drain()
        await self._detach(seat)

    async def recover(self, device_id=None) -> list[SessionRef]:
        handles = await self.channel.discover(device_id)
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
            await self._attach(handle)
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
                    system_prompt,
                    resume_session_id,
                    model,
                    env,
                    memory_scope,
                    owner,
                    agent_handle,
                    # 平台自己起的那几轮走这条入口，今天照旧租手：它们跑在项目工
                    # 作机的根话题沙箱里。写出来是为了让这条老路看得见，改不改是
                    # 另一件事 (P21 只动房间里的那一轮)。
                    needs_place=True,
                ),
                work_id=work,
                on_mark=lambda _: None,
                images=images,
            )
            while True:
                event = await queue.get()
                yield event
                if isinstance(event, AgentResult):
                    return
        finally:
            self.queues.pop(work, None)
