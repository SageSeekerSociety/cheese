"""Sessions on the central session host: started from a spec, spoken to, and
read from a cursor.

A session lives in a runner on the session host that outlives this process
(`driven/runner.py`). Starting one is running its harness's launch there
(``pi.launch``); a runner that is already up for the same model is used as it
is. A session sits idle for ``SessionSpec.idle_exit_s`` and then exits; its
conversation stays in its state directory, and the next ``send`` starts it
again on it.

Reading is the runner's journal from a cursor: each read is held at the runner
until there is news, and carries what the session is writing besides
(``Writing``). A runner that has exited, or stays out of reach for longer than
``SessionSpec.gone_after_s``, ends the reading (``Ended``). The journal
outlives the reader, so a reader that leaves loses the reading, never the
records.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from app.core.config import settings
from app.domain.agent.admission import HostMemory
from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import PI
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.pi.events import thread_of
from app.domain.agent.nonce import new_nonce
from app.domain.agent.session_host import pi
from app.domain.agent.session_host.contract import (
    Access,
    Cursor,
    Ended,
    HostFull,
    Prompt,
    Read,
    SessionError,
    SessionRef,
    SessionSpec,
    SessionStatus,
    Writing,
)

logger = logging.getLogger(__name__)

#: How long a launch may take: the first after a pin bump downloads the harness.
LAUNCH_TIMEOUT_S = 900
#: How long one read may be held at the runner.
READ_WAIT_S = 25.0


@dataclass
class _Running:
    """A session this process has started or found running."""

    device_id: str
    spec: SessionSpec
    #: The newest journal entry read; None before the first read.
    cursor: Cursor | None = None
    #: Whether anything has been said to the session yet.
    spoken_to: bool = False


def api_base() -> str:
    """The backend as the session host reaches it."""
    return (settings.agent_session_api_base or settings.connector_public_base).rstrip(
        "/"
    )


class SessionHost:
    """Every session on the session host this backend process talks to."""

    def __init__(self, hub=None, memory: HostMemory | None = None):
        self._hub = hub
        self._memory = memory
        self._running: dict[SessionRef, _Running] = {}
        self._locks: dict[SessionRef, asyncio.Lock] = {}

    @property
    def hub(self):
        if self._hub is None:
            from app.domain.agent.device_hub import device_hub

            self._hub = device_hub
        return self._hub

    def available(self) -> bool:
        host = settings.agent_session_device_id
        return bool(host and self.hub.is_online(host))

    # --- starting ------------------------------------------------------------

    async def start(self, ref: SessionRef, spec: SessionSpec, access: Access) -> None:
        """Have the session running: the one this process already reads, one
        another process started, or one started now. Raises ``HostFull`` when
        the host has no memory for it."""
        await self._ensure(ref, spec, access)

    async def _ensure(
        self, ref: SessionRef, spec: SessionSpec, access: Access
    ) -> _Running:
        _harness(ref)
        lock = self._locks.setdefault(ref, asyncio.Lock())
        async with lock:
            known = self._running.get(ref)
            if known is not None and known.spec.model == spec.model:
                known.spec = spec
                return known
            host = settings.agent_session_device_id
            if not host or not self.hub.is_online(host):
                raise SessionError("The session host is not connected")
            running = _Running(host, spec)
            status = await self._ping(ref, host)
            if status is None or status.get("model") != spec.model:
                await self._launch(ref, host, spec, access)
            await self._catch_up(ref, running)
            self._running[ref] = running
            return running

    async def _launch(
        self, ref: SessionRef, host: str, spec: SessionSpec, access: Access
    ) -> None:
        memory = self._memory or HostMemory(self.hub)
        if not await memory.can_start(spec.footprint.memory_mb):
            raise HostFull("The session host has no memory for another session")
        launch = pi.launch(ref, spec, access, api_base())
        started = time.monotonic()
        # The runner archive goes only to a state directory that lacks it: a
        # session's first start, or the first after a deploy.
        result = await self._run(host, launch.program(ship=False))
        if _last_line(result).get("runner") == "missing":
            await self._run(host, launch.program(ship=True))
        logger.info(
            "session started on the host home=%s in %.2fs",
            ref.home,
            time.monotonic() - started,
        )

    async def _run(self, host: str, program: str) -> dict:
        try:
            result = await self.hub.exec(
                host, ["python3", "-"], stdin=program, timeout=LAUNCH_TIMEOUT_S
            )
        except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
            raise SessionError(f"The session could not be started: {exc}") from exc
        if result.get("exit") != 0 or result.get("truncated"):
            raise SessionError(
                (result.get("stderr") or "the session did not start").strip()[-600:]
            )
        return result

    async def _catch_up(self, ref: SessionRef, running: _Running) -> None:
        """Read the journal to its end: where the next answer starts, and
        whether the session was ever spoken to (one started for a conversation
        that has history somewhere else has not)."""
        while True:
            page = (
                await self._call(
                    ref, running.device_id, "entries", {"since": running.cursor}
                )
            )["entries"]
            for record in page:
                running.cursor = record.get("id") or running.cursor
                message = record.get("message") or {}
                if not thread_of(record) and message.get("role") == "user":
                    running.spoken_to = True
            if len(page) < PAGE:
                return

    # --- speaking --------------------------------------------------------------

    async def send(
        self,
        ref: SessionRef,
        spec: SessionSpec,
        access: Access,
        prompt: Prompt,
        *,
        work_id: uuid.UUID,
    ) -> Cursor | None:
        """Say ``prompt`` to the session, starting it (again) when it is not
        running. Returns the cursor its answer is read from."""
        # The marker is how the runner knows which prompt the entries after it
        # answer (`runner.refresh`), as for a room's input.
        marker = new_nonce()
        for attempt in range(2):
            running = await self._ensure(ref, spec, access)
            text = prompt.text
            if prompt.preface and not running.spoken_to:
                text = f"{prompt.preface}\n\n{text}"
            params = {
                "input_id": str(prompt.id),
                "work_id": str(work_id),
                "text": f"{text}\n{marker}",
            }
            if prompt.acting is not None:
                params["platform_token"] = prompt.acting
            try:
                await self._call(ref, running.device_id, "send", params)
            except (DeviceOffline, DeviceCallError) as exc:
                self.release(ref)
                if attempt:
                    raise SessionError(
                        f"The session did not take the prompt: {exc}"
                    ) from exc
                continue
            running.spoken_to = True
            return running.cursor
        raise AssertionError("unreachable")

    async def stop(self, ref: SessionRef) -> bool:
        """Stop what the session is doing, whichever process started it; the
        session and its conversation stay. False when there was nothing to
        stop or the host could not be reached."""
        host = settings.agent_session_device_id
        if not host or not self.hub.is_online(host):
            return False
        try:
            answer = await self._call(ref, host, "abort", {}, timeout=15)
        except (DeviceOffline, DeviceCallError, TimeoutError):
            logger.warning("stopping a session on the host failed", exc_info=True)
            return False
        return bool(answer.get("aborted"))

    def release(self, ref: SessionRef) -> None:
        """Stop keeping track of the session; it exits once idle."""
        self._running.pop(ref, None)

    async def status(self, ref: SessionRef) -> SessionStatus | None:
        """The session as its runner says it is now; None when it is not
        running. Starts nothing."""
        host = settings.agent_session_device_id
        if not host or not self.hub.is_online(host):
            return None
        status = await self._ping(ref, host)
        if status is None:
            return None
        return SessionStatus(
            working=bool(status.get("working")), model=status.get("model") or ""
        )

    # --- reading ---------------------------------------------------------------

    async def read(self, ref: SessionRef, after: Cursor | None) -> AsyncGenerator[Read]:
        """What the session writes from ``after`` on, as it is written; the
        reading goes on until the caller stops it, or ends with ``Ended`` when
        the session is gone. Only a session this process started can be read."""
        running = self._running.get(ref)
        if running is None:
            raise SessionError("The session was not started by this process")
        translation = pi.Translation()
        cursor = after
        mark: str | None = None
        failing_since: float | None = None
        while True:
            try:
                answer = await self._call(
                    ref,
                    running.device_id,
                    "entries",
                    {"since": cursor, "wait": READ_WAIT_S, "live": mark},
                )
            except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
                # The connection to the host blinks; a runner that is gone
                # stays gone.
                now = time.monotonic()
                failing_since = failing_since or now
                if now - failing_since > running.spec.gone_after_s:
                    self.release(ref)
                    logger.warning("a session on the host is out of reach: %s", exc)
                    yield Read(cursor, None, Ended("out of reach"))
                    return
                await asyncio.sleep(1)
                continue
            failing_since = None
            live = answer.get("live")
            if isinstance(live, dict):
                mark = live.get("mark")
                yield Read(
                    cursor,
                    live.get("work_id"),
                    Writing(tuple(live.get("blocks") or ())),
                )
            for record in answer.get("entries") or []:
                cursor = record.get("id") or cursor
                running.cursor = cursor
                work = (record.get("cheese") or {}).get("work_id")
                for event in translation.events(record):
                    yield Read(cursor, work, event)
            if not answer.get("alive", True):
                self.release(ref)
                yield Read(cursor, None, Ended("exited"))
                return

    # --- the wire --------------------------------------------------------------

    async def _ping(self, ref: SessionRef, host: str) -> dict | None:
        try:
            status = await self._call(ref, host, "ping", {}, timeout=15)
        except (DeviceOffline, DeviceCallError, TimeoutError):
            return None
        return status if status.get("alive") else None

    async def _call(
        self,
        ref: SessionRef,
        host: str,
        method: str,
        params: dict,
        *,
        timeout: float = 660,
    ) -> dict:
        return await self.hub.call_executor(
            host, ref.state, method, params, timeout=timeout
        )


def _harness(ref: SessionRef) -> None:
    """Refuse a harness the core cannot start yet: a room's Claude Code and
    Codex sessions are still started by their own channels."""
    if ref.harness != PI:
        raise SessionError(f"{ref.harness} sessions are not started by the core yet")


def _last_line(result: dict) -> dict:
    """The launch's last line of output, as JSON; {} when it said nothing."""
    lines = (result.get("stdout") or "").strip().splitlines()
    return json.loads(lines[-1]) if lines else {}
