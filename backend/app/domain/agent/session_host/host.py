"""Sessions on the session host: started from a spec, spoken to, and read.

A session lives in a runner on the session host that outlives this process
(`driven/runner.py`). Starting one is its harness's launch there (``Driver``):
a session already running from the same launch is used as it is, and one
running from another is started again from this one once it is idle. A session
sits idle for a while and then exits; its conversation stays in its state
directory, and the next start brings it back on it.

Reading is handed over one item at a time (``read``). For a harness whose
runner reports in the lifecycle protocol (``Reports``, Claude Code) it is what
the runner reports, acknowledged once applied. For one that keeps a mirror
(``Mirrors``, pi and Codex) it is the runner's journal, mirrored on this
backend, and the mirror's landing cursor moves only past what the
reader took: an item it took is one it asked for the next of, or one it took
before it stopped reading; an item it failed on is thrown back into the reading
(``athrow``), re-read on the next pass, and stepped over once it has failed
often enough (`driven/subscription.py`). So a reader that dies anywhere re-reads
rather than skips, and the next process to read a session starts where this one
stopped landing.

Whether a session is gone: a reporting runner says its process exited
(``SessionExited``), and being out of reach for ``LEASE_S`` only tells the
room. For a mirrored harness it is decided here, once, for every reader: its
runner says its harness process exited, or it stays out of reach for longer
than the spec's ``gone_after_s`` while something said to it is unanswered
(``Ended``).

``SessionHost`` is what the rest of the platform knows the session core by, and
this module is that door (``block``, ``room``, ``personal``, ``document``, the
routes). What it is asked of a session is written beside it: bringing one up on
its host (``starting``), saying something to it (``speaking``), asking after it
and letting it go (``control``), and what this process holds of one
(``sessions``) — and those parts name this class through the package
(``session_host.SessionHost``), because a sibling import both ways is a cycle.
The reading is this module's own, and so are the waits it
takes: ``READ_WAIT_S`` holds one read at the runner, ``GIVE_UP_CHECK_S`` is how
often a start waiting for the host's memory looks again, and neither is read
from anywhere else.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
)
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.core.config import settings
from app.domain.agent import session_state
from app.domain.agent.admission import HostMemory
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceNotReady,
    DeviceOffline,
)
from app.domain.agent.harness import CLAUDE_CODE, CODEX, PI
from app.domain.agent.harness import SessionRef as Seat
from app.domain.agent.harness.driven import lifecycle
from app.domain.agent.harness.driven.reporting import Reading
from app.domain.agent.harness.driven.subscription import Records, Subscription
from app.domain.agent.machine_address import device_api_base
from app.domain.agent.reads import (
    CaughtUp,
    ControlsMoved,
    Ended,
    Moved,
    Reachable,
    Read,
    Report,
    SessionExited,
    Working,
    Writing,
)
from app.domain.agent.session_host import control, speaking, starting
from app.domain.agent.session_host.claude_code import ClaudeCodeDriver
from app.domain.agent.session_host.codex import CodexDriver
from app.domain.agent.session_host.contract import (
    Access,
    HostFull,
    Prompt,
    SessionError,
    SessionRef,
    SessionSpec,
    SessionStatus,
    StartAbandoned,
)
from app.domain.agent.session_host.driver import (
    Driver,
    Mirrors,
    Readers,
    Reports,
    Wire,
)
from app.domain.agent.session_host.pi import PiDriver

# 下面这些 import 都是再导出：`X as X` 是仓库里认的那种写法，读的人一眼看出它
# 进来不是为了在本文件里用。拆分改的是文件，不是这个模块能取到的名字 —— 谁以前
# 从 app.domain.agent.session_host.host 拿得到 keeps_memory、RunnerUnsupported 或
# _Running，拆分之后照样拿得到。
from app.domain.agent.session_host.sessions import (
    PICTURES as PICTURES,
)
from app.domain.agent.session_host.sessions import (
    _mirror as _mirror,
)
from app.domain.agent.session_host.sessions import (
    _Running as _Running,
)
from app.domain.agent.session_host.sessions import (
    _seat as _seat,
)
from app.domain.agent.session_host.sessions import (
    keeps_memory as keeps_memory,
)
from app.domain.agent.session_host.sessions import key
from app.domain.agent.session_host.sessions import (
    with_pictures as with_pictures,
)
from app.domain.agent.session_host.starting import (
    RunnerUnsupported as RunnerUnsupported,
)
from app.domain.agent.session_state import SeatKey
from app.domain.delivery.receipts import hand_to

if TYPE_CHECKING:
    from app.domain.agent.device_provider import DeviceChannel

logger = logging.getLogger(__name__)

#: How long one read may be held at the runner. A runner holds a read until it
#: has something to answer it with (``driven.runner``), so a quiet session costs
#: one call per this long, and a record or a token it writes is read the moment
#: it is written. Far below every timeout on the way: the call's own (660 s),
#: the connection owner's, and the connector's.
READ_WAIT_S = 25.0
#: How often a start waiting for the host's memory looks again, and asks
#: whether it is still wanted.
GIVE_UP_CHECK_S = 1.0
#: How long a reading with nothing owed waits before asking again a runner that
#: let its idle session go; nobody is waiting on it, and something said to the
#: session wakes the reading at once.
IDLE_GONE_READ_S = 60.0
#: How long a reading that failed waits before trying again.
RETRY_S = 2.0
#: How long a pass refused under the epoch this process is replacing waits
#: before it reads again: the registration on its way to the runner lands
#: within a call's round trip.
REGISTERING_WAIT_S = 0.05
#: How long the readings under way may take to finish their pass when this
#: process stops reading.
STOP_LISTENING_S = 5.0
#: Calls that only look: a ping, a stop.
QUICK_S = 15
#: How long a runner that reports in the lifecycle protocol may go unheard
#: before its session is unreachable: longer than the connection owner takes to
#: come back after a release, and than a device takes to reconnect after a
#: network blip.
LEASE_S = 120.0


class SessionHost:
    """Every session on the session hosts this backend process talks to."""

    def __init__(
        self,
        hub=None,
        memory: HostMemory | None = None,
        *,
        screens: "DeviceChannel | None" = None,
        session_factory=None,
        mirrors: Path | None = None,
        on_alive: Callable[[uuid.UUID], None] | None = None,
        records: Records | None = None,
        register: Callable[[SeatKey], Awaitable[int]] | None = None,
        hand: Callable[[str, str, uuid.UUID, int], Awaitable[None]] | None = None,
        drivers: Mapping[str, Driver] | None = None,
    ):
        self._hub = hub
        #: Where every row a session's journal holds is kept as its raw
        #: record (`session_record.service.append`). None keeps no record.
        self._records = records
        #: Issues a seat's runner its worker epoch, for a harness whose runner
        #: reports in the lifecycle protocol: in the platform's database
        #: (`session_state.register`) unless told otherwise.
        self._registrar = register
        #: Records which process an input is being handed to, in the books the
        #: epochs come from (`delivery.receipts.hand_to`) unless told otherwise:
        #: ``(harness, native conversation, input id, epoch)``.
        self._hand = hand
        self._memory = memory
        #: Told the conversation of a session whose runner answered a read, on
        #: every pass that heard one: proof that the session is still there,
        #: which no frame carries. The stall verdict reads it
        #: (`TopicService.stall_signal`), through `realtime.broker.note_alive`.
        self._on_alive = on_alive
        #: Where this backend keeps its copies of the journals of the sessions
        #: of a harness that keeps a mirror (`driver.Mirrors`).
        self._mirrors = mirrors or Path(settings.workspace_root) / ".harness"
        self._screens = screens
        self._session_factory = session_factory
        #: Drivers a caller hands in, by harness name: consulted before the ones
        #: this class knows how to build. A test that drives a harness with a
        #: stand-in hands one here rather than reaching into ``_drivers``.
        self._injected = drivers
        self._drivers: dict[str, Driver] | None = None
        self._running: dict[SessionRef, _Running] = {}
        self._locks: dict[SessionRef, asyncio.Lock] = {}

    @property
    def hub(self):
        if self._hub is None:
            # deferred-import: tests replace this name on app.domain.agent.device_hub
            from app.domain.agent.device_hub import device_hub

            self._hub = device_hub
        return self._hub

    def _driver(self, ref: SessionRef) -> Driver:
        if self._injected is not None:
            injected = self._injected.get(ref.harness)
            if injected is not None:
                return injected
        if self._drivers is None:
            screens = self._screens
            if screens is None:
                # deferred-import: cycle agent.device_provider -> session_host.host
                from app.domain.agent.device_provider import DeviceChannel

                screens = DeviceChannel(
                    hub=self.hub, session_factory=self._session_factory
                )
            drivers: dict[str, Driver] = {
                PI: PiDriver(),
                CLAUDE_CODE: ClaudeCodeDriver(screens),
                CODEX: CodexDriver(),
            }
            self._drivers = drivers
        driver = self._drivers.get(ref.harness)
        if driver is None:
            raise SessionError(f"No session can be started for {ref.harness}")
        return driver

    def _wire(self) -> Wire:
        return Wire(self.hub, self._api)

    async def _register(self, key: SeatKey) -> int:
        if self._registrar is not None:
            return await self._registrar(key)
        # deferred-import: tests replace this name on app.core.db
        from app.core.db import async_session_factory

        return await session_state.register(
            self._session_factory or async_session_factory, key
        )

    async def _handing(
        self, ref: SessionRef, running: _Running, input_id: uuid.UUID
    ) -> None:
        """Record that ``input_id`` is being handed to the process ``running``
        holds (`delivery.receipts.hand_to`)."""
        args = (ref.harness, running.launched.conversation, input_id)
        epoch = running.launched.epoch
        if self._hand is not None:
            await self._hand(*args, epoch)
            return
        # deferred-import: tests replace this name on app.core.db
        from app.core.db import async_session_factory

        async with (self._session_factory or async_session_factory)() as session:
            await hand_to(
                session,
                harness=args[0],
                native_session_id=args[1],
                input_id=args[2],
                epoch=epoch,
            )
            await session.commit()

    def _call(
        self, running: _Running, ref: SessionRef, method: str, params: dict, **kwargs
    ):
        """A call the platform makes of the session's runner, carrying the
        epoch it registered the runner under when it did: a runner registered
        since by another process refuses it (`lifecycle.StaleEpoch`)."""
        if running.launched.epoch:
            params = {**params, "epoch": running.launched.epoch}
        return self._wire().call(running.host, ref, method, params, **kwargs)

    async def _api(self, host: str) -> str:
        """The platform as ``host`` reaches it."""
        # deferred-import: tests replace this name on app.core.db
        from app.core.db import async_session_factory

        factory = self._session_factory or async_session_factory
        async with factory() as db:
            return await device_api_base(db, host, settings.connector_public_base)

    def _look(
        self, host: str, ref: SessionRef, method: str, params: dict, *, epoch: int = 0
    ):
        """A call that only looks — a ping, a stop — which no runner holds
        open (``QUICK_S``); one the platform makes as a runner's registration
        carries its ``epoch``."""
        if epoch:
            params = {**params, "epoch": epoch}
        return self._wire().call(host, ref, method, params, timeout=QUICK_S)

    def available(self) -> bool:
        host = settings.agent_session_device_id
        return bool(host and self.hub.is_online(host))

    def reads_on_accept(self, ref: SessionRef) -> bool:
        """Whether the runner taking an input is the session reading it; for
        the rest, the reading says when an input was read (``Received``)."""
        return self._driver(ref).receipt_on_accept

    def _started(self, ref: SessionRef) -> _Running:
        running = self._running.get(ref)
        if running is None:
            raise SessionError("The session was not started by this process")
        return running

    def _wake(self, running: _Running) -> None:
        running.woken.set()
        if running.subscription is not None:
            running.subscription.unpark()

    async def start(
        self,
        ref: SessionRef,
        spec: SessionSpec,
        access: Access,
        *,
        on_wait: Callable[[], Awaitable[None]] | None = None,
        give_up: Callable[[], Awaitable[bool]] | None = None,
    ) -> SessionStatus:
        return await starting.start(
            self, ref, spec, access, on_wait=on_wait, give_up=give_up
        )

    async def adopt(self, found: list[tuple[SessionRef, Access]]) -> None:
        return await starting.adopt(self, found)

    async def attach(self, ref: SessionRef, access: Access) -> SessionStatus | None:
        return await starting.attach(self, ref, access)

    async def _wait_for_memory(
        self,
        spec: SessionSpec,
        on_wait: Callable[[], Awaitable[None]] | None,
        give_up: Callable[[], Awaitable[bool]] | None,
    ) -> None:
        assert spec.footprint is not None
        # One reading for every start that waits: it is cached
        # (``admission.READING_TTL_S``), so looking again costs the host nothing.
        if self._memory is None:
            self._memory = HostMemory(self.hub)
        memory = self._memory
        until = time.monotonic() + spec.host_wait_s
        told = False
        while not await memory.can_start(spec.footprint.memory_mb):
            if time.monotonic() >= until:
                raise HostFull("The session host has no memory for another session")
            if give_up is not None and await give_up():
                raise StartAbandoned("Stopped waiting for the session host")
            if not told:
                told = True
                if on_wait is not None:
                    await on_wait()
            await asyncio.sleep(
                min(GIVE_UP_CHECK_S, max(0.0, until - time.monotonic()))
            )

    async def send(
        self, ref: SessionRef, prompt: Prompt, *, work_id: uuid.UUID
    ) -> None:
        return await speaking.send(self, ref, prompt, work_id=work_id)

    async def steer(
        self, ref: SessionRef, prompt: Prompt, *, work_id: uuid.UUID
    ) -> None:
        return await speaking.steer(self, ref, prompt, work_id=work_id)

    async def act_as(self, ref: SessionRef, acting: str) -> None:
        return await speaking.act_as(self, ref, acting)

    def answers(self, ref: SessionRef) -> bool | None:
        return speaking.answers(self, ref)

    async def stop(
        self, ref: SessionRef, *, host: str | None = None, epoch: int = 0
    ) -> bool:
        return await control.stop(self, ref, host=host, epoch=epoch)

    def release(self, ref: SessionRef) -> None:
        return control.release(self, ref)

    async def status(self, ref: SessionRef) -> SessionStatus | None:
        return await control.status(self, ref)

    def frame_snapshots(self, turn_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, dict]:
        """The last frame each of these live turns' sessions reported.

        One pass over the sessions this process already reads — never one
        cross-machine call per turn — and only a session whose last frame
        belongs to a work still in flight contributes. A turn this process
        does not read, or whose frame belongs to a finished work, is simply
        absent: the caller must read that as "unknown", not as age 0.
        """
        wanted = {str(turn_id) for turn_id in turn_ids}
        found: dict[uuid.UUID, dict] = {}
        if not wanted:
            return found
        for running in list(self._running.values()):
            subscription = running.subscription
            if subscription is None:
                continue
            frame = subscription.frame()
            if frame is None:
                continue
            work = frame.get("work_id")
            if work is None or str(work) not in wanted:
                continue
            found[uuid.UUID(str(work))] = frame
        return found

    async def memory(self, ref: SessionRef, request: dict) -> dict | None:
        return await control.memory(self, ref, request)

    async def control(self, ref: SessionRef, request: dict) -> dict:
        return await control.control(self, ref, request)

    async def control_state(self, ref: SessionRef) -> dict:
        return await control.control_state(self, ref)

    async def read(self, ref: SessionRef) -> AsyncGenerator[Read]:
        """What the session writes, from where this backend last landed, as it
        is written. The reading goes on until the caller stops it, this process
        stops listening (``stop_listening``), or the session is gone
        (its runner reports its process gone, or, for a harness that keeps a
        mirror, ``Ended``).

        An item is taken once the caller asks for the next one or stops
        reading. A caller that failed on one throws the failure back in
        (``athrow``): the item is read again on the next pass, and the reading
        goes on from there."""
        running = self._started(ref)
        driver = self._driver(ref)
        handing: asyncio.Queue[tuple[Read, asyncio.Future]] = asyncio.Queue(1)

        async def hand(read: Read) -> None:
            taken = asyncio.get_running_loop().create_future()
            await handing.put((read, taken))
            await taken

        async def consume(project, topic, work, event, eid, seen, unsolicited):
            await hand(Read(str(work), event, eid, seen, unsolicited))

        async def activity(project, seat, work, active):
            running.owed = active
            await hand(Read(str(work), Working(active)))

        async def moved(work, marks):
            await hand(Read(str(work), Moved(marks)))

        async def announce():
            await hand(Read(None, ControlsMoved()))

        async def report(work, reported: Report):
            if isinstance(reported.fact, SessionExited) and (
                reported.of_this_registration
            ):
                # Its runner says the process this registration holds is gone:
                # this process holds no session there any more, whatever its
                # reader does next (a send that follows starts it again). The
                # reader applies it under this same registration, and one it
                # refuses as stale means another process holds the seat now,
                # which ends this process's holding of it either way.
                self._forget(ref, running)
            await hand(Read(str(work) if work else None, reported))

        async def call(method: str, params: dict) -> dict:
            return await self._wire().call(running.host, ref, method, params)

        readers = Readers(
            consume,
            activity,
            moved,
            announce,
            # A session no place owns (a person's 芝士, a document's) has no
            # conversation to keep a record in (`sessions._seat`).
            self._records if _owned(running.seat) else None,
            report,
            key(ref, running.seat, running.acting),
            lambda: running.launched.epoch,
        )
        subscription: Subscription | Reading
        if isinstance(driver, Reports):
            subscription = driver.reading(
                running.seat, running.acting, call, running.launched, readers
            )
        else:
            assert isinstance(driver, Mirrors) and running.mirror is not None
            running.mirror.parent.mkdir(parents=True, exist_ok=True)
            subscription = driver.subscription(
                running.seat,
                running.acting,
                running.mirror,
                call,
                running.launched,
                readers,
            )
        running.subscription = subscription
        pump = asyncio.create_task(
            self._pump_reports(ref, running, subscription, hand)
            if isinstance(subscription, Reading)
            else self._pump(ref, running, subscription, hand),
            name=f"reading {ref.harness} {ref.home}",
        )
        running.reading = pump
        getting: asyncio.Future | None = None
        try:
            while True:
                getting = asyncio.ensure_future(handing.get())
                await asyncio.wait({getting, pump}, return_when=asyncio.FIRST_COMPLETED)
                if not getting.done():
                    getting.cancel()
                    await asyncio.gather(getting, return_exceptions=True)
                    if not pump.cancelled():
                        pump.result()
                    return
                read, taken = getting.result()
                # The pump waits on ``taken`` while the caller holds the item.
                # A stop that outwaited the pass (``stop_listening``) cancels
                # the pump, and with it ``taken``: nobody is left to tell, and
                # the next turn of the loop finds the pump done and ends.
                try:
                    yield read
                except GeneratorExit:
                    if not taken.done():
                        taken.set_result(None)
                    raise
                except BaseException as exc:
                    if not taken.done():
                        taken.set_exception(exc)
                else:
                    if not taken.done():
                        taken.set_result(None)
        finally:
            # A reader that stopped while waiting leaves the wait pending.
            if getting is not None and not getting.done():
                getting.cancel()
            if not pump.done():
                pump.cancel()
            await asyncio.gather(
                pump, *([getting] if getting else []), return_exceptions=True
            )
            if running.subscription is subscription:
                running.subscription = None
                running.reading = None
            await subscription.release()

    async def _pump_reports(
        self, ref: SessionRef, running: _Running, reading: Reading, hand: Callable
    ) -> None:
        """Pass after pass over a runner that reports in the lifecycle
        protocol, until its process is gone, another registration took it
        over, or the reading is stopped.

        Every answer renews the lease on its runner. One not renewed for
        ``LEASE_S`` makes the session unreachable, which the room is told and
        nothing else: no input is settled and no work ended on it, because a
        runner that answers again reports what happened meanwhile.
        """
        driver = self._driver(ref)
        first = True
        failing_since: float | None = None
        told = False
        while True:
            running.woken.clear()
            # The registration this pass reads under: one this process made
            # since is its own, and the pass is simply asked again.
            asked = running.launched.epoch
            try:
                await reading.drain(wait=0 if first else READ_WAIT_S)
            except lifecycle.StaleEpoch as exc:
                if await self._ours(running, asked):
                    continue
                return self._superseded(ref, running, driver, exc)
            except (DeviceOffline, DeviceCallError, httpx.TransportError) as exc:
                if lifecycle.stale(exc):
                    if await self._ours(running, asked):
                        continue
                    return self._superseded(ref, running, driver, exc)
                running.answering = False
                now = time.monotonic()
                if failing_since is None:
                    logger.info(
                        "%s session %s (%s) waiting for %s: %s",
                        driver.label,
                        ref.home,
                        reading.state.get("worker_status"),
                        _waiting_for(exc),
                        exc,
                    )
                failing_since = failing_since or now
                if not told and now - failing_since >= LEASE_S:
                    told = True
                    logger.warning(
                        "a %s session on %s is unreachable: %s",
                        driver.label,
                        running.host,
                        exc,
                    )
                    await hand(Read(None, Reachable(False, _why(exc))))
                if running.stopping:
                    return
                missing = isinstance(exc, DeviceCallError) and not isinstance(
                    exc, DeviceNotReady
                )
                if missing and reading.state.get("worker_status") != lifecycle.RUNNING:
                    # The runner let its idle session go; nobody is waiting.
                    await self._wait(running, IDLE_GONE_READ_S)
                else:
                    await asyncio.sleep(RETRY_S)
                continue
            except Exception:
                # What the reader refused: the event it refused, and every one
                # after it, is unacknowledged and handed over again next pass.
                logger.exception("%s home=%s", driver.read_failure, ref.home)
                if running.stopping:
                    return
                await asyncio.sleep(RETRY_S)
                continue
            if failing_since is not None:
                failing_since = None
                logger.info("%s session %s resumed", driver.label, ref.home)
                if told:
                    told = False
                    await hand(Read(None, Reachable(True)))
            if first:
                first = False
                await hand(Read(None, CaughtUp()))
            heard = reading.heard
            # A runner on its way out answers that its process is going: a
            # send that follows starts it again rather than trusting it.
            running.answering = heard.get("alive") is not False
            if isinstance(live := heard.get("live"), dict):
                await hand(
                    Read(
                        live.get("work_id"),
                        Writing(
                            tuple(live.get("blocks") or ()),
                            returned=tuple(live.get("returned") or ()),
                            branches=tuple(live.get("branches") or ()),
                            made=tuple(live.get("made") or ()),
                        ),
                    )
                )
            if heard.get("alive") is True and self._on_alive is not None:
                if (seat := running.seat) is not None:
                    self._on_alive(seat.conversation_id)
            if reading.exited is not None:
                # Its runner said the process is gone, and the room took it.
                return
            if running.stopping:
                return

    @staticmethod
    async def _ours(running: _Running, asked: int) -> bool:
        """Whether a stale-epoch refusal of a pass read under ``asked`` came
        from this process's own registration: one it made since the pass
        began (read again at once, under it), or one it is handing to the
        runner right now (read again once that had a moment). Only a newer
        registration another process made supersedes the reading.

        A runner this process is replacing refuses the old epoch the moment it
        is launched, before the registration exists to hand it
        (``relaunching``): that refusal too is this process's own."""
        if running.launched.epoch != asked:
            return True
        if running.registering is not None or running.relaunching:
            await asyncio.sleep(REGISTERING_WAIT_S)
            return True
        return False

    def _superseded(
        self, ref: SessionRef, running: _Running, driver: Driver, exc: Exception
    ) -> None:
        """Another registration holds the session's runner now: this
        process's reading of it is over, and it changes nothing more."""
        logger.info("%s session %s superseded: %s", driver.label, ref.home, exc)
        self._forget(ref, running)

    async def _pump(
        self,
        ref: SessionRef,
        running: _Running,
        subscription: Subscription,
        hand: Callable,
    ) -> None:
        """Pass after pass over the session's journal, each landing what the
        reader took, until the session is gone or the reading is stopped."""
        driver = self._driver(ref)
        first = True
        failing_since: float | None = None
        while True:
            running.woken.clear()
            # The runner this pass asks: one started meanwhile is not the one
            # whose answer it brings back.
            launched = running.launched
            try:
                await subscription.drain(wait=0 if first else READ_WAIT_S)
            except (DeviceOffline, DeviceCallError, httpx.TransportError) as exc:
                running.answering = False
                now = time.monotonic()
                idle_gone = (
                    isinstance(exc, DeviceCallError)
                    and not isinstance(exc, DeviceNotReady)
                    and not running.owed
                )
                if failing_since is None:
                    # Waiting for a host to come back is this reading's job,
                    # not a failure of it: said once per outage, and its end
                    # once. A host that is off, a runner a cold host is still
                    # starting, the connection owner being replaced — at ERROR
                    # each was an alert about an ordinary moment.
                    (logger.info if idle_gone else logger.warning)(
                        "%s session %s waiting for %s: %s",
                        driver.label,
                        ref.home,
                        _waiting_for(exc),
                        exc,
                    )
                failing_since = failing_since or now
                await hand(Read(None, Reachable(False, _why(exc))))
                gone_after = running.spec.gone_after_s if running.spec else 120.0
                if running.owed and now - failing_since > gone_after:
                    logger.warning(
                        "a %s session on %s is out of reach: %s",
                        driver.label,
                        running.host,
                        exc,
                    )
                    self._forget(ref, running)
                    await hand(Read(None, Ended("out of reach")))
                    return
                if running.stopping:
                    return
                if idle_gone:
                    # The runner let its idle session go; nobody is waiting.
                    await self._wait(running, IDLE_GONE_READ_S)
                else:
                    await asyncio.sleep(RETRY_S)
                continue
            except Exception:
                running.answering = False
                # What the reader refused, or a mirror that failed: the cursor
                # moved only past what was taken, so the next pass re-reads.
                logger.exception("%s home=%s", driver.read_failure, ref.home)
                if running.stopping:
                    return
                await asyncio.sleep(RETRY_S)
                continue
            if failing_since is not None:
                failing_since = None
                logger.info("%s session %s resumed", driver.label, ref.home)
                await hand(Read(None, Reachable(True)))
            if first:
                first = False
                await hand(Read(None, CaughtUp()))
            heard = subscription.heard
            if isinstance(live := heard.get("live"), dict):
                await hand(
                    Read(
                        live.get("work_id"),
                        Writing(
                            tuple(live.get("blocks") or ()),
                            returned=tuple(live.get("returned") or ()),
                            branches=tuple(live.get("branches") or ()),
                            made=tuple(live.get("made") or ()),
                        ),
                    )
                )
            if heard.get("alive") is True and self._on_alive is not None:
                # The runner answered. It may have had nothing to answer with:
                # a turn that is thinking, or running a long command, writes no
                # record for the whole of it. The answer itself is the only
                # thing that can be said about such a turn, and saying it is
                # what keeps a quiet turn from reading as a stopped one.
                if (seat := running.seat) is not None:
                    self._on_alive(seat.conversation_id)
            if not heard.get("alive", True) and running.launched is launched:
                self._forget(ref, running)
                said = await driver.last_words(
                    self._wire(), running.host, ref, launched
                )
                await hand(Read(None, Ended("exited", said)))
                return
            if running.stopping:
                # Asked to stop: this pass landed what was there.
                return

    def _forget(self, ref: SessionRef, running: _Running) -> None:
        running.answering = False
        if self._running.get(ref) is running:
            del self._running[ref]

    async def _wait(self, running: _Running, delay: float) -> None:
        try:
            await asyncio.wait_for(running.woken.wait(), delay)
        except TimeoutError:
            return

    async def stop_listening(self, refs: Iterable[SessionRef]) -> None:
        """End the readings of these sessions, and leave the sessions running:
        the way out of a process that hands its work to another
        (`app.core.ownership`). Two readers of one session land what it says
        twice.

        A pass already under way finishes first, for up to
        ``STOP_LISTENING_S``. The landing cursor only moves past what was
        taken, so whatever this process had not landed is still unread for
        the process that listens next."""
        readings = []
        for ref in refs:
            running = self._running.get(ref)
            if running is None:
                continue
            # Marked on the session, not on a reading: one about to start
            # makes its one pass and ends there too.
            running.stopping = True
            self._wake(running)
            if running.reading is not None and not running.reading.done():
                readings.append(running.reading)
        if readings:
            _, stuck = await asyncio.wait(readings, timeout=STOP_LISTENING_S)
            for reading in stuck:
                reading.cancel()
            await asyncio.gather(*stuck, return_exceptions=True)


def _owned(seat: Seat | None) -> bool:
    """Whether the session reads into a conversation: an ownerless session's
    seat is the nil conversation (`sessions._seat`)."""
    return seat is not None and seat.conversation_id.int != 0


def _waiting_for(exc: Exception) -> str:
    if isinstance(exc, DeviceOffline):
        return "the device"
    if isinstance(exc, DeviceCallError):
        return "the runner"
    return "the connection owner"


def _why(exc: Exception) -> str:
    if isinstance(exc, DeviceOffline):
        return "device offline"
    if isinstance(exc, DeviceCallError):
        return f"runner not answering: {exc}"
    return f"device connection lost: {exc}"
