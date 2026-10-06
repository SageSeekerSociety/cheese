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
"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import settings
from app.domain.agent.admission import HostMemory
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceNotReady,
    DeviceOffline,
)
from app.domain.agent.harness import CLAUDE_CODE, CODEX, HARNESSES, PI
from app.domain.agent.harness import SessionRef as Seat
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.harness.driven.subscription import Subscription
from app.domain.agent.harness.pi.events import thread_of
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
from app.domain.agent.session_host.claude_code import ClaudeCodeDriver
from app.domain.agent.session_host.codex import CodexDriver
from app.domain.agent.session_host.contract import (
    Access,
    HostFull,
    InputUnconfirmed,
    Prompt,
    SessionError,
    SessionRef,
    SessionSpec,
    SessionStatus,
    StartAbandoned,
)
from app.domain.agent.session_host.driver import Driver, Launched, Readers, Wire
from app.domain.agent.session_host.pi import PiDriver

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


class _NotThere(Exception):
    """A call no runner took: the host or the runner was not there."""


class RunnerUnsupported(SessionError):
    """The runner a session was greeted by cannot hold a read (``LONG_POLL``),
    and every read of a session is one the runner holds until there is news."""


@dataclass
class _Running:
    """A session this process started or found running."""

    host: str
    launched: Launched
    #: Who reads it: the place it is in, and the agent acting there.
    seat: Seat
    acting: str
    mirror: Path
    spec: SessionSpec | None = None
    access: Access | None = None
    #: Whether its runner has answered alive on every read since it was
    #: started or found: what lets a start from the same launch skip the host.
    answering: bool = True
    #: Something said to it is unanswered: its runner going out of reach then
    #: is a reader left waiting.
    owed: bool = False
    #: Whether anything was said to it before this process found it; None
    #: until asked.
    spoken_to: bool | None = None
    #: Cut short a reading's wait when something is said to the session.
    woken: asyncio.Event = field(default_factory=asyncio.Event)
    #: The reading under way, when there is one.
    subscription: Subscription | None = None
    reading: asyncio.Task | None = None
    stopping: bool = False


def keeps_memory(harness: str) -> bool:
    """Whether a harness's sessions keep memory as files the platform
    reconciles (``SessionHost.memory``): the one place the registry's bit is
    read, so the system prompt's memory section and the reconciliation cannot
    disagree about it."""
    entry = HARNESSES.get(harness)
    return bool(entry and entry.keeps_memory)


def _seat(ref: SessionRef, access: Access) -> tuple[Seat, str]:
    """The session as its subscription names what it reads: the place and the
    session's name in it, and the agent acting there. A session with no owner
    reads into no place."""
    owner = access.owner
    if owner is None:
        nobody = uuid.UUID(int=0)
        return Seat(nobody, nobody, "", harness=ref.harness), ""
    return (
        Seat(owner.project_id, owner.place_id, owner.name, harness=ref.harness),
        owner.handle,
    )


