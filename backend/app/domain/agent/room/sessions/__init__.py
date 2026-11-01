"""A room's sessions on one machine pool, for one harness, as the room keeps them.

The session core (`session_host`) starts, talks to and reads every session;
this keeps the room's books on them. A room seats one session per teammate
(``Seat`` = topic, agent), and each seat has the work it is running, the inputs
said to it mid-work and which work read them, and the clocks its liveness rules
read. One reader per seat (``_poll``) hands each item the core reads to the
room (``RoomReader``), after these books have taken it.

The work does not live here. A session runs in a runner on the session host
that outlives this process, and ``recover`` finds it again after a restart.

What ends work the session did not end itself depends on the harness
(``docs/agent-liveness.md``). A runner that reports in the lifecycle protocol
ends stuck work itself, on the two limits it is registered with, and reports
the exit. For any other harness the core says the session is gone (its process
exited, or its runner was out of reach for ``RUNNER_GONE_S`` while work was
owed); here, the session has been talking without working for
``no_progress_s``, or something said to it has gone unread for
``unread_grace_s``.

**This package is the module's facade.** One file held all of it until
2026-10-16; the bodies moved into the sibling modules of this package, and what
stays in ``__init__.py`` is what the tree reaches by *this* module path:

  * the timing constants — ``RUNNER_GONE_S`` is monkeypatched on this module
    object (``tests/support/fast_timings.py`` names it, and
    ``tests/unit/test_driven_liveness.py`` patches it), so every decision that
    reads one (``_died``, ``ensure``) reads it through the package
    (``pkg.RUNNER_GONE_S``) rather than copying it at import time;
  * the public surface of ``RoomSessions`` — a forwarding method takes no
    decision, which is what keeps the two copies from drifting apart, and it is
    why every body that does decide stayed put. Two of them stay here for a
    second reason: ``ensure`` and ``_died`` are the readers of
    ``RUNNER_GONE_S``. The contract test takes the verb names off the class
    (``tests/contract/test_harness_contract.py``), and the integration tests
    subclass ``RoomSessions`` to replace ``ensure`` and ``_listen``
    (``tests/integration/test_input_not_sent.py``), so each one stays in the
    class body as a one-line delegation;
  * the type aliases the tree names through this module (``MemoryConsumer``,
    ``UnreadProbe``, ``QuietListener`` — read by
    ``app/domain/agent/compute.py``), and the two shapes a seat's session is
    held as (``Live``, ``Clock``);
  * the state: every map in ``__init__`` (``live``, ``work``, ``clocks``,
    ``tasks``, ``dead`` …) is here, and the sibling modules take an
    instance as their first argument. ``attachments.note`` registers the seat's
    attachment id, so ``__init__`` is where that registry's slot is decided.

Where each moved piece went: ``books`` (what the room hears, and the books kept
from it), ``reading`` (one seat's reading, attach, and the endings only this
process sees), ``starting`` (``ensure``), ``speaking`` (a message, a steer), ``lifecycle`` (letting go, and finding again), ``records``
(``Live``, ``Clock``, ``_home``).

**A package rather than six flat siblings.** C3 (``sibling domains under
app.domain are acyclic``) counts cycles among ``app.domain``'s children and among
theirs, and these bodies call into each other — ``send`` asks ``ensure`` for the
seat's session, ``_take`` asks the books to keep an ending — which as flat
modules under ``app.domain.agent`` is five new pairs, counting the type-only
import of this module back into the modules it forwards to. A child of this
package importing its own package is not one of them, so the internal cycle
stays inside: it is one room's sessions sharing one room's state, not the
cross-feature coupling C3 is there to catch. ``app/domain/agent/device_hub`` and
``app/domain/feedback/services`` took the same shape for the same reason.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.domain.agent.harness import HARNESSES, SessionRef
from app.domain.agent.harness.driven.subscription import Seat
from app.domain.agent.reads import Read
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.room.sessions.records import Clock as Clock
from app.domain.agent.room.sessions.records import Live as Live
from app.domain.agent.room.sessions.records import Outdated
from app.domain.agent.room.sessions.records import _home as _home
from app.domain.agent.service import AgentResult
from app.domain.agent.session_host.contract import Image, Prompt
from app.domain.agent.session_host.host import SessionHost
from app.domain.delivery.input_identity import (
    InputIdentity,
    InputReceipt,
    InputRegistrar,
)

# 同级子模块从**定义它们的模块**取，不经过包 `__init__` 的名字空间：
# `from . import x` 在名字尚未绑定时会回落去导入子模块本身，所以这里的
# 导入顺序不依赖 `__init__.py` 把 `books` 等名字先绑定好。
from . import books as _books
from . import lifecycle as _lifecycle
from . import reading as _reading
from . import speaking as _speaking
from . import starting as _starting

if TYPE_CHECKING:
    from app.domain.agent.central_provider import CentralChannel
    from app.domain.agent.room.reads import RoomReader

logger = logging.getLogger(__name__)

# (session) — lay this room's memory tree down in that seat's session, and take
# back what the agent wrote into it. Asked at two moments, and both ask the same
# question: just before an input goes in (so the session reads the platform's
# version) and just after a turn ends (so what it wrote comes back in the turn it
# was written in). It names the seat's session, not just the room: a room with
# several teammates live has a session per seat, and the one whose turn this is
# is the one to ask. Everything else it needs — who is speaking, the project's
# memory — lives on the side that owns the room (`chat.ChatService`).
MemoryConsumer = Callable[[SessionRef], Awaitable[None]]

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
# How often a runner another release started is asked whether the work it is
# finishing is done (``lifecycle._drain``). Its messages wait on the answer, so
# it is asked often; a ping is the cheapest call a runner answers.
DRAIN_POLL_S = 5.0
# How long a drain that interrupted its runner at the cap goes on asking it how
# the interrupted work ended, before settling without that answer.
DRAIN_STOP_WAIT_S = 60.0


class RoomSessions:
    """One harness's sessions on one machine pool, for every room."""

    #: The pool's backends all carry the pictures with the words.
    embeds_images = True

    def __init__(
        self,
        channel: CentralChannel,
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
        # Seats whose runner another release started, found by this round's
        # recovery: not read and given nothing. An idle one is settled at its
        # replay; one at work is drained first (``lifecycle._drain``), and its
        # seat's messages wait until it is settled (``draining``).
        self.outdated: dict[Seat, Outdated] = {}
        self.drains: dict[Seat, asyncio.Task] = {}
        # Seats settled that way, whose runner a start of this release
        # replaces: due as soon as the seat is quiet (``prewarm_due``).
        self.replace_due: dict[Seat, Live] = {}
        self._owns_sessions_provider = None
        self.tasks: dict[Seat, asyncio.Task] = {}
        # Each seat's reading caught up with what its session had written.
        self.caught: dict[Seat, asyncio.Event] = {}
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
        reader: RoomReader,
        *,
        unread: UnreadProbe,
        memory: MemoryConsumer,
        quiet: QuietListener | None = None,
    ) -> None:
        """The room's books: where it hears its sessions, where it says what
        it sent that is still unread, where its memory is reconciled, and who
        brings a seat that went quiet up to date (``prewarm_due``)."""
        self.reader, self.unread, self._memory = reader, unread, memory
        self._quiet = quiet

    def prewarm_due(self, session: SessionRef) -> Live | None:
        """The seat's live session, when the next message would have to start
        it again and nothing is running on it; else None.

        Owed after this process took the seat over, until a start compares it
        (``unchecked``); whenever the channel put a relaunch off because the
        session was working (``owes``); and once its runner stopped answering,
        which is what letting an idle session go looks like from here. Only the
        process that owns the running work answers yes: the one handing it over
        must not start what the next one is about to read."""
        seat = self._seat_of(session)
        provider = self._owns_sessions_provider
        if provider is not None and not provider().owns_sessions:
            return None
        if seat in self.replace_due:
            # Its runner is another release's, settled and idle: replaced now.
            return self.replace_due[seat]
        live = self.live.get(seat)
        if live is None or seat in self.work:
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
    ) -> object:
        return await _books._hear(self, session, read, required=required)

    def _room(
        self, project: uuid.UUID, topic: uuid.UUID, agent: str = ""
    ) -> SessionRef:
        return _books._room(self, project, topic, agent)

    async def _hear_receipt(self, receipt: InputReceipt) -> None:
        return await _books._hear_receipt(self, receipt)

    async def reconcile_memory(self, session: SessionRef) -> None:
        return await _books.reconcile_memory(self, session)

    async def memory(self, session: SessionRef, request: dict) -> dict | None:
        return await _books.memory(self, session, request)

    def _room_seat(self, topic_id: uuid.UUID) -> Seat | None:
        """The room's only live seat, or None when there is none — or several.

        The controls relay predates seats; with two teammates live in one room
        it has no single answer, and None makes the caller say so rather than
        pick a teammate at random.
        """
        seats = [seat for seat in self.live if seat[0] == topic_id]
        return seats[0] if len(seats) == 1 else None

    # --- the room's books ----------------------------------------------------

    def pulse(self, seat: Seat, marks: frozenset[str]) -> None:
        return _books.pulse(self, seat, marks)

    def verdict(self, seat: Seat) -> AgentResult | None:
        return _books.verdict(self, seat)

    async def _end_by_verdict(self, live: Live, result: AgentResult) -> None:
        return await _books._end_by_verdict(self, live, result)

    async def _consume(
        self, project, topic, work, event, eid, seen, unsolicited
    ) -> None:
        return await _books._consume(
            self, project, topic, work, event, eid, seen, unsolicited
        )

    async def _activity(self, project, seat: Seat, work, active) -> None:
        return await _books._activity(self, project, seat, work, active)

    # --- reading -------------------------------------------------------------

    @staticmethod
    def _seat_of(session: SessionRef) -> Seat:
        """The seat a session ref names: (conversation, agent)."""
        return _reading._seat_of(session)

    async def _attach(self, live: Live) -> Live:
        return await _reading._attach(self, live)

    def _owning(self, seat: Seat) -> None:
        _reading._owning(self, seat)

    def _listen(self, seat: Seat) -> None:
        _reading._listen(self, seat)

    def _clock_read_s(self, seat: Seat) -> float:
        return _reading._clock_read_s(self, seat)

    async def _poll(self, seat: Seat, live: Live, caught: asyncio.Event) -> None:
        return await _reading._poll(self, seat, live, caught)

    async def _take(
        self, seat: Seat, live: Live, read: Read, caught: asyncio.Event
    ) -> bool:
        return await _reading._take(self, seat, live, read, caught)

    async def _say_waiting(self, seat: Seat, reason: str) -> None:
        return await _reading._say_waiting(self, seat, reason)

    async def _say_resumed(self, seat: Seat) -> None:
        return await _reading._say_resumed(self, seat)

    async def _died(
        self, live: Live, *, out_of_reach: bool = False, said: str = ""
    ) -> None:
        return await _reading._died(self, live, out_of_reach=out_of_reach, said=said)

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
        keeps_nothing: bool = False,
        phases: dict[str, float] | None = None,
    ) -> Live:
        return await _starting.ensure(
            self,
            session,
            system_prompt=system_prompt,
            resume_token=resume_token,
            model=model,
            env=env,
            acting=acting,
            needs_place=needs_place,
            keeps_nothing=keeps_nothing,
            phases=phases,
        )

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
        model: str | None = None,
        env: dict[str, str] | None = None,
        acting: str | None = None,
        needs_place: bool = True,
        keeps_nothing: bool = False,
        images: list[Image] | None = None,
        owes_reply: bool = False,
        session_opening: str = "",
        opening_changes: str = "",
    ) -> bool:
        return await _speaking.send(
            self,
            session,
            message,
            system_prompt=system_prompt,
            work_id=work_id,
            on_mark=on_mark,
            register_input=register_input,
            resume_token=resume_token,
            model=model,
            env=env,
            acting=acting,
            needs_place=needs_place,
            keeps_nothing=keeps_nothing,
            images=images,
            owes_reply=owes_reply,
            session_opening=session_opening,
            opening_changes=opening_changes,
        )

    def _with_project_state(
        self,
        seat: Seat,
        live: Live,
        resume_token: str | None,
        session_opening: str,
        opening_changes: str,
        message: str,
    ) -> str:
        return _speaking._with_project_state(
            self,
            seat,
            live,
            resume_token,
            session_opening,
            opening_changes,
            message,
        )

    async def steer(
        self,
        topic_id: uuid.UUID,
        text: str,
        images: list[Image] | None = None,
        *,
        register_input: InputRegistrar,
        agent_handle: str | None = None,
        owes_reply: bool = False,
    ) -> bool:
        return await _speaking.steer(
            self,
            topic_id,
            text,
            images,
            register_input=register_input,
            agent_handle=agent_handle,
            owes_reply=owes_reply,
        )

    async def _submit(
        self,
        live: Live,
        identity: InputIdentity,
        prompt: Prompt,
        *,
        steer: bool,
        register_input: InputRegistrar,
    ) -> None:
        return await _speaking._submit(
            self,
            live,
            identity,
            prompt,
            steer=steer,
            register_input=register_input,
        )

    def _steer_seat(self, topic_id, agent_handle) -> Seat | None:
        return _speaking._steer_seat(self, topic_id, agent_handle)

    async def interrupt(self, session: SessionRef) -> bool:
        """Take the seat's work away; the session and its conversation stay.
        True = the stop reached the session. A runner being drained is
        stopped too, under the registration it holds, and its work then ends
        as stopped rather than as one to start again."""
        seat = self._seat_of(session)
        live = self.live.get(seat)
        if live is not None:
            return await self.host.stop(live.ref)
        if not self._draining(seat):
            return False
        outdated = self.outdated[seat]
        stopped = await self.host.stop(
            outdated.live.ref, host=outdated.access.host, epoch=outdated.epoch
        )
        # Only a stop that reached it ends its work as stopped; one that did
        # not leaves the work to end as it will.
        outdated.stopped = outdated.stopped or stopped
        return stopped

    def holding(self, seat: Seat) -> SessionRef | None:
        """The session this process holds on the seat, a runner being drained
        included; None when it holds none."""
        if (live := self.live.get(seat)) is not None:
            return live.session
        return self.outdated[seat].live.session if self._draining(seat) else None

    async def announce(self, topic: uuid.UUID) -> None:
        """Tell the room its session's controls moved."""
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
        """What the room's controls show, as the session's runner last reported
        them (`session_host.control`); nothing for a harness whose runner does
        not report them.

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

    def holds(self, topic_id, agent_handle=None) -> bool:
        """Is there a session here this process can still reach — for this
        agent's seat in the room, when one is named?

        This is what "the work survived" means after a backend restart: the
        coroutine waiting on the turn died with the process, the agent on the
        session host did not, and ``recover`` found it again. A runner being
        drained (``draining``) is such a session: its work goes on."""
        held = [*self.live, *(seat for seat in self.outdated if self._draining(seat))]
        if agent_handle is not None:
            return (topic_id, agent_handle) in held
        return any(seat[0] == topic_id for seat in held)

    def _draining(self, seat: Seat) -> bool:
        outdated = self.outdated.get(seat)
        return outdated is not None and outdated.working and not outdated.settling

    def draining(self, topic_id: uuid.UUID, agent_handle: str) -> bool:
        """Is this seat's runner another release's, finishing its work? Then
        it is given nothing, and a message for the seat waits until a runner
        of this release has taken its place (``lifecycle._drain``)."""
        return self._draining((topic_id, agent_handle))

    def tell_draining(self, topic_id: uuid.UUID, agent_handle: str) -> bool:
        """Whether the room is to be told now that a message waits for this
        seat's drain: once per drain, the first time one does."""
        outdated = self.outdated.get((topic_id, agent_handle))
        if outdated is None or outdated.told:
            return False
        outdated.told = True
        return True

    def working_seats(self, topic_id, agent_handle=None) -> list[tuple[str, str]]:
        """The seats whose live session in this room is at work, each as its
        own handle and the handle it acts under; with ``agent_handle``, only
        the one it names, by either handle."""
        return [
            (seat[1], live.acting)
            for seat, live in self.live.items()
            if seat[0] == topic_id
            and seat in self.work
            and (agent_handle is None or agent_handle in (seat[1], live.acting))
        ]

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
        return await _lifecycle._detach(self, seat)

    async def close(self, session: SessionRef) -> None:
        return await _lifecycle.close(self, session)

    async def stop_listening(self) -> None:
        return await _lifecycle.stop_listening(self)

    async def recover(self, device_id: str | None = None) -> list[SessionRef]:
        return await _lifecycle.recover(self, device_id)

    async def replay(self, session: SessionRef) -> None:
        return await _lifecycle.replay(self, session)
