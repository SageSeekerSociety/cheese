"""Having a session run: started from a spec, or taken up as another process
left it.

``start`` is the one way a session begins — the one already running from the
same launch, or one launched now from the spec — and it waits for the host to
have memory for it when the spec says so. ``attach`` takes up a session this
process did not start, starting nothing, and ``adopt`` is what its harness has
to be told before this process reads it. Both raise ``RunnerUnsupported`` for a
runner that answered alive but cannot hold a read, because every read of a
session is one the runner holds until there is news (``LONG_POLL``).

``SessionHost`` forwards to these; the signatures and what they say are here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings
from app.domain.agent.device_hub import (
    DeviceCallError,
    DeviceNotReady,
    DeviceOffline,
)
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.session_host.contract import (
    Access,
    Ending,
    SessionError,
    SessionRef,
    SessionSpec,
    SessionStatus,
)
from app.domain.agent.session_host.driver import Launched, Reports
from app.domain.agent.session_host.sessions import (
    _mirror,
    _Running,
    _seat,
    key,
    with_pictures,
)

if TYPE_CHECKING:
    from app.domain.agent.session_host import SessionHost

logger = logging.getLogger(__name__)


class RunnerUnsupported(SessionError):
    """The runner a session was greeted by cannot hold a read (``LONG_POLL``),
    and every read of a session is one the runner holds until there is news."""


async def start(
    svc: SessionHost,
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
    spec = with_pictures(spec)
    driver = svc._driver(ref)
    lock = svc._locks.setdefault(ref, asyncio.Lock())
    async with lock:
        host = access.host or settings.agent_session_device_id
        if not host or not svc.hub.is_online(host):
            raise SessionError("The session host is not connected")
        running = svc._running.get(ref)
        known = (
            running.launched
            if running is not None and running.answering and running.host == host
            else None
        )
        if known is None and spec.footprint is not None:
            await svc._wait_for_memory(spec, on_wait, give_up)
        started = time.monotonic()
        # The runner being replaced, when this start replaces one.
        replacing = running if running is not None and known is None else None
        if replacing is not None:
            replacing.relaunching = True
        try:
            launched = await driver.launch(svc._wire(), host, ref, spec, access, known)
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
            if (
                running is None
                or running.host != host
                # Its reading heard the runner it had go while this one
                # started, and let it go with it.
                or svc._running.get(ref) is not running
            ):
                seat, acting = _seat(ref, access)
                running = _Running(
                    host,
                    launched,
                    seat,
                    acting,
                    _mirror(svc._mirrors, ref, access, driver),
                )
                svc._running[ref] = running
            if launched.lifecycle is not None and not launched.epoch:
                launched = await register(svc, host, ref, running, launched)
        finally:
            if replacing is not None:
                replacing.relaunching = False
        running.launched = launched
        running.spec, running.access = spec, access
        running.answering = True
        running.stopping = False
        return SessionStatus(
            working=False,
            model=spec.model,
            conversation=launched.conversation,
        )


async def register(
    svc: SessionHost, host: str, ref: SessionRef, running: _Running, launched: Launched
) -> Launched:
    """Register a runner that reports in the lifecycle protocol: issue its
    seat a new worker epoch and hand it to the runner, with the clocks it ends
    stuck work by. From here on only this registration changes anything.

    The epoch is issued first, so between the two a read under the epoch this
    replaces is refused. That refusal is this process's own doing, not another
    registration's (``running.registering``): the reading reads again under
    the new epoch, and the runner hands the same events over again."""
    epoch = await svc._register(key(ref, running.seat, running.acting))
    running.registering = epoch
    try:
        await svc._look(
            host,
            ref,
            "register",
            {
                "epoch": epoch,
                "watchdog": {
                    "no_progress_s": settings.agent_no_progress_s,
                    "unread_s": settings.agent_unread_grace_s,
                    "silent_s": settings.agent_stuck_silence_s,
                },
            },
        )
        launched = replace(launched, epoch=epoch)
        running.launched = launched
    finally:
        running.registering = None
    return launched


async def adopt(svc: SessionHost, found: list[tuple[SessionRef, Access]]) -> None:
    """Before reading again sessions another process started: have what
    keeps them on their hosts know them again (Claude Code's screens)."""
    by_driver: dict[str, list[tuple[str, Access]]] = {}
    for ref, access in found:
        host = access.host or settings.agent_session_device_id
        if host:
            by_driver.setdefault(ref.harness, []).append((host, access))
    for harness, sessions in by_driver.items():
        driver = svc._driver(SessionRef(harness, ""))
        await driver.adopt(svc._wire(), sessions)


async def attach(
    svc: SessionHost, ref: SessionRef, access: Access
) -> SessionStatus | None:
    """Read again a session another process started, as it is, starting
    nothing. None when its runner could not be asked; a status that is not
    ``alive`` when the runner answered that its harness process is gone."""
    driver = svc._driver(ref)
    host = access.host or settings.agent_session_device_id
    if not host or not svc.hub.is_online(host):
        return None
    try:
        status = await svc._look(host, ref, "ping", {})
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
            return SessionStatus(working=False, model="", alive=False, runner_gone=True)
        return None
    alive = status.get("alive")
    found = SessionStatus(
        working=driver.working(status),
        model=str(status.get("model") or ""),
        conversation=driver.conversation(status),
        work_id=status.get("work_id"),
        # The hub hands the runner's answer through unchecked: only an
        # explicit False says the process is gone.
        alive=alive is not False,
    )
    if alive is not True:
        return found if alive is False else None
    if isinstance(driver, Reports) and not driver.speaks(status):
        # A runner another release started: it does not report in the protocol
        # this one reads, and this release keeps nothing that reads another.
        # It is not taken up, and nothing it is doing is touched here: the room
        # lets work it is doing finish and replaces it once its ping says it is
        # idle (``room.sessions.lifecycle``), with one of this release's on the
        # same conversation (``ClaudeCodeDriver.launch``). What this reads of
        # it is the ping's handshake, which every protocol keeps
        # (``harness/driven/lifecycle.py``).
        return replace(
            found,
            outdated=True,
            epoch=int(status.get("worker_epoch") or 0),
            launch=status.get("launch"),
            endings=tuple(
                Ending(
                    str(ending["work"]),
                    ending.get("failure") is not None,
                    tuple(ending.get("inputs") or ()),
                )
                for ending in status.get("endings") or ()
            ),
        )
    launched = Launched(
        found.conversation,
        frozenset(status.get("capabilities") or ()),
        "",
        status.get("lifecycle"),
        status.get("launch"),
    )
    if LONG_POLL not in launched.capabilities:
        raise RunnerUnsupported(
            f"The {driver.label} runner on {host} at {ref.state} cannot hold "
            f"a read (it announced {sorted(launched.capabilities)}, without "
            f"{LONG_POLL!r}); it is not supported"
        )
    seat, acting = _seat(ref, access)
    running = svc._running.get(ref)
    if running is None or running.host != host:
        running = _Running(
            host,
            launched,
            seat,
            acting,
            _mirror(svc._mirrors, ref, access, driver),
        )
        svc._running[ref] = running
    if launched.lifecycle is not None:
        launched = await register(svc, host, ref, running, launched)
    running.launched = launched
    running.answering = True
    running.stopping = False
    running.owed = found.working
    return found


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
