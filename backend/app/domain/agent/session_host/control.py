"""Asking after a session, and letting it go.

``status`` is what its runner says it is now, without starting anything;
``stop`` takes the work away and leaves the session standing; ``memory`` and
``control`` hand a request to the runner and bring its answer back;
``control_state`` reads what the session's controls show off its mirror alone.
``release`` is this process forgetting a session it holds: it exits once idle,
and the reading under way ends after its pass.

Every call here that only looks goes through ``SessionHost._look``, and a host
that cannot be reached answers as the docstrings say rather than raising —
these run wherever a session is already in trouble.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.config import settings
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.session_host.contract import SessionRef, SessionStatus
from app.domain.agent.session_host.sessions import keeps_memory

if TYPE_CHECKING:
    from app.domain.agent.session_host import SessionHost

logger = logging.getLogger(__name__)


async def stop(svc: SessionHost, ref: SessionRef) -> bool:
    """Stop what the session is doing, whichever process started it; the
    session and its conversation stay. False when there was nothing to
    stop or the host could not be reached."""
    running = svc._running.get(ref)
    host = running.host if running else settings.agent_session_device_id
    if not host or not svc.hub.is_online(host):
        return False
    method, key = svc._driver(ref).stop
    try:
        answer = await svc._look(host, ref, method, {})
    except (DeviceOffline, DeviceCallError, TimeoutError):
        logger.warning("stopping a session on the host failed", exc_info=True)
        return False
    return bool(answer.get(key))


def release(svc: SessionHost, ref: SessionRef) -> None:
    """Stop keeping track of the session; it exits once idle. A reading
    under way ends after its pass."""
    running = svc._running.pop(ref, None)
    if running is not None:
        running.stopping = True
        svc._wake(running)


async def status(svc: SessionHost, ref: SessionRef) -> SessionStatus | None:
    """The session as its runner says it is now; None when it is not
    running. Starts nothing."""
    running = svc._running.get(ref)
    host = running.host if running else settings.agent_session_device_id
    if not host or not svc.hub.is_online(host):
        return None
    driver = svc._driver(ref)
    try:
        status = await svc._look(host, ref, "ping", {})
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


async def memory(svc: SessionHost, ref: SessionRef, request: dict) -> dict | None:
    """One reconciliation of the memory files the session keeps; None when
    its harness keeps none, or it cannot be reached. The platform's copy is
    still the truth then, and the session's edits stay on its disk until
    it is next reached."""
    running = svc._running.get(ref)
    if running is None or not keeps_memory(ref.harness):
        return None
    try:
        return await svc._wire().call(running.host, ref, "memory", request)
    except (DeviceCallError, DeviceOffline):
        return None


async def control(svc: SessionHost, ref: SessionRef, request: dict) -> dict:
    """One control request to the session, to its response."""
    running = svc._started(ref)
    return await svc._wire().call(running.host, ref, "control", {"request": request})


async def control_state(svc: SessionHost, ref: SessionRef) -> dict:
    """What the session's controls show, from its mirror alone; {} for a
    harness that has none."""
    running = svc._running.get(ref)
    driver = svc._driver(ref)
    show = getattr(driver, "control_state", None)
    if running is None or show is None:
        return {}
    return await show(running.mirror)