def _mirror(root: Path, ref: SessionRef, access: Access, driver: Driver) -> Path:
    """Where this backend keeps its copy of the session's journal, under
    ``root``. A session with an owner keeps it under the owner's place, by the
    machine and the agent it acts as there, so a reopened place starts a fresh
    one."""
    owner = access.owner
    if owner is None:
        return root / ref.home / driver.mirror
    return (
        root
        / str(owner.project_id)
        / str(owner.place_id)
        / ref.harness
        / hashlib.sha256(f"{owner.resource_id}{owner.handle}".encode()).hexdigest()
        / driver.mirror
    )


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
            from app.domain.agent.device_hub import device_hub

            self._hub = device_hub
        return self._hub

    def _driver(self, ref: SessionRef) -> Driver:
        if self._drivers is None:
            screens = self._screens
            if screens is None:
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
        from app.core.db import async_session_factory
        from app.domain.agent.machine_address import device_api_base

        factory = self._session_factory or async_session_factory
        async with factory() as db:
            return await device_api_base(db, host, settings.connector_public_base)

    def available(self) -> bool:
        host = settings.agent_session_device_id
        return bool(host and self.hub.is_online(host))

    def reads_on_accept(self, ref: SessionRef) -> bool:
        """Whether the runner taking an input is the session reading it; for
        the rest, the reading says when an input was read (``Received``)."""
        return self._driver(ref).receipt_on_accept

    # --- starting ------------------------------------------------------------

    async def start(
        self,
        ref: SessionRef,
        spec: SessionSpec,
        access: Access,
        *,
        on_wait: Callable[[], Awaitable[None]] | None = None,
        give_up: Callable[[], Awaitable[bool]] | None = None,
    ) -> SessionStatus:
        """Have the session running from ``spec``: the one already running,
        or one started now. A session that is not running waits up to
        ``spec.host_wait_s`` for the host to have memory for it, telling
        ``on_wait`` once when it starts waiting; past that it raises
        ``HostFull``, and ``StartAbandoned`` once ``give_up`` says so."""
        driver = self._driver(ref)
        lock = self._locks.setdefault(ref, asyncio.Lock())
        async with lock:
            host = access.host or settings.agent_session_device_id
            if not host or not self.hub.is_online(host):
                raise SessionError("The session host is not connected")
            running = self._running.get(ref)
            known = (
                running.launched
                if running is not None and running.answering and running.host == host
                else None
            )
            if known is None and spec.footprint is not None:
                await self._wait_for_memory(spec, on_wait, give_up)
            started = time.monotonic()
            launched = await driver.launch(self._wire(), host, ref, spec, access, known)
            if launched is not known:
                logger.info(
                    "session started on the host harness=%s home=%s in %.2fs",
                    ref.harness,
                    ref.home,
                    time.monotonic() - started,
                )
            if LONG_POLL not in launched.capabilities:
                raise RunnerUnsupported(
                    f"The {driver.label} runner on {host} at {ref.state} cannot "
                    f"hold a read (it announced {sorted(launched.capabilities)}, "
                    f"without {LONG_POLL!r}); it is not supported"
                )
            if running is None or running.host != host:
                seat, acting = _seat(ref, access)
                running = _Running(
                    host,
                    launched,
                    seat,
                    acting,
                    _mirror(self._mirrors, ref, access, driver),
                )
                self._running[ref] = running
            running.launched = launched
            running.spec, running.access = spec, access
            running.answering = True
            running.stopping = False
            return SessionStatus(
                working=False,
                model=spec.model,
                conversation=launched.conversation,
                takes_inputs=driver.takes_inputs(
                    {"input_protocol": launched.input_protocol}
                ),
            )

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

    async def adopt(self, found: list[tuple[SessionRef, Access]]) -> None:
        """Before reading again sessions another process started: have what
        keeps them on their hosts know them again (Claude Code's screens)."""
        by_driver: dict[str, list[tuple[str, Access]]] = {}
        for ref, access in found:
            host = access.host or settings.agent_session_device_id
            if host:
                by_driver.setdefault(ref.harness, []).append((host, access))
        for harness, sessions in by_driver.items():
            driver = self._driver(SessionRef(harness, ""))
            await driver.adopt(self._wire(), sessions)

    async def attach(self, ref: SessionRef, access: Access) -> SessionStatus | None:
        """Read again a session another process started, as it is, starting
        nothing. None when its runner could not be asked; a status that is not
        ``alive`` when the runner answered that its harness process is gone."""
        driver = self._driver(ref)
        host = access.host or settings.agent_session_device_id
        if not host or not self.hub.is_online(host):
            return None
        try:
            status = await self._wire().call(host, ref, "ping", {}, timeout=QUICK_S)
        except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
            # A runner that is not there is a session that sat idle and was let
            # go (``driven.runner``); the next message starts it again. Every
            # deploy finds dozens. A host that is away or not answering is
            # worth a warning.
            missing = isinstance(exc, DeviceCallError) and not isinstance(
                exc, DeviceNotReady
            )
            (logger.info if missing else logger.warning)(
                "%s session %s on %s could not be asked whether it runs: %s",
                driver.label,
                ref.home,
                host,
                exc,
            )
            if missing and _no_runner(exc, ref.state):
                # FB-56 forbids reading a question nobody answered as death:
                # an offline machine, a timeout, a connector not ready yet. This
                # is not that. The machine is online and answered that the
                # runner's socket does not exist, and a runner lives exactly as
                # long as its harness process (``driven.runner``), so the
                # conversation it ran is over.
                return SessionStatus(
                    working=False, model="", alive=False, runner_gone=True
                )
            return None
        alive = status.get("alive")
        found = SessionStatus(
            working=driver.working(status),
            model=str(status.get("model") or ""),
            conversation=driver.conversation(status),
            work_id=status.get("work_id"),
            takes_inputs=driver.takes_inputs(status),
            # The hub hands the runner's answer through unchecked: only an
            # explicit False says the process is gone.
            alive=alive is not False,
        )
        if alive is not True:
            return found if alive is False else None
        launched = Launched(
            found.conversation,
            frozenset(status.get("capabilities") or ()),
            "",
            status.get("input_protocol"),
        )
        if LONG_POLL not in launched.capabilities:
            raise RunnerUnsupported(
                f"The {driver.label} runner on {host} at {ref.state} cannot hold "
                f"a read (it announced {sorted(launched.capabilities)}, without "
                f"{LONG_POLL!r}); it is not supported"
            )
        seat, acting = _seat(ref, access)
        running = self._running.get(ref)
        if running is None or running.host != host:
            running = _Running(
                host,
                launched,
                seat,
                acting,
                _mirror(self._mirrors, ref, access, driver),
            )
            self._running[ref] = running
        else:
            running.launched = launched
        running.answering = True
        running.stopping = False
        running.owed = found.working
        return found

    # --- speaking --------------------------------------------------------------

    async def send(
        self, ref: SessionRef, prompt: Prompt, *, work_id: uuid.UUID
    ) -> None:
        """Say ``prompt`` to the session, as work ``work_id``: what it does
        about it is read (``read``). Raises ``InputUnconfirmed`` when the
        runner may have taken it and did not say so."""
        running = self._started(ref)
        text = prompt.text
        if prompt.preface and not await self._spoken_to(ref, running):
            text = f"{prompt.preface}\n\n{text}"
        try:
            await self._say(ref, running, "send", prompt, text, work_id)
        except _NotThere:
            if running.spec is None or running.access is None:
                raise InputUnconfirmed(accepted=False) from None
            # The runner let the session go since it was last read (it sat
            # idle): started again on its conversation, it is told once more.
            # The runner keeps the inputs it took by id, so one that did reach
            # it is not taken twice.
            running.answering = False
            await self.start(ref, running.spec, running.access)
            running = self._started(ref)
            try:
                await self._say(ref, running, "send", prompt, text, work_id)
            except _NotThere as exc:
                raise InputUnconfirmed(accepted=False) from exc.__cause__
        running.spoken_to = True

    async def steer(
        self, ref: SessionRef, prompt: Prompt, *, work_id: uuid.UUID
    ) -> None:
        """Say ``prompt`` to the session in the middle of work ``work_id``,
        without opening work of its own. Raises ``InputUnconfirmed`` as
        ``send`` does."""
        running = self._started(ref)
        method = self._driver(ref).steer
        try:
            await self._say(ref, running, method, prompt, prompt.text, work_id)
        except _NotThere as exc:
            raise InputUnconfirmed(accepted=False) from exc.__cause__

    async def _say(
        self,
        ref: SessionRef,
        running: _Running,
        method: str,
        prompt: Prompt,
        text: str,
        work_id: uuid.UUID,
    ) -> None:
        params: dict[str, Any] = {
            "input_id": str(prompt.id),
            "work_id": str(work_id),
            "text": text,
        }
        if prompt.images:
            params["images"] = self._driver(ref).images(prompt.images)
        if prompt.owes_reply:
            params["owes_reply"] = True
        if prompt.acting is not None:
            params["platform_token"] = prompt.acting
        running.owed = True
        try:
            await self._wire().call(running.host, ref, method, params)
        except (DeviceOffline, DeviceCallError) as exc:
            # Nothing on the host took it: the host is not connected, or the
            # runner is not there to answer.
            raise _NotThere() from exc
        except Exception as exc:
            # Even a transport error can follow admission at the runner.
            raise InputUnconfirmed(accepted=False) from exc
        finally:
            self._wake(running)

    def _started(self, ref: SessionRef) -> _Running:
        running = self._running.get(ref)
        if running is None:
            raise SessionError("The session was not started by this process")
        return running

    def answers(self, ref: SessionRef) -> bool | None:
        """Whether the session's runner answered its last read; False once it
        stopped — most often because it let its idle session go — and None
        when this process holds no such session."""
        running = self._running.get(ref)
        return None if running is None else running.answering

    def _wake(self, running: _Running) -> None:
        running.woken.set()
        if running.subscription is not None:
            running.subscription.unpark()

    async def _spoken_to(self, ref: SessionRef, running: _Running) -> bool:
        """Whether anything was said to the session yet: a session started
        for a conversation that has history somewhere else has not been."""
        if running.spoken_to is None:
            running.spoken_to = False
            since = None
            while True:
                page = (
                    await self._wire().call(
                        running.host, ref, "entries", {"since": since}
                    )
                )["entries"]
                for record in page:
                    since = record.get("id") or since
                    message = record.get("message") or {}
                    if not thread_of(record) and message.get("role") == "user":
                        running.spoken_to = True
                if len(page) < PAGE:
                    break
        return running.spoken_to

    async def stop(self, ref: SessionRef) -> bool:
        """Stop what the session is doing, whichever process started it; the
        session and its conversation stay. False when there was nothing to
        stop or the host could not be reached."""
        running = self._running.get(ref)
        host = running.host if running else settings.agent_session_device_id
        if not host or not self.hub.is_online(host):
            return False
        method, key = self._driver(ref).stop
        try:
            answer = await self._wire().call(host, ref, method, {}, timeout=QUICK_S)
        except (DeviceOffline, DeviceCallError, TimeoutError):
            logger.warning("stopping a session on the host failed", exc_info=True)
            return False
        return bool(answer.get(key))

    def release(self, ref: SessionRef) -> None:
        """Stop keeping track of the session; it exits once idle. A reading
        under way ends after its pass."""
        running = self._running.pop(ref, None)
        if running is not None:
            running.stopping = True
            self._wake(running)

    async def status(self, ref: SessionRef) -> SessionStatus | None:
        """The session as its runner says it is now; None when it is not
        running. Starts nothing."""
        running = self._running.get(ref)
        host = running.host if running else settings.agent_session_device_id
        if not host or not self.hub.is_online(host):
            return None
        driver = self._driver(ref)
        try:
            status = await self._wire().call(host, ref, "ping", {}, timeout=QUICK_S)
        except (DeviceOffline, DeviceCallError, TimeoutError):
            return None
        if not status.get("alive"):
            return None
        return SessionStatus(
            working=driver.working(status),
            model=str(status.get("model") or ""),
            conversation=driver.conversation(status),
            work_id=status.get("work_id"),
            takes_inputs=driver.takes_inputs(status),
        )

    async def memory(self, ref: SessionRef, request: dict) -> dict | None:
        """One reconciliation of the memory files the session keeps; None when
        its harness keeps none, or it cannot be reached. The platform's copy is
        still the truth then, and the session's edits stay on its disk until
        it is next reached."""
        running = self._running.get(ref)
        if running is None or not keeps_memory(ref.harness):
            return None
        try:
            return await self._wire().call(running.host, ref, "memory", request)
        except (DeviceCallError, DeviceOffline):
            return None

    async def control(self, ref: SessionRef, request: dict) -> dict:
        """One control request to the session, to its response."""
        running = self._started(ref)
        return await self._wire().call(
            running.host, ref, "control", {"request": request}
        )

    async def control_state(self, ref: SessionRef) -> dict:
        """What the session's controls show, from its mirror alone; {} for a
        harness that has none."""
        running = self._running.get(ref)
        driver = self._driver(ref)
        show = getattr(driver, "control_state", None)
        if running is None or show is None:
            return {}
        return await show(running.mirror)

    # --- reading ---------------------------------------------------------------

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
            if not heard.get("alive", True):
                self._forget(ref, running)
                await hand(Read(None, Ended("exited")))
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


def _no_runner(exc: Exception, state) -> bool:
    """The connector's own answer that the runner is not there: its socket
    does not exist (``cli/internal/host/executor_unix.go`` dials it by name),
    or the session's state directory itself does not (``executor.go`` resolves
    it with ``EvalSymlinks`` first, and reports its ``lstat``)."""
    text = str(exc)
    if "no such file or directory" not in text:
        return False
    return "cheese-execution-" in text or (
        "lstat " in text and Path(str(state)).name in text
    )


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
