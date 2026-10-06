"""A room's sessions on one machine pool, for one harness, as the room keeps them.

The session core (`session_host`) starts, talks to and reads every session;
this keeps the room's books on them. A room seats one session per teammate
(``Seat`` = topic, agent), and each seat has the work it is running, the inputs
said to it mid-work and which work read them, and the clocks its liveness rules
read. One reader per seat (``_poll``) hands each item the core reads to the
room (``RoomReader``), after these books have taken it.

The work does not live here. A session runs in a runner on the session host
that outlives this process, and ``recover`` finds it again after a restart.

What ends work the session did not end itself is decided here and in the core,
the same way for every harness (``docs/agent-liveness.md``): the core says the
session is gone (its process exited, or its runner was out of reach for
``RUNNER_GONE_S`` while work was owed); here, the session has been talking
without working for ``no_progress_s``, or something said to it has gone unread
for ``unread_grace_s``.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent import attachments, machine_launcher
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.harness import HARNESSES, SessionRef
from app.domain.agent.harness.channel import Placement, mint_session_token
from app.domain.agent.harness.driven.subscription import (
    OUTPUT,
    PROGRESS,
    TOOL_RETURNED,
    TOOL_STARTED,
    Seat,
)
from app.domain.agent.harness.prompt import PLATFORM_NOTICE
from app.domain.agent.place import seat_key
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_CODE,
    PROMPT_UNDELIVERED_MESSAGE,
    TURN_TIMEOUT_CODE,
    TURN_TIMEOUT_MESSAGE,
)
from app.domain.agent.reads import (
    CaughtUp,
    Completed,
    ControlsMoved,
    Ended,
    Moved,
    Reachable,
    Read,
    Received,
    Terminated,
    Working,
    Writing,
)
from app.domain.agent.room.stopwatch import Stopwatch, timed
from app.domain.agent.service import AgentEvent, AgentResult, AgentSessionInfo
from app.domain.agent.session_host.contract import (
    Access,
    Image,
    InputProtocolUnavailable,
    InputUnconfirmed,
    Owner,
    Prompt,
    SessionError,
    SessionSpec,
)
from app.domain.agent.session_host.contract import SessionRef as CoreRef
from app.domain.agent.session_host.host import RunnerUnsupported, SessionHost
from app.domain.delivery.input_identity import (
    InputIdentity,
    InputNotSent,
    InputOutcomeUnconfirmed,
    InputReceipt,
    InputRegistrar,
    WorkCompletion,
    WorkTermination,
)
from app.domain.library import service as library
from app.domain.project_skill.service import session_skill_files

if TYPE_CHECKING:
    from app.domain.agent.central_provider import CentralChannel
    from app.domain.agent.room.reads import RoomReader

logger = logging.getLogger(__name__)

# (topic) — lay this room's memory tree down in its session, and take back what
# the agent wrote into it. Asked at two moments, and both ask the same question:
# just before an input goes in (so the session reads the platform's version)
# and just after a turn ends (so what it wrote comes back in the turn it was
# written in). It takes only the topic because everything else it needs — the
# project, who is speaking, the reach to the session — lives on the side that
# owns the room (`chat.ChatService`).
MemoryConsumer = Callable[[uuid.UUID], Awaitable[None]]

# (topic) → the loop-clock reading at which the OLDEST message we injected and
# have not seen consumed was written, or None when nothing is waiting.
#
# A receipt (``Received``) answers "did this one land"; this answers "is anything still
# unanswered, and since when". A session that has stopped reading its input can
# go on producing output indefinitely, so nothing else in the liveness picture
# notices it: the hooks keep arriving and the screen stays alive. What it cannot
# do is take the next thing somebody typed, and that is a failure with a person
# on the other end of it.
UnreadProbe = Callable[[uuid.UUID], float | None]

# A seat whose session went quiet while it may not be what the backend would
# start today (``prewarm_due``): the room brings it up to date then, so the
# restart is not paid by the next person's message.
QuietListener = Callable[[SessionRef], None]

# How long a session's runner may go unanswered while work is owed before the
# work is called dead. Longer than the connection owner takes to come back after
# a release, and than a device takes to reconnect after a network blip: those
# are waited out, and a runner that is still there answers again.
RUNNER_GONE_S = 120.0
# How long a reader waits for the next item before reading a seat's clocks.
CLOCK_READ_S = 25.0
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


@dataclass(frozen=True)
class Live:
    """A seat's session, as this process started or found it."""

    #: The conversation's key in the room (``SessionRef``): what its row,
    #: its resume token and every ref the room builds are written under.
    session: SessionRef
    #: The session in the core.
    ref: CoreRef
    #: The agent it acts as: the teammate a room addressed by instance acts
    #: under the handle its credential names.
    acting: str
    #: The harness's own id for the conversation.
    conversation: str
    takes_inputs: bool = True
    #: Which reading of the seat its events come from (FB-56): a reading
    #: that was replaced is refused where the room's books change.
    attachment: str = field(default="", compare=False)


def _home(state: str) -> str:
    """A state directory as the core names it: under the host's ``~/.cheese``."""
    return state.removeprefix("$HOME/.cheese/")


