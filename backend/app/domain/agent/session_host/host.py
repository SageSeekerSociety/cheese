"""Sessions on the session host: started from a spec, spoken to, and read.

A session lives in a runner on the session host that outlives this process
(`driven/runner.py`). Starting one is its harness's launch there (``Driver``):
a session already running from the same launch is used as it is, and one
running from another is started again from this one once it is idle. A session
sits idle for a while and then exits; its conversation stays in its state
directory, and the next start brings it back on it.

Reading is the runner's journal, mirrored on this backend and handed over one
item at a time (``read``). The mirror's landing cursor moves only past what the
reader took: an item it took is one it asked for the next of, or one it took
before it stopped reading; an item it failed on is thrown back into the reading
(``athrow``), re-read on the next pass, and stepped over once it has failed
often enough (`driven/subscription.py`). So a reader that dies anywhere re-reads
rather than skips, and the next process to read a session starts where this one
stopped landing.

Whether a session is gone is decided here, once, for every reader: its runner
says its harness process exited, or it stays out of reach for longer than the
spec's ``gone_after_s`` while something said to it is unanswered (``Ended``).

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
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.core.config import settings
from app.domain.agent.admission import HostMemory
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceNotReady,
    DeviceOffline,
)
from app.domain.agent.harness import CLAUDE_CODE, CODEX, PI
from app.domain.agent.harness.driven.subscription import Subscription
from app.domain.agent.machine_address import device_api_base
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
from app.domain.agent.session_host.driver import Driver, Readers, Wire
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
from app.domain.agent.session_host.sessions import (
    with_pictures as with_pictures,
)
from app.domain.agent.session_host.starting import (
    RunnerUnsupported as RunnerUnsupported,
)

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
#: How long the readings under way may take to finish their pass when this
#: process stops reading.
STOP_LISTENING_S = 5.0
#: Calls that only look: a ping, a stop.
QUICK_S = 15


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
    ):
        self._hub = hub
        self._memory = memory
        #: Where this backend keeps its copies of the sessions' journals.
        self._mirrors = mirrors or Path(settings.workspace_root) / ".harness"
        self._screens = screens
        self._session_factory = session_factory
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

    async def _api(self, host: str) -> str:
        """The platform as ``host`` reaches it."""
        # deferred-import: tests replace this name on app.core.db
        from app.core.db import async_session_factory

        factory = self._session_factory or async_session_factory
        async with factory() as db:
            return await device_api_base(db, host, settings.connector_public_base)

    def _look(self, host: str, ref: SessionRef, method: str, params: dict):
        """A call that only looks — a ping, a stop — which no runner holds
        open (``QUICK_S``)."""
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

    def answers(self, ref: SessionRef) -> bool | None:
        return speaking.answers(self, ref)

    async def stop(self, ref: SessionRef) -> bool:
        return await control.stop(self, ref)

    def release(self, ref: SessionRef) -> None:
        return control.release(self, ref)

    async def status(self, ref: SessionRef) -> SessionStatus | None:
        return await control.status(self, ref)

    async def memory(self, ref: SessionRef, request: dict) -> dict | None:
        return await control.memory(self, ref, request)

    async def control(self, ref: SessionRef, request: dict) -> dict:
        return await control.control(self, ref, request)

    async def control_state(self, ref: SessionRef) -> dict:
        return await control.control_state(self, ref)

    async def read(
        self, ref: SessionRef, *, recovered: bool = False
    ) -> AsyncGenerator[Read]:
        """What the session writes, from where this backend last landed, as it
        is written. The reading goes on until the caller stops it, this process
        stops listening (``stop_listening``), or the session is gone
        (``Ended``). ``recovered`` first settles what the journal kept of work
        that ended while nobody read it (Claude Code's).

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

        async def receipts(receipt):
            await hand(Read(str(receipt.identity.work_id), Received(receipt)))

        async def completions(completion):
            await hand(Read(str(completion.work_id), Completed(completion)))

        async def terminations(termination):
            await hand(Read(str(termination.work_id), Terminated(termination)))

        async def moved(work, marks):
            await hand(Read(str(work), Moved(marks)))

        async def announce():
            await hand(Read(None, ControlsMoved()))

        async def call(method: str, params: dict) -> dict:
            return await self._wire().call(running.host, ref, method, params)

        running.mirror.parent.mkdir(parents=True, exist_ok=True)
        subscription = driver.subscription(
            running.seat,
            running.acting,
            running.mirror,
            call,
            running.launched,
            Readers(
                consume,
                activity,
                receipts,
                completions,
                terminations,
                moved,
                announce,
            ),
        )
        running.subscription = subscription
        pump = asyncio.create_task(
            self._pump(ref, running, subscription, hand, recovered),
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
                try:
                    yield read
                except GeneratorExit:
                    taken.set_result(None)
                    raise
                except BaseException as exc:
                    taken.set_exception(exc)
                else:
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

    async def _pump(
        self,
        ref: SessionRef,
        running: _Running,
        subscription: Subscription,
        hand: Callable,
        recovered: bool,
    ) -> None:
        """Pass after pass over the session's journal, each landing what the
        reader took, until the session is gone or the reading is stopped."""
        driver = self._driver(ref)
        if recovered:
            await subscription.reconcile_history()
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
                    Read(live.get("work_id"), Writing(tuple(live.get("blocks") or ())))
                )
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