class RoomSessions:
    """One harness's sessions on one machine pool, for every room."""

    #: The pool's backends all carry the pictures with the words.
    embeds_images = True

    def __init__(
        self,
        channel: "CentralChannel",
        harness: str,
        host: SessionHost,
        *,
        hard_ceiling_s: float = 900,
        no_progress_s: float = 0.0,
        unread_grace_s: float = 0.0,
    ):
        self.channel = channel
        self.harness = harness
        self.host = host
        self.hard_ceiling_s = hard_ceiling_s
        self.no_progress_s = no_progress_s
        self.unread_grace_s = unread_grace_s
        entry = HARNESSES.get(harness)
        self.label = entry.label if entry else harness
        self.controls: tuple[str, ...] = entry.controls if entry else ()
        self.executor_controls: frozenset[str] = (
            entry.executor_controls if entry else frozenset()
        )
        # 一间房给每个 agent 各摆一个座位（Seat = (topic, agent_handle)）：会话、
        # 读它的那一路、在跑的活和它的钟都按座位键住。同一间房里另一个 agent 的
        # 会话与这里互不相干——它开它的轮、它等它的设备，谁也不顶掉谁。
        self.live: dict[Seat, Live] = {}
        self.work: dict[Seat, uuid.UUID] = {}
        self.clocks: dict[Seat, Clock] = {}
        # Work a verdict already ended. What the session goes on saying still
        # lands; a second ending for it does not.
        self.closed: set[uuid.UUID] = set()
        # The open work each seat was last told is waiting on its machine.
        self.told_waiting: dict[Seat, uuid.UUID] = {}
        # The conversation each seat was last handed its opening state in.
        self.opened: dict[Seat, str] = {}
        # (seat, conversation) pairs this process saw die (`_died` / a
        # verdict): the only thing that makes "no live session" a fact rather
        # than an unanswered question (FB-56 legacy③). A turn on the same seat
        # in a DIFFERENT conversation is not covered by it.
        self.dead: set[tuple[Seat, str]] = set()
        # Conversations this round's recovery found alive, and found
        # TERMINATED by the runner's own per-conversation answer (FB-56
        # legacy③): only a terminal answer bound to the stored resume token
        # lands here. "Not heard from" never does — that is unknown.
        self.found_conversations: set[tuple[Seat, str]] = set()
        self.terminal_conversations: set[tuple[Seat, str]] = set()
        self._owns_sessions_provider = None
        self.tasks: dict[Seat, asyncio.Task] = {}
        # Each seat's reading caught up with what its session had written.
        self.caught: dict[Seat, asyncio.Event] = {}
        # The platform's own work (``reading``) reads its events here rather
        # than the room hearing them.
        self.queues: dict[uuid.UUID, asyncio.Queue[AgentEvent]] = {}
        #: Where the room hears what its sessions say and do.
        self.reader: RoomReader | None = None
        self.unread: UnreadProbe | None = None
        self._memory: MemoryConsumer | None = None
        self._quiet: QuietListener | None = None
        # Seats this process took over and has not yet brought up to date with
        # what it would start today: a release changes what a session is
        # launched with, and no turn here has compared it yet.
        self.unchecked: set[Seat] = set()

    # --- the machine pool ----------------------------------------------------

    @property
    def name(self) -> str:
        return self.channel.name

    @property
    def deferred_work(self) -> bool:
        return self.channel.deferred_work

    @property
    def builds_model_env(self) -> bool:
        return self.channel.builds_model_env

    def available(self) -> bool:
        return self.channel.available()

    def report_to(
        self,
        reader: "RoomReader",
        *,
        unread: "UnreadProbe",
        memory: "MemoryConsumer",
        quiet: "QuietListener | None" = None,
    ) -> None:
        """The room's books: where it hears its sessions, where it says what
        it sent that is still unread, where its memory is reconciled, and who
        brings a seat that went quiet up to date (``prewarm_due``)."""
        self.reader, self.unread, self._memory = reader, unread, memory
        self._quiet = quiet

    def prewarm_due(self, session: SessionRef) -> "Live | None":
        """The seat's live session, when the next message would have to start
        it again and nothing is running on it; else None.

        Owed after this process took the seat over, until a start compares it
        (``unchecked``); whenever the channel put a relaunch off because the
        session was working (``owes``); and once its runner stopped answering,
        which is what letting an idle session go looks like from here. Only the
        process that owns the running work answers yes: the one handing it over
        must not start what the next one is about to read."""
        seat = self._seat_of(session)
        live = self.live.get(seat)
        if live is None or not live.takes_inputs or seat in self.work:
            return None
        provider = self._owns_sessions_provider
        if provider is not None and not provider().owns_sessions:
            return None
        ledger = getattr(self.channel, "screen_ledger", None)
        if (
            seat in self.unchecked
            or (ledger is not None and ledger.owes((seat[0], live.acting)))
            or self.host.answers(live.ref) is False
        ):
            return live
        return None

    def _went_quiet(self, session: SessionRef) -> None:
        if self._quiet is not None and self.prewarm_due(session) is not None:
            self._quiet(session)

    def bind_owns_sessions(self, provider) -> None:
        """Who answers ``owns_sessions`` at attach time (FB-56)."""
        self._owns_sessions_provider = provider

    # --- hearing -------------------------------------------------------------

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
            self._room(identity.project_id, identity.conversation_id),
            Read(str(identity.work_id), Received(receipt)),
            required=True,
        )

    async def _hear_completion(self, completion: WorkCompletion) -> None:
        await self._hear(
            self._room(completion.project_id, completion.conversation_id),
            Read(str(completion.work_id), Completed(completion)),
            required=True,
        )

    async def _hear_termination(self, termination: WorkTermination) -> None:
        await self._hear(
            self._room(termination.project_id, termination.conversation_id),
            Read(str(termination.work_id), Terminated(termination)),
            required=True,
        )

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
            logger.exception("memory reconciliation failed topic=%s", topic)

    async def memory(self, topic_id: uuid.UUID, request: dict) -> dict | None:
        """One memory reconciliation, with the session the room's only live seat
        holds. ``None`` is 「这里没有记忆文件」 — no live session, or a harness
        whose sessions keep none — which the caller reads as 「这一轮不用对账」,
        not as a failure."""
        seat = self._room_seat(topic_id)
        live = self.live.get(seat) if seat is not None else None
        if live is None:
            return None
        return await self.host.memory(live.ref, request)

    def _room_seat(self, topic_id: uuid.UUID) -> Seat | None:
        """The room's only live seat, or None when there is none — or several.

        Room-scoped questions (the controls relay, the memory relay) predate
        seats; with two teammates live in one room they have no single answer,
        and None makes the caller say so rather than pick a teammate at random.
        """
        seats = [seat for seat in self.live if seat[0] == topic_id]
        return seats[0] if len(seats) == 1 else None

    # --- the room's books ----------------------------------------------------

    def pulse(self, seat: Seat, marks: frozenset[str]) -> None:
        """What a record just said about the seat's open turn."""
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
            logger.warning(
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
                    logger.warning(
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

    async def _end_by_verdict(self, live: Live, result: AgentResult) -> None:
        """End the turn in the room's books, and take the work away."""
        seat = self._seat_of(live.session)
        work = self.work[seat]
        await self._consume(
            live.session.project_id,
            seat[0],
            work,
            AgentResult(
                text=result.text,
                session_id=live.conversation,
                is_error=result.is_error,
                failure_code=result.failure_code,
                agent_handle=live.acting,
                harness=self.harness,
            ),
            f"{self.harness}:{live.conversation}:verdict:{work}",
            False,
            False,
        )
        await self._activity(live.session.project_id, seat, work, False)
        self.closed.add(work)
        await self.host.stop(live.ref)

    async def _consume(self, project, topic, work, event, eid, seen, unsolicited):
        if isinstance(event, AgentResult) and work in self.closed:
            return
        if queue := self.queues.get(work):
            await queue.put(event)
        else:
            await self._hear(
                self._room(project, topic),
                Read(
                    str(work),
                    event,
                    eid=eid,
                    text_seen=seen,
                    unsolicited=unsolicited,
                ),
                required=True,
            )

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
        if work not in self.queues:
            await self._hear(
                self._room(project, seat[0], seat[1]), Read(str(work), Working(active))
            )

    # --- reading -------------------------------------------------------------

    @staticmethod
    def _seat_of(session: SessionRef) -> Seat:
        """The seat a session ref names: (conversation, agent)."""
        return (session.conversation_id, session.agent_handle)

    async def _attach(self, live: Live) -> Live:
        """Make ``live`` the seat's session, read from now on by one reading
        whose events are the only ones the room's books take (FB-56)."""
        seat = self._seat_of(live.session)
        async with attachments.lock(seat):
            # Ownership is checked FIRST, and the idempotent fast path is
            # checked INSIDE the lock with it (FB-56): an early return ahead
            # of both would wave a same-session recover through while the
            # platform is not owning sessions — the old maps still answer
            # "attached", and the caller goes on bookkeeping a recovery the
            # ownership gate never allowed.
            self._owning(seat)
            current = self.live.get(seat)
            if (
                current is not None
                and current == live
                and seat in self.tasks
                and not self.tasks[seat].done()
            ):
                return current
        await self._detach(seat)
        attached = Live(
            live.session,
            live.ref,
            live.acting,
            live.conversation,
            live.takes_inputs,
            attachment=uuid.uuid4().hex,
        )
        async with attachments.lock(seat):
            # The flag is checked HERE, under the lock — not once at some
            # route's entry (FB-56): while a stop is being taken, an attach
            # can land between the flag flip and the swap, and only the lock
            # makes the order real. A refused attach registers nothing.
            self._owning(seat)
            self.live[seat] = attached
            self.dead = {pair for pair in self.dead if pair[0] != seat}
            attachments.note(seat, attached.attachment)
        return attached

    def _owning(self, seat: Seat) -> None:
        if self._owns_sessions_provider is not None and (
            not self._owns_sessions_provider().owns_sessions
        ):
            raise DeviceOffline(
                f"platform is not owning sessions right now (topic={seat[0]})"
            )

    def _listen(self, seat: Seat, *, recovered: bool = False) -> None:
        live = self.live.get(seat)
        if live is None:
            return
        if seat not in self.tasks or self.tasks[seat].done():
            self.caught[seat] = asyncio.Event()
            self.tasks[seat] = asyncio.create_task(
                self._poll(seat, live, recovered, self.caught[seat]),
                name=f"{self.label} reading {seat[0]}/{seat[1]}",
            )

    def _clock_read_s(self, seat: Seat) -> float:
        """How long a reader waits for the next item before reading the seat's
        clocks: never so long that a turn's own clocks (``verdict``) are read
        late by more than a fraction of them."""
        if seat not in self.work:
            return CLOCK_READ_S
        clocks = [s / 4 for s in (self.no_progress_s, self.unread_grace_s) if s > 0]
        return min([CLOCK_READ_S, *clocks])

    async def _poll(
        self, seat: Seat, live: Live, recovered: bool, caught: asyncio.Event
    ) -> None:
        reading = self.host.read(live.ref, recovered=recovered)
        pending: asyncio.Future | None = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(anext(reading))
                done, _ = await asyncio.wait(
                    {pending}, timeout=self._clock_read_s(seat)
                )
                if done:
                    try:
                        read = pending.result()
                    except StopAsyncIteration:
                        return
                    pending = None
                    try:
                        if await self._take(seat, live, read, caught):
                            return
                    except Exception as exc:
                        # The core reads it again, and steps over one the room
                        # goes on refusing.
                        pending = asyncio.ensure_future(reading.athrow(exc))
                        continue
                if seat in self.work and self.live.get(seat) is live:
                    if verdict := self.verdict(seat):
                        await self._end_by_verdict(live, verdict)
        finally:
            caught.set()
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await reading.aclose()

    async def _take(
        self, seat: Seat, live: Live, read: Read, caught: asyncio.Event
    ) -> bool:
        """Keep the room's books on one item its session's reading handed over,
        and hand it on to the room. True when the reading is over."""
        project, topic = live.session.project_id, seat[0]
        event = read.event
        work = uuid.UUID(read.work_id) if read.work_id else None
        if isinstance(event, Working):
            assert work is not None
            await self._activity(project, seat, work, event.active)
            if not event.active:
                # 一轮结束时问一次记忆：agent 该写的记忆按规矩写在回复之前，所
                # 以一轮读完就是它写完的时刻。
                await self.reconcile_memory(topic)
                self._went_quiet(live.session)
        elif isinstance(event, Moved):
            self.pulse(seat, event.marks)
        elif isinstance(event, Received):
            await self._hear_receipt(event.receipt)
        elif isinstance(event, Completed):
            await self._hear_completion(event.completion)
        elif isinstance(event, Terminated):
            await self._hear_termination(event.termination)
        elif isinstance(event, Writing):
            # What the agent is in the middle of writing, under its own name.
            work = work or self.work.get(seat)
            await self._hear(
                live.session,
                Read(
                    str(work) if work else None,
                    Writing(event.blocks, author=live.acting),
                ),
            )
        elif isinstance(event, Reachable):
            if event.yes:
                await self._say_resumed(seat)
            else:
                await self._say_waiting(seat, event.reason)
        elif isinstance(event, Ended):
            if seat in self.work and self.live.get(seat) is live:
                await self._died(live, out_of_reach=event.reason == "out of reach")
            return True
        elif isinstance(event, CaughtUp):
            caught.set()
        elif isinstance(event, ControlsMoved):
            await self.announce(topic)
            # A background task finishing is what lets a relaunch put off for
            # it happen; the controls moving is how that is heard.
            self._went_quiet(live.session)
        else:
            assert work is not None, "a session's events belong to work"
            if hasattr(event, "attachment"):
                # Which reading this came from (FB-56): a replaced one's is
                # refused where the room's books change.
                event.attachment = live.attachment  # type: ignore[union-attr]
            await self._consume(
                project,
                topic,
                work,
                event,
                read.eid,
                read.text_seen,
                read.unsolicited,
            )
        return False

    async def _say_waiting(self, seat: Seat, reason: str) -> None:
        """Tell the room this seat's open turn is waiting on the machine — once
        per turn per outage. A seat with no turn open is not waiting for
        anything: its machine being off is the ordinary state of a platform
        nobody is using."""
        work = self.work.get(seat)
        live = self.live.get(seat)
        if work is None or live is None or self.told_waiting.get(seat) == work:
            return
        self.told_waiting[seat] = work
        await self._hear(live.session, Read(str(work), Reachable(False, reason)))

    async def _say_resumed(self, seat: Seat) -> None:
        work = self.told_waiting.pop(seat, None)
        live = self.live.get(seat)
        if work is None or live is None or self.work.get(seat) != work:
            return
        await self._hear(live.session, Read(str(work), Reachable(True)))

    async def _died(self, live: Live, *, out_of_reach: bool = False) -> None:
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
        seat = self._seat_of(live.session)
        work = self.work[seat]
        await self._consume(
            live.session.project_id,
            seat[0],
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
                session_id=live.conversation,
                is_error=True,
                agent_handle=live.acting,
                harness=self.harness,
            ),
            f"{self.harness}:{live.conversation}:exit:{work}",
            False,
            False,
        )
        await self._activity(live.session.project_id, seat, work, False)
        self.closed.add(work)
        self.live.pop(seat, None)
        if not out_of_reach:
            # A verdict/exit the runner itself reported is death. Merely
            # being out of reach is not — a machine still being prepared
            # looks exactly the same, and that one stays "unknown".
            self.dead.add((seat, live.conversation))

    # --- starting and speaking -----------------------------------------------

    async def ensure(
        self,
        session: SessionRef,
        *,
        system_prompt: str,
        resume_token: str | None = None,
        model: str | None = None,
        env: dict[str, str] | None = None,
        acting: str | None = None,
        needs_place: bool = True,
        reads_only: bool = False,
        phases: dict[str, float] | None = None,
    ) -> Live:
        """The seat's session, started where the room's placement puts it if it
        has to be. ``reads_only``: a task's session before its owner starts it,
        which may read the machine and change nothing on it. ``phases``, when
        given, receives how long each step took (`send` logs it)."""
        mark = Stopwatch(phases)
        seat = self._seat_of(session)
        previous = self.live.get(seat)
        if previous is not None and not previous.takes_inputs:
            # An adopted runner from before the receipt protocol owns native
            # pipes and may still hold the original executor's work; it is not
            # relaunched to upgrade it.
            raise InputProtocolUnavailable()
        if previous is not None and acting is not None and acting != previous.acting:
            # A different teammate is taking this seat over; the conversation
            # that belonged to the last one does not carry over to them. Other
            # seats in the same room are not this call's business.
            await self.interrupt(session)
            await self.close(session)
        mark("ensure_close")
        precheck = await self.channel.precheck(session, needs_place=needs_place)
        assert isinstance(precheck, Placement)
        mark("ensure_precheck")
        # WHO acts with it. The caller pins a teammate when a message named one;
        # unnamed, it is the agent the machine resolver resolved for this room.
        # The room itself never answers: it may seat several agents, and a name
        # signed into a token cannot be taken back.
        agent = acting or precheck.agent_handle
        placed: dict = {}

        # A task's or a 支线's session is a conversation of its own beside the
        # room's, for the same agent: its state and every file it starts from
        # live apart from the room seat's.
        state_key = seat_key(agent, session.inner_id)

        def place(resource) -> dict:
            placed.update(
                harness=self.harness,
                agent_handle=agent,
                state=machine_launcher.state_dir(
                    session.project_id, resource, self.harness, state_key
                ),
            )
            return placed

        async with self.channel.prepare_session(
            session=session,
            token=mint_session_token(
                session.project_id, session.conversation_id, agent
            ),
            env=env,
            precheck=precheck,
            runtime_factory=place,
            reading=reads_only,
        ) as prepared:
            ref = CoreRef(self.harness, _home(placed["state"]))
            spec = SessionSpec(
                system_prompt=system_prompt,
                model=model or settings.agent_model,
                gone_after_s=RUNNER_GONE_S,
                resume_token=resume_token,
                env={
                    **prepared.env,
                    "CHEESE_PROJECT": str(session.project_id),
                    # The conversation: a room, or the task this session works.
                    "CHEESE_TOPIC": str(session.conversation_id),
                    "CHEESE_AUTHOR": prepared.agent_handle,
                    # Part of the launch, so starting the task relaunches
                    # an idle session with a credential that may write.
                    **(
                        {"CHEESE_TASK_READS_ONLY": "1" if reads_only else "0"}
                        if session.inner_id is not None
                        else {}
                    ),
                },
                acting=prepared.agent_handle,
                skills=session_skill_files(session.project_id),
                # The marker platform instructions carry in this room, so the
                # one a harness raises is not a second convention to learn.
                notice=PLATFORM_NOTICE,
            )
            access = Access(
                prepared.token,
                json.loads(prepared.env["CHEESE_EXECUTION_TARGET"]),
                host=prepared.device_id,
                owner=Owner(
                    session.project_id,
                    session.conversation_id,
                    prepared.env["CHEESE_RESOURCE_ID"],
                    session.agent_handle,
                    prepared.agent_handle,
                    prepared.agent_user_id,
                    seat=state_key,
                ),
            )
            mark("ensure_prepare")
            status = await self.host.start(ref, spec, access)
            mark("ensure_start")
        attached = await self._attach(
            Live(
                session,
                ref,
                prepared.agent_handle,
                status.conversation,
                status.takes_inputs,
            )
        )
        mark("ensure_attach")
        # Compared with what it would be started with now: up to date, or a
        # relaunch the channel owes (``prewarm_due``).
        self.unchecked.discard(seat)
        return attached

    async def send(
        self,
        session: SessionRef,
        message: str,
        *,
        system_prompt: str,
        work_id: uuid.UUID,
        on_mark: Callable[[uuid.UUID], None],
        register_input: InputRegistrar,
        resume_token: str | None = None,
        expected_native_session: str | None = None,
        model: str | None = None,
        env: dict[str, str] | None = None,
        acting: str | None = None,
        needs_place: bool = True,
        reads_only: bool = False,
        images: list[dict] | None = None,
        owes_reply: bool = False,
        session_opening: str = "",
        opening_changes: str = "",
    ) -> bool:
        """Put a message into the seat's session, as work ``work_id``, starting
        the session if it has to be. True = the session's runner took it.

        ``session_opening`` is the project state a new conversation is handed in
        front of its first message; ``opening_changes`` the part of it that
        changed since a conversation that goes on last heard (`_with_project_state`).

        An ack, not an answer: what the agent does about it arrives through the
        seat's reading — possibly minutes later, possibly to a different process
        than the one that sent this. ``expected_native_session`` is an Ask's
        answer: it may go only into that live conversation, never a cold one.
        ``owes_reply``: a person wrote this, and the session answers them in the
        room before it does anything else (`driven/runner.py`)."""
        # One line per message put to a session: where the platform spent the
        # time between the turn opening and the runner holding the input.
        with timed(
            logger,
            "room_send_timing",
            topic=session.conversation_id,
            seat=session.agent_handle,
            work=work_id,
        ) as mark:
            seat = self._seat_of(session)
            if expected_native_session is not None:
                live = self.live.get(seat)
                if (
                    live is None
                    or live.conversation != expected_native_session
                    or live.session.project_id != session.project_id
                ):
                    raise ValidationError(
                        "The original Ask session is not live; no replacement started"
                    )
            else:
                live = await self.ensure(
                    session,
                    system_prompt=system_prompt,
                    resume_token=resume_token,
                    model=model,
                    env=env,
                    acting=acting,
                    needs_place=needs_place,
                    reads_only=reads_only,
                    phases=mark.phases,
                )
                mark.lap()
                message = self._with_project_state(
                    seat, live, resume_token, session_opening, opening_changes, message
                )
            if not live.takes_inputs:
                raise InputProtocolUnavailable()
            pictures = self._images(session, images)
            on_mark(work_id)
            await self._consume(
                session.project_id,
                session.conversation_id,
                work_id,
                AgentSessionInfo(
                    session_id=live.conversation,
                    agent_handle=live.acting,
                    harness=self.harness,
                ),
                f"{self.harness}:{live.conversation}:opening:{work_id}",
                False,
                False,
            )
            mark("consume")
            # A message sent while the session is in the middle of a turn is read at
            # that turn's next tool boundary and answered inside it: its records,
            # its result and its end all carry the running turn's work, and none
            # ever names this one. Taking the seat over would leave it holding a
            # turn that never ends, and the next time the session goes away (the
            # runner lets an idle one go) that turn would be failed as a crash. The
            # platform's own work (``reading``) takes the seat all the same: its
            # caller waits on its own queue for an ending, and the session going
            # away is the only one it can get.
            if seat not in self.clocks or work_id in self.queues:
                self.work[seat] = work_id
            # 记忆先落到会话目录里，输入后写进去：agent 这一轮一睁眼读到的应当是平台
            # 现在这一份（别人刚改的也在里面），而不是它上一次看见的那一份。
            await self.reconcile_memory(session.topic_id)
            mark("memory")
            identity = InputIdentity(
                session.project_id,
                session.conversation_id,
                live.acting,
                self.harness,
                live.conversation,
                work_id,
                work_id,
            )
            await register_input(identity)
            mark("register")
            try:
                await self._submit(
                    live,
                    identity,
                    Prompt(work_id, message, images=pictures, owes_reply=owes_reply),
                    steer=False,
                    register_input=register_input,
                )
                mark("submit")
            finally:
                # A lost acknowledgement does not mean the session stopped working.
                self._listen(seat)
            return True

    def _with_project_state(
        self,
        seat: Seat,
        live: Live,
        resume_token: str | None,
        session_opening: str,
        opening_changes: str,
        message: str,
    ) -> str:
        """The message with the project state this conversation has not heard.

        A conversation is new when it is not the one the caller asked to resume:
        no token, or a resume that failed and started afresh. A new one gets the
        whole opening; one that goes on gets what changed since it last heard.
        A second message before the new conversation's token is recorded must
        not hand it the opening again, so each seat remembers the conversation
        it opened. After a backend restart that is gone, and the most a lost
        entry costs is an opening handed twice."""
        conversation = live.conversation
        fresh = conversation != resume_token and self.opened.get(seat) != conversation
        self.opened[seat] = conversation
        state = session_opening if fresh else opening_changes
        return f"{state}\n\n{message}" if state else message

    async def steer(
        self,
        topic_id: uuid.UUID,
        text: str,
        images: list[dict] | None = None,
        *,
        register_input: InputRegistrar,
        expected_work_id: uuid.UUID | None = None,
        agent_handle: str | None = None,
        owes_reply: bool = False,
    ) -> bool:
        """Words for a session that is already working, with no work opened for
        them: a person's message mid-work, or the platform telling a working
        session the world changed under it. True = it landed; False = no seat
        here is working.

        Addressed by room rather than by session because the caller is on the
        hot path with a person waiting and has no project id in hand.
        ``agent_handle`` names the seat when the caller knows whose work this
        belongs to; without it the room must have exactly one working seat, or
        the expected work id must pick it out — a guess between two working
        teammates would put the words into the wrong conversation.
        """
        seat = self._steer_seat(topic_id, expected_work_id, agent_handle)
        if seat is None:
            return False
        live = self.live.get(seat)
        work = self.work.get(seat)
        if live is None or work is None:
            return False
        status = await self.host.status(live.ref)
        if status is None or not status.working:
            return False
        if self.live.get(seat) is not live or self.work.get(seat) != work:
            return False
        if not status.takes_inputs:
            raise InputProtocolUnavailable()
        identity = InputIdentity(
            live.session.project_id,
            topic_id,
            live.acting,
            self.harness,
            live.conversation,
            uuid.uuid4(),
            work,
        )
        pictures = self._images(live.session, images)
        await register_input(identity)
        await self._submit(
            live,
            identity,
            Prompt(identity.input_id, text, images=pictures, owes_reply=owes_reply),
            steer=True,
            register_input=register_input,
        )
        return True

    async def _submit(
        self,
        live: Live,
        identity: InputIdentity,
        prompt: Prompt,
        *,
        steer: bool,
        register_input: InputRegistrar,
    ) -> None:
        accepted = False
        try:
            say_it = self.host.steer if steer else self.host.send
            await say_it(live.ref, prompt, work_id=identity.work_id)
            accepted = True
            # Only a harness whose acceptance *is* the read reports here. A
            # harness that says when its session read the input (Claude Code)
            # leaves this to the echo, so a consumer still holding the receipt
            # must never gate the send that admits it.
            if self.host.reads_on_accept(live.ref):
                await self._hear_receipt(InputReceipt(identity, "accepted"))
        except SessionError as exc:
            if accepted or isinstance(exc, InputUnconfirmed):
                raise InputOutcomeUnconfirmed(identity, accepted=accepted) from exc
            # The host raises InputUnconfirmed for anything after the write was
            # attempted; any other SessionError means it never got that far (the
            # session is not this process's, or could not be started again), so
            # the input is certainly not in the session: nothing to reconcile.
            await register_input.withdraw(identity)
            raise InputNotSent(identity, str(exc)) from exc
        except Exception as exc:
            # Even a transport error can follow admission at the remote end.
            # Keep the committed identity; the caller must not queue a new UUID.
            raise InputOutcomeUnconfirmed(identity, accepted=accepted) from exc

    def _steer_seat(self, topic_id, expected_work_id, agent_handle) -> Seat | None:
        """Which seat words addressed to a room mean, or None when ambiguous."""
        if agent_handle is not None:
            return (topic_id, agent_handle)
        working = [seat for seat in self.work if seat[0] == topic_id]
        if expected_work_id is not None:
            working = [seat for seat in working if self.work[seat] == expected_work_id]
        return working[0] if len(working) == 1 else None

    @staticmethod
    def _images(session: SessionRef, images: list[dict] | None) -> tuple[Image, ...]:
        """The room's attachments, as the pictures said along with words."""
        return tuple(
            Image(
                image["media_type"],
                library.read_attachment(
                    session.project_id, session.topic_id, image["path"]
                ),
            )
            for image in images or ()
        )

    @contextlib.asynccontextmanager
    async def reading(
        self, work_id: uuid.UUID
    ) -> AsyncIterator[AsyncIterator[AgentEvent]]:
        """The events of the platform's own work ``work_id`` (the memory
        consolidation), read here rather than heard by the room, until its
        result. Opened before the work is sent, so nothing it says is missed."""
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self.queues[work_id] = queue

        async def events() -> AsyncIterator[AgentEvent]:
            while True:
                event = await queue.get()
                yield event
                if isinstance(event, AgentResult):
                    return

        try:
            yield events()
        finally:
            self.queues.pop(work_id, None)

    async def ask_origin(self, project_id, topic_id, agent_handle) -> dict | None:
        """The seat's exact live native identity, starting and sending nothing:
        the work its session is doing is the work this process has open."""
        from app.domain.agent.ask_origin import refused

        seat = (topic_id, agent_handle)
        live = self.live.get(seat)
        work = self.work.get(seat)
        if live is None or work is None or live.session.project_id != project_id:
            refused(
                "no live session work",
                topic_id,
                agent_handle,
                live=live is not None,
                work=work,
            )
            return None
        status = await self.host.status(live.ref)
        if status is None or not status.working or status.work_id != str(work):
            refused(
                "session not working on it",
                topic_id,
                agent_handle,
                work=work,
                reachable=status is not None,
                working=status.working if status is not None else None,
                session_work=status.work_id if status is not None else None,
            )
            return None
        if self.live.get(seat) is not live or self.work.get(seat) != work:
            refused("session changed while reading", topic_id, agent_handle, work=work)
            return None
        return {
            "harness": self.harness,
            "native_session_id": live.conversation,
            "work_id": str(work),
            "recipient_handle": agent_handle,
        }

    async def interrupt(self, session: SessionRef) -> bool:
        """Take the seat's work away; the session and its conversation stay.
        True = the stop reached the session."""
        live = self.live.get(self._seat_of(session))
        if live is None:
            return False
        return await self.host.stop(live.ref)

    async def announce(self, topic: uuid.UUID) -> None:
        """Tell the room its session's controls moved."""
        from app.domain.agent.runtime import get_broker

        await get_broker().publish(
            str(topic),
            {"type": "agent_control", "state": await self.control_state(topic)},
        )

    def _control_seat(self, topic: uuid.UUID, agent: str | None) -> Seat | None:
        """The seat a control names, or the room's only live one when it names
        none. Several live and none named is no seat: the caller picks from
        ``seats`` rather than this picking a teammate at random."""
        return (topic, agent) if agent else self._room_seat(topic)

    async def control_state(self, topic: uuid.UUID, agent: str | None = None) -> dict:
        """What the room's controls show, from the session's mirror alone.

        ``seats`` lists every live seat in the room with its session, so a room
        with two teammates working says so instead of showing no session."""
        seat = self._control_seat(topic, agent)
        live = self.live.get(seat) if seat is not None else None
        shown = await self.host.control_state(live.ref) if live is not None else {}
        return {
            "id": live.conversation if live else None,
            "connected": live is not None,
            "agent": seat[1] if live is not None and seat is not None else None,
            "seats": [
                {"agent": handle, "id": held.conversation}
                for (room, handle), held in sorted(self.live.items())
                if room == topic
            ],
            "controls": list(self.controls),
            **shown,
        }

    async def control(
        self, topic: uuid.UUID, request: dict, agent: str | None = None
    ) -> dict:
        """One control request to the room's session, to its response."""
        seat = self._control_seat(topic, agent)
        live = self.live.get(seat) if seat is not None else None
        if live is None:
            raise LookupError("No session is running in this room")
        return await self.host.control(live.ref, request)

    # --- what the room asks --------------------------------------------------

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

    def holds_conversation(self, topic_id, harness, conversation) -> bool:
        """Is that exact conversation still attached somewhere in this room?

        A seat outlives its conversations, and an Ask answer may enter only the
        one that asked it: ``send`` refuses to start another for it. The
        conversation id is the harness's own, and a room holds it at most once,
        so the room is enough to identify it."""
        return self.harness == harness and any(
            live.conversation == conversation
            for seat, live in self.live.items()
            if seat[0] == topic_id
        )

    def holds(self, topic_id, agent_handle=None) -> bool:
        """Is there a session here this process can still reach — for this
        agent's seat in the room, when one is named?

        This is what "the work survived" means after a backend restart: the
        coroutine waiting on the turn died with the process, the agent on the
        session host did not, and ``recover`` found it again."""
        if agent_handle is not None:
            return (topic_id, agent_handle) in self.live
        return any(seat[0] == topic_id for seat in self.live)

    def work_in_flight(self, topic_id, agent_handle=None) -> uuid.UUID | None:
        """The work this pool's live seat in the room is running, if one is.

        With the agent named the answer is exact; without it only an
        unambiguous room gets one.
        """
        seats = [
            seat
            for seat in self.work
            if seat[0] == topic_id and (agent_handle is None or seat[1] == agent_handle)
        ]
        return self.work[seats[0]] if len(seats) == 1 else None

    # --- letting go, and finding again ---------------------------------------

    async def _detach(self, seat: Seat) -> None:
        task = self.tasks.pop(seat, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with attachments.lock(seat):
            if self.live.pop(seat, None) is not None:
                attachments.note(seat, None)
        self.work.pop(seat, None)
        self.clocks.pop(seat, None)
        self.told_waiting.pop(seat, None)
        self.caught.pop(seat, None)

    async def close(self, session: SessionRef) -> None:
        """Let this seat's session go: land what it has written, stop reading
        it and stop keeping track of it. Whatever a runner that is not there
        left is read when its session is next started."""
        seat = self._seat_of(session)
        live = self.live.get(seat)
        reading = self.tasks.get(seat)
        if live is not None and reading is not None and not reading.done():
            # The pass under way finishes, and the reading ends with it.
            await self.host.stop_listening([live.ref])
            await asyncio.wait({reading}, timeout=5)
        await self._detach(seat)
        if live is not None:
            self.host.release(live.ref)

    async def stop_listening(self) -> None:
        """Stop reading every seat, and leave every session running.

        A pass already under way finishes first. The landing cursor only moves
        past what was taken, so whatever this process had not landed is still
        unread for the process that listens next.

        The swap is per seat and by INSTANCE (FB-56): each seat's registry note
        and map removal happen under its own attachment lock, and only for the
        exact session this stop started with — an attach that won the seat in
        the meantime keeps its reading, its registry entry and every map,
        because the swap it made was complete. A seat whose reading was about to
        start finds nothing to read.
        """
        targets = [
            (seat, live, self.tasks.get(seat)) for seat, live in self.live.items()
        ]
        for seat, live, _task in targets:
            async with attachments.lock(seat):
                # Identity, not position: clear this seat's CURRENT state only
                # while it is still the session this stop took. A newer attach
                # is a complete swap and owns everything from here on.
                if self.live.get(seat) is live:
                    attachments.note(seat, None)
                    for held in (
                        self.live,
                        self.tasks,
                        self.work,
                        self.clocks,
                        self.told_waiting,
                    ):
                        held.pop(seat, None)
        await self.host.stop_listening([live.ref for _, live, _ in targets])
        pending = [
            task for _, _, task in targets if task is not None and not task.done()
        ]
        if pending:
            _, stuck = await asyncio.wait(pending, timeout=5)
            for task in stuck:
                task.cancel()
            await asyncio.gather(*stuck, return_exceptions=True)

    async def recover(self, device_id: str | None = None) -> list[SessionRef]:
        """This pool's sessions of this harness that outlived the process
        before this one, read again. What they said while nobody read them is
        ``replay``'s.

        Each placed session's runner is asked once. An answer that its harness
        process is gone counts as the conversation's death only when it names
        the stored resume token (FB-56 legacy③); a runner that could not be
        asked is unknown, never dead.
        """
        # These answer for THIS round only — a conversation the last round
        # reached says nothing about this one. (The cumulative witness is
        # `dead`: a death once seen stays seen.)
        self.found_conversations.clear()
        self.terminal_conversations.clear()
        found: list[tuple[SessionRef, CoreRef, Access, str | None, str]] = [
            (
                placed.session,
                CoreRef(self.harness, _home(placed.state)),
                Access(
                    "",
                    host=placed.machine,
                    owner=Owner(
                        placed.session.project_id,
                        placed.session.conversation_id,
                        placed.resource_id,
                        placed.session.agent_handle,
                        placed.agent_handle,
                        # The seat it was started on: a task's or a 支线's own.
                        seat=seat_key(placed.agent_handle, placed.session.inner_id),
                    ),
                ),
                placed.resume_token,
                placed.agent_handle,
            )
            for placed in await self.channel.placed(self.harness, device_id)
        ]
        await self.host.adopt([(ref, access) for _, ref, access, _, _ in found])
        recovered: list[SessionRef] = []
        for session, ref, access, resume_token, acting in found:
            seat = self._seat_of(session)
            room_id = session.topic_id
            try:
                status = await self.host.attach(ref, access)
            except RunnerUnsupported as exc:
                logger.warning(
                    "%s recovery refused topic=%s: %s", self.label, room_id, exc
                )
                continue
            if status is None:
                continue
            if not status.alive:
                # A runner that is gone ran the placement's stored
                # conversation; one that answered names its own.
                if resume_token and (
                    status.runner_gone or status.conversation == resume_token
                ):
                    self.terminal_conversations.add((seat, resume_token))
                continue
            self.found_conversations.add((seat, status.conversation))
            try:
                await self._attach(
                    Live(
                        session,
                        ref,
                        acting,
                        status.conversation,
                        status.takes_inputs,
                    )
                )
            except DeviceOffline:
                continue
            if status.working and status.work_id:
                self.work[seat] = uuid.UUID(status.work_id)
                now = time.monotonic()
                self.clocks[seat] = Clock(opened=now, progressed=now)
            self.unchecked.add(seat)
            recovered.append(session)
        # Chat restores room bookkeeping before replay starts reading.
        return recovered

    async def replay(self, session: SessionRef) -> None:
        """Land the tail a recovered session wrote while nobody read it, and go
        on reading it. Returns once the tail is landed; raises what stopped the
        reading before it was."""
        seat = self._seat_of(session)
        if seat not in self.live or (
            seat in self.tasks and not self.tasks[seat].done()
        ):
            return
        self._listen(seat, recovered=True)
        reading = self.tasks[seat]
        await self.caught[seat].wait()
        failure = (
            reading.exception() if reading.done() and not reading.cancelled() else None
        )
        if failure is not None:
            # What the journal kept could not be settled (a retained result
            # that disagrees with its inputs): the seat is not read on, and
            # whoever recovered it hears why.
            raise failure
