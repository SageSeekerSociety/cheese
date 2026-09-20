"""``DeviceHub`` — the server end of the frozen ``link.Msg`` protocol (P3).

One long-lived control channel per connected device, over which the server opens
*screens* (each a hosted ``claude``), hands a screen its next prompt, relays a
screen's raw terminal bytes to browser viewers (the 现场 — relayed, never parsed),
and runs one-shot ``exec`` on the device. The wire shape is the ``cli/``
``link.Msg`` contract (``device_link``).

Ported from the reference ``app/agent/hub.py`` and kept I/O-free: it depends only on
two tiny transport Protocols, so it is exercised with in-process fakes — no
WebSocket, no device, no DB. Perception of what the agent *does* flows through our
Claude Code hooks (``hook_events``), NOT through reading the screen, so the raw
screen channel here serves only the human viewer.

Our adaptations vs the reference:
  * a screen carries our identity shape — ``agent_user_id: uuid`` + ``agent_handle``
    (the authorship key) — plus its ``project_id``/``topic_id`` and a ``hook_key``
    (the token the device's ``cheese-hook`` posts under, so hooks route to the turn);
  * ``call_screen`` returns the call id so the caller (DeviceChannel) can correlate
    an ``rpc.result`` (used to await a ``prompt`` acknowledgement).
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.agent import connector_build, device_link

logger = logging.getLogger(__name__)


class DeviceOffline(RuntimeError):
    """The device has no live link, so a frame to it would go nowhere.

    Raised by the awaited calls (``exec``) instead of letting them wait out
    their timeout: ``HubDevice.send`` drops a frame to a device with no
    transport, and a caller that then waits 35s and reports "the connector
    did not answer" has described the opposite of what happened."""

    def __init__(self, device_id: str) -> None:
        super().__init__(f"device {device_id} is offline")
        self.device_id = device_id


class DeviceCallError(RuntimeError):
    """The machine answered a call with a failure of its own.

    「dial unix …sock: no such file」 is the connector saying the runner's
    socket is not there yet; 「lstat …/.cheese/executor: no such file」 that the
    home it was asked about is gone. Those are answers, and the message is the
    whole of what the person in the room can act on. Raised as a bare
    RuntimeError they were the owner's unhandled 500, the backend's unhandled
    500 on top of it, and two alerts describing this server for every one the
    machine sent — 17 alerts folding 29 more repeats on 2026-09-18 alone, with
    the machine's words cut out of the ones that reached the room.

    A subclass, so every ``except RuntimeError`` that already waits one of
    these out keeps doing so.
    """


class DeviceNotReady(DeviceCallError):
    """The link is up, but this machine cannot serve calls yet.

    A connector that has just dialled in finishes updating itself before it can
    run anything, and a call that arrives in that window has nothing to reach.
    It is the same standing as the machine being away — the caller's next poll,
    a second or two later, finds it ready — but as a bare RuntimeError it was an
    unhandled 500 and one alert per release: 「Device connector must finish
    updating before execution」 arrived that way at 01:47 UTC on 2026-09-20,
    seconds after the owner was replaced and the fleet re-attached.
    """


def _read_a_failure_nobody_awaited(future: asyncio.Future[Any]) -> None:
    """A call registers its future and then writes to the link; when the write
    itself finds the link dead, ``drop_transport`` fails that very future and
    the write raises ``DeviceOffline`` — so the caller leaves through the send
    and never awaits the future it registered. asyncio reports that at garbage
    collection as 「Future exception was never retrieved」, an ERROR with no
    route and a traceback into the collector, for a device the caller already
    reported offline. Reading the exception here is what makes that untrue;
    a future the caller did await answers the same thing twice for free."""
    if future.done() and not future.cancelled():
        future.exception()


def configure_subscription_cleanup(remote_hub: Any) -> None:
    """Wire business subscription cleanup without coupling the RPC transport."""
    from app.domain.agent.harness.claude_code import (
        drop_device_subscriptions,
        drop_screen_subscriptions,
    )

    remote_hub.set_subscription_cleanup_callbacks(
        drop_device=drop_device_subscriptions,
        drop_screen=drop_screen_subscriptions,
    )


PROTOCOL_VERSION = device_link.PROTOCOL_VERSION


class DeviceTransport(Protocol):
    """A live device control channel. Satisfied by a ``fastapi.WebSocket`` adapter
    and trivially fakeable.

    ``send_json`` raises ``ConnectionError`` when the channel can no longer carry
    a frame; the hub then treats the device as gone."""

    async def send_json(self, msg: dict[str, Any]) -> None: ...


class ViewerTransport(Protocol):
    """A browser terminal viewer. It only ever *receives* raw screen bytes."""

    async def send_bytes(self, data: bytes) -> None: ...


@dataclass
class HubScreen:
    sid: str
    device_id: str
    command: list[str]
    token: str  # the CHEESE_SCREEN value injected into the screen
    # A screen *is* an agent (一个 agent 是一个屏幕): it acts as one agent-user in its
    # project/topic. Attribution of the screen's cheese-api calls keys on
    # agent_user_id; hooks route by hook_key (see DeviceChannel).
    agent_user_id: int
    agent_handle: str
    project_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    resource_id: uuid.UUID | None = None
    hook_key: str = ""
    # UNIX expiry of the model credential the screen's `claude` was LAUNCHED with
    # (its `CHEESE_TOKEN_EXPIRES`). A bare `claude` reads that credential — the
    # HTTPS_PROXY CONNECT password / CLAUDE_CODE_OAUTH_TOKEN — ONCE at startup and
    # never re-reads it, and a reused screen is only reasserted (an adopt-create),
    # never relaunched, so once this passes the process is a corpse
    # that 407s/401s every turn while still alive. The DeviceChannel stamps it at
    # open time and folds it into the reuse decision (retire + reopen past it),
    # and the zero-output fuse reads it to fast-fail with the true reason (#388).
    # `None` = never recorded (a screen adopted after a server restart, or a dev
    # token with no decodable expiry) → treated as fresh, never retired on it.
    credential_expires: int | None = None
    agent_configuration: str = ""
    execution_target: dict | None = None
    closing: bool = False
    viewers: set[ViewerTransport] = field(default_factory=set)


@dataclass
class HubDevice:
    device_id: str
    transport: DeviceTransport | None = None
    # What a person calls this machine, as the connector route knows it at attach
    # time. Kept here so a failure on the link can name the machine without a
    # database read on a path that is already failing.
    name: str = ""
    proto: int | None = None
    # What the connector said about itself in `hello`: the sha256 of its own
    # executable and the `<os>-<arch>` it was built for. Both empty from a
    # connector built before it announced either.
    build: str = ""
    target: str = ""
    executor: bool = False
    # Whether this CONNECTION has already been told to update itself. Reset on
    # every attach, so a machine whose self-update failed is told again the next
    # time it dials in rather than once and never again.
    update_pushed: bool = False
    # Last time the device sent any frame (hello/heartbeat/…). A liveness signal for
    # ops/UX: `is_online` already tracks the socket; this dates the last contact so a
    # future reaper can distinguish a wedged-but-connected device from a healthy one.
    last_seen: float = 0.0
    screens: dict[str, HubScreen] = field(default_factory=dict)
    exec_seq: int = 0
    call_seq: int = 0
    file_seq: int = 0
    exec_pending: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict
    )
    call_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    file_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    executor_pending: dict[str, tuple[asyncio.Future[Any], bytearray]] = field(
        default_factory=dict
    )
    session_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connection_generation: int = 0

    async def send(self, msg: dict[str, Any]) -> None:
        # Serialize sends to one device: a WebSocket is not safe for concurrent
        # writes, and the orchestrator, exec, and every viewer's fan-out all send
        # here. Without this, interleaved frames corrupt the channel.
        async with self.send_lock:
            transport = self.transport
            if transport is None:
                return
            try:
                await transport.send_json(msg)
            except ConnectionError as exc:
                # The receive loop only learns of a dead link when the peer says
                # so; a peer that vanished never does, and then the socket stays
                # attached for good — every caller that trusts ``is_online`` sends
                # into it and fails, once a minute for a storage sweep. A failed
                # send is the proof the receive loop never gets.
                self.drop_transport(transport)
                raise DeviceOffline(self.device_id) from exc

    def drop_transport(self, transport: DeviceTransport) -> bool:
        """Forget ``transport`` if it is still the live one; fail what waited on it."""
        if self.transport is not transport:
            return False
        self.transport = None
        for future, _ in self.executor_pending.values():
            if not future.done():
                future.set_exception(DeviceOffline(self.device_id))
        for future in self.session_pending.values():
            if not future.done():
                future.set_exception(DeviceOffline(self.device_id))
        return True


class DeviceHub:
    def __init__(self) -> None:
        self._devices: dict[str, HubDevice] = {}
        self._screens: dict[str, HubScreen] = {}  # sid -> screen (across devices)
        self._by_screen_token: dict[str, HubScreen] = {}

    def _device(self, device_id: str) -> HubDevice:
        return self._devices.setdefault(device_id, HubDevice(device_id=device_id))

    # -- device connection -------------------------------------------------

    async def attach_device(
        self, device_id: str, transport: DeviceTransport, *, name: str = ""
    ) -> None:
        device = self._device(device_id)
        if device.transport is not None and device.transport is not transport:
            for future, _ in device.executor_pending.values():
                if not future.done():
                    future.set_exception(DeviceOffline(device_id))
            for future in device.session_pending.values():
                if not future.done():
                    future.set_exception(DeviceOffline(device_id))
        device.transport = transport
        device.connection_generation += 1
        device.executor = False
        if name:
            device.name = name
        device.update_pushed = False
        await device.send(device_link.welcome())

    async def detach_device(self, device_id: str, transport: DeviceTransport) -> None:
        device = self._devices.get(device_id)
        if device is not None and device.drop_transport(transport):
            if not settings.device_connection_owner:
                from app.domain.agent.harness.claude_code import (
                    drop_device_subscriptions,
                    drop_screen_subscriptions,
                )

                for screen in list(device.screens.values()):
                    await drop_screen_subscriptions(screen)
                await drop_device_subscriptions(device_id)

    def is_online(self, device_id: str) -> bool:
        device = self._devices.get(device_id)
        return device is not None and device.transport is not None

    def online_device_ids(self) -> list[str]:
        return [d.device_id for d in self._devices.values() if d.transport is not None]

    def device_name(self, device_id: str) -> str:
        """The machine's name as announced at attach, or its id when unknown."""
        device = self._devices.get(device_id)
        return device.name if device is not None and device.name else device_id

    def last_seen_age(self, device_id: str) -> float | None:
        """Seconds since the device last sent any frame; None when it never has."""
        device = self._devices.get(device_id)
        if device is None or not device.last_seen:
            return None
        return asyncio.get_event_loop().time() - device.last_seen

    # -- screens (server -> device) ----------------------------------------

    async def open_screen(
        self,
        device_id: str,
        command: list[str],
        *,
        agent_user_id: int,
        agent_handle: str,
        project_id: uuid.UUID | None = None,
        topic_id: uuid.UUID | None = None,
        hook_key: str = "",
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> HubScreen:
        """Open a screen (an agent) on the device acting as ``agent_user_id``: mint
        its per-screen token, ship the command, and inject ``env`` into the screen
        process. The token becomes ``CHEESE_SCREEN`` inside the screen so a call from
        within it proves which screen it belongs to."""
        device = self._device(device_id)
        sid = "s" + uuid.uuid4().hex[:8]
        screen = HubScreen(
            sid=sid,
            device_id=device_id,
            command=command,
            token=uuid.uuid4().hex,
            agent_user_id=agent_user_id,
            agent_handle=agent_handle,
            project_id=project_id,
            topic_id=topic_id,
            hook_key=hook_key,
        )
        device.screens[sid] = screen
        self._screens[sid] = screen
        self._by_screen_token[screen.token] = screen
        await device.send(
            device_link.session_create(
                sid=sid,
                command=command,
                screen_token=screen.token,
                cols=cols,
                rows=rows,
                env=env,
            )
        )
        return screen

    async def reassert_screen(
        self,
        screen: HubScreen,
        *,
        command: list[str],
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        """Re-send an existing screen's ``session.create`` with ``adopt`` set — the
        frozen cli's designed re-provision path. The registry alone never proves the
        device still runs a screen: the connector process may have restarted (its
        tmux sessions outlive it, but its in-memory session map does not, so it
        knows nothing about this sid until a create makes it re-adopt), or the
        original create may not have been delivered at all (``open_screen``
        registers before an unacknowledged send).
        The cli silently drops ``rpc.call`` for a sid it does not know, so prompting
        a lost screen strands the turn in a bare timeout. An adopt-create is
        idempotent on the device: a live session keeps running untouched; a lost one
        is respawned under the SAME sid + screen token, so viewers and attribution
        stay intact."""
        screen.command = command
        await self._device(screen.device_id).send(
            device_link.session_create(
                sid=screen.sid,
                command=command,
                screen_token=screen.token,
                cols=cols,
                rows=rows,
                env=env,
                adopt=True,
            )
        )

    def adopt_screen(
        self,
        device_id: str,
        sid: str,
        *,
        token: str,
        agent_user_id: int,
        agent_handle: str,
        command: list[str] | None = None,
        project_id: uuid.UUID | None = None,
        topic_id: uuid.UUID | None = None,
        resource_id: uuid.UUID | None = None,
        hook_key: str = "",
        credential_expires: int | None = None,
        agent_configuration: str = "",
        execution_target: dict | None = None,
    ) -> HubScreen:
        """Re-register a screen the *device* is still running after the server lost its
        in-memory state (a restart). The frozen cli auto-reconnects its control channel
        and re-announces the screens it kept alive; adopting rebinds the sid + its
        screen token to the agent identity so viewers/attribution work again without
        restarting the screen. Idempotent per (device, sid).

        The channel checks room ownership against the DB before adopting the
        identity retained by the device's tmux session.
        """
        device = self._device(device_id)
        existing = device.screens.get(sid)
        if existing is not None:
            if (existing.project_id, existing.topic_id, existing.token) != (
                project_id,
                topic_id,
                token,
            ):
                raise ValueError("screen identity changed during recovery")
            return existing
        if sid in self._screens or token in self._by_screen_token:
            raise ValueError("screen identity belongs to another device")
        screen = HubScreen(
            sid=sid,
            device_id=device_id,
            command=command or [],
            token=token,
            agent_user_id=agent_user_id,
            agent_handle=agent_handle,
            project_id=project_id,
            topic_id=topic_id,
            resource_id=resource_id,
            hook_key=hook_key,
            credential_expires=credential_expires,
            agent_configuration=agent_configuration,
            execution_target=execution_target,
        )
        device.screens[sid] = screen
        self._screens[sid] = screen
        self._by_screen_token[token] = screen
        return screen

    def update_screen(
        self,
        sid: str,
        *,
        resource_id: uuid.UUID | None,
        execution_target: dict | None,
        credential_expires: int | None = None,
        agent_configuration: str | None = None,
    ) -> HubScreen:
        screen = self._screens.get(sid)
        if screen is None:
            raise KeyError("screen not found")
        screen.resource_id = resource_id
        screen.execution_target = execution_target
        if credential_expires is not None:
            screen.credential_expires = credential_expires
        if agent_configuration is not None:
            screen.agent_configuration = agent_configuration
        return screen

    async def close_screen(self, device_id: str, sid: str) -> bool:
        """Forget a screen only after its owner confirms that it has stopped."""
        device = self._device(device_id)
        screen = device.screens.get(sid)
        if screen is not None:
            screen.closing = True
        await self.session_request(device_id, device_link.session_close(sid))
        if screen is None:
            return False
        device.screens.pop(sid, None)
        self._screens.pop(sid, None)
        self._by_screen_token.pop(screen.token, None)
        if not settings.device_connection_owner:
            from app.domain.agent.harness.claude_code import (
                drop_screen_subscriptions,
            )

            await drop_screen_subscriptions(screen)
        return True

    async def session_request(
        self, device_id: str, message: dict[str, Any], *, timeout: float = 30
    ) -> Any:
        device = self._device(device_id)
        if device.transport is None:
            raise DeviceOffline(device_id)
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        device.session_pending[request_id] = future
        try:
            await device.send({**message, "id": request_id})
            return await asyncio.wait_for(future, timeout)
        finally:
            device.session_pending.pop(request_id, None)
            _read_a_failure_nobody_awaited(future)

    async def list_screens(self, device_id: str) -> list[dict[str, Any]]:
        return await self.session_request(device_id, {"t": "session.list"})

    def screen_by_token(self, token: str) -> HubScreen | None:
        return self._by_screen_token.get(token)

    def screen(self, sid: str) -> HubScreen | None:
        return self._screens.get(sid)

    def all_online_screens(self) -> list[HubScreen]:
        return [s for s in self._screens.values() if self.is_online(s.device_id)]

    def screens_in_project(self, project_id: uuid.UUID) -> list[HubScreen]:
        return [
            s
            for s in self._screens.values()
            if s.project_id == project_id and self.is_online(s.device_id)
        ]

    def screens_for_topic(self, topic_id: uuid.UUID) -> list[HubScreen]:
        """Include offline screens so a close can remain pending until reconnect."""
        return [s for s in self._screens.values() if s.topic_id == topic_id]

    async def call_screen(
        self, device_id: str, sid: str, name: str, args: list[Any]
    ) -> str:
        """Server→screen call (``prompt``). Returns the call id so the caller may
        await the matching ``rpc.result`` via ``await_call``."""
        device = self._device(device_id)
        device.call_seq += 1
        call_id = f"srv{device.call_seq}"
        await device.send(device_link.rpc_call(sid, call_id, name, args))
        return call_id

    async def await_call(
        self, device_id: str, call_id: str, *, timeout: float = 30
    ) -> Any:
        """Await the screen's ``rpc.result`` for a prior ``call_screen``. Raises on
        device error or timeout."""
        device = self._device(device_id)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        device.call_pending[call_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            device.call_pending.pop(call_id, None)

    async def put_file(
        self,
        device_id: str,
        sid: str,
        path: str,
        data: bytes,
        *,
        timeout: float = 30,
    ) -> Any:
        """Atomically stage one file under the screen's workspace and await ack."""
        device = self._device(device_id)
        device.file_seq += 1
        file_id = f"f{device.file_seq}"
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        device.file_pending[file_id] = future
        try:
            await device.send(device_link.file_put(sid, file_id, path, data))
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            device.file_pending.pop(file_id, None)

    # -- exec (server -> device, awaited) ----------------------------------

    async def exec(
        self,
        device_id: str,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60,
        stdin: str | None = None,
    ) -> dict[str, Any]:
        """One-shot command on the device → ``{stdout, stderr, exit, truncated}``.

        Raises ``DeviceOffline`` at once when the device has no link, rather than
        sending into the void and timing out ``timeout``+5s later."""
        device = self._device(device_id)
        if device.transport is None:
            raise DeviceOffline(device_id)
        device.exec_seq += 1
        eid = f"e{device.exec_seq}"
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        device.exec_pending[eid] = fut
        try:
            await device.send(
                device_link.exec_cmd(
                    exec_id=eid,
                    command=argv,
                    timeout=int(timeout),
                    cwd=cwd,
                    env=env,
                    stdin=stdin,
                )
            )
            return await asyncio.wait_for(fut, timeout=timeout + 5)
        except TimeoutError:
            await device.send(device_link.exec_cancel(eid))
            raise
        finally:
            device.exec_pending.pop(eid, None)

    async def call_executor(
        self,
        device_id: str,
        state: str,
        method: str,
        params: dict,
        *,
        timeout: float = 660,
        trace_id: str | None = None,
    ) -> dict:
        device = self._device(device_id)
        if device.transport is None:
            raise DeviceOffline(device_id)
        identifier = trace_id or "execution-" + uuid.uuid4().hex
        if not device.executor:
            raise DeviceNotReady(
                f"device {device_id} is still updating its connector; "
                "execution is available once it reports ready"
            )
        future = asyncio.get_running_loop().create_future()
        device.executor_pending[identifier] = (future, bytearray())
        # The stage lines are DEBUG, and `device_error` below is not. They carry
        # raw `mono_ns` for someone to subtract while chasing latency (#951) —
        # a debugging artifact, not an event an operator acts on. At INFO they
        # made this process's log unreadable: measured 2026-09-15, four of them
        # per call meant a 600-line tail of the owner held TWELVE SECONDS, and
        # the refused-socket RuntimeError behind a room that would not answer
        # had already scrolled out of reach of the only probe that can read it.
        try:
            logger.debug(
                "execution_timing stage=device_send_start trace=%s mono_ns=%d",
                identifier,
                time.monotonic_ns(),
            )
            await device.send(
                {
                    "t": "execution.call",
                    "id": identifier,
                    "path": state,
                    "stdin": json.dumps({"method": method, "params": params}),
                    "timeout": int(timeout),
                }
            )
            logger.debug(
                "execution_timing stage=device_sent trace=%s mono_ns=%d",
                identifier,
                time.monotonic_ns(),
            )
            return await asyncio.wait_for(future, timeout)
        except (TimeoutError, asyncio.CancelledError):
            await device.send(device_link.exec_cancel(identifier))
            raise
        finally:
            device.executor_pending.pop(identifier, None)
            _read_a_failure_nobody_awaited(future)

    # -- viewers (browser <-> device screen) -------------------------------

    async def attach_viewer(
        self,
        device_id: str,
        sid: str,
        viewer: ViewerTransport,
        *,
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        """Attach a browser viewer to a screen. The first viewer triggers a
        ``screen.subscribe`` carrying its size (the device attaches a tmux client)."""
        screen = self._require_screen(device_id, sid)
        first = not screen.viewers
        screen.viewers.add(viewer)
        if first:
            await self._device(device_id).send(
                device_link.screen_subscribe(sid, cols, rows)
            )

    async def detach_viewer(
        self, device_id: str, sid: str, viewer: ViewerTransport
    ) -> None:
        screen = self._screens.get(sid)
        if screen is None:
            return
        screen.viewers.discard(viewer)
        device = self._device(screen.device_id)
        if not screen.viewers:
            await device.send(device_link.screen_unsubscribe(sid))

    async def viewer_input(self, device_id: str, sid: str, data: bytes) -> None:
        await self._device(device_id).send(device_link.screen_input(sid, data))

    async def viewer_resize(
        self, device_id: str, sid: str, cols: int, rows: int
    ) -> None:
        await self._device(device_id).send(device_link.screen_resize(sid, cols, rows))

    # -- device -> server (inbound) ----------------------------------------

    async def on_device_message(self, device_id: str, m: dict[str, Any]) -> None:
        """Dispatch one inbound ``link.Msg`` from the device."""
        device = self._device(device_id)
        # Any inbound frame is a liveness signal (heartbeats included) — monotonic.
        device.last_seen = asyncio.get_event_loop().time()
        msg = device_link.LinkMsg.parse(m)
        screen = device.screens.get(msg.sid)

        if msg.t == "hello":
            device.proto = msg.v
            device.build = msg.build
            device.target = msg.target
            device.executor = msg.executor
            if device.proto not in (None, PROTOCOL_VERSION):
                self._on_version_skew(device_id, device.proto)
            await self._update_if_stale(device, msg)
            return
        if msg.t == "session.error":
            # The device could not start (or attach) this screen. There is no
            # pending future to fail — the turn discovers the loss when its prompt
            # times out — but dropping the one frame that says WHY turns that
            # timeout into a blank error, so at least keep the reason in the log.
            logger.warning(
                "device %s screen %s reported session.error: %s",
                device_id,
                msg.sid,
                msg.error,
            )
            return
        if msg.t in ("heartbeat", "session.ready"):
            return
        if msg.t == "exec.result":
            fut = device.exec_pending.get(msg.id)
            if fut is not None and not fut.done():
                fut.set_result(
                    {
                        "stdout": msg.stdout,
                        "stderr": msg.stderr,
                        "exit": msg.exit,
                        "truncated": msg.truncated,
                    }
                )
            return
        if msg.t in {"execution.data", "execution.result"}:
            pending = device.executor_pending.get(msg.id)
            if pending is None or pending[0].done():
                return
            future, data = pending
            try:
                if msg.t == "execution.data":
                    if not data:
                        logger.debug(
                            "execution_timing stage=device_first_data "
                            "trace=%s mono_ns=%d",
                            msg.id,
                            time.monotonic_ns(),
                        )
                    data.extend(base64.b64decode(msg.data, validate=True))
                elif msg.error:
                    logger.info(
                        "execution_timing stage=device_error trace=%s mono_ns=%d",
                        msg.id,
                        time.monotonic_ns(),
                    )
                    raise DeviceCallError(msg.error)
                else:
                    logger.debug(
                        "execution_timing stage=device_complete trace=%s mono_ns=%d",
                        msg.id,
                        time.monotonic_ns(),
                    )
                    response = json.loads(data)
                    if "error" in response:
                        raise DeviceCallError(response["error"])
                    future.set_result(response["result"])
            except (ValueError, KeyError, RuntimeError) as exc:
                future.set_exception(exc)
            return
        if msg.t == "screen.data" and screen is not None:
            await self._fan_out_screen_data(screen, msg.decoded_data())
            return
        if msg.t == "rpc.result":
            fut = device.call_pending.get(msg.id)
            if fut is not None and not fut.done():
                if msg.error:
                    fut.set_exception(DeviceCallError(msg.error))
                else:
                    fut.set_result(msg.value)
            return
        if msg.t in ("file.result", "session.result"):
            pending = (
                device.file_pending
                if msg.t == "file.result"
                else device.session_pending
            )
            fut = pending.get(msg.id)
            if fut is not None and not fut.done():
                if msg.error:
                    fut.set_exception(DeviceCallError(msg.error))
                else:
                    fut.set_result(msg.value)
            return

    async def _fan_out_screen_data(self, screen: HubScreen, raw: bytes) -> None:
        dead: list[ViewerTransport] = []
        for viewer in list(screen.viewers):
            try:
                await viewer.send_bytes(raw)
            except Exception:  # noqa: BLE001 — a dead viewer is dropped, not fatal
                dead.append(viewer)
        for viewer in dead:
            screen.viewers.discard(viewer)

    def _require_screen(self, device_id: str, sid: str) -> HubScreen:
        screen = self._device(device_id).screens.get(sid)
        if screen is None:
            raise KeyError(f"no screen {sid!r} on device {device_id!r}")
        return screen

    async def _update_if_stale(
        self, device: HubDevice, msg: device_link.LinkMsg
    ) -> None:
        """Tell a machine whose connector is not the one we serve to replace it.

        Nothing else closes the gap between what the server sends and what the
        far end can receive. A connector drops a frame it does not recognise
        without answering it, so drift surfaces as a timeout somewhere
        unrelated — an image that never arrived, a call that never returned —
        and no error anywhere names a version. Left to a person to notice, a
        machine stays behind for as long as nobody looks: one ran a build from
        the day before ``file.put`` merged for two weeks, and every image
        attached to any topic on it was staged into a twenty-second silence.

        Only ever on a definite answer. A connector that identifies itself but
        whose bytes we cannot compare is left alone — telling it to update on a
        half-answer would re-exec the machine on every reconnect and never
        converge, which is worse than the drift.
        """
        if device.update_pushed:
            return
        if msg.build or msg.target:
            if not (msg.build and msg.target):
                return
            served = await asyncio.to_thread(connector_build.served_digest, msg.target)
            if served is None or served == msg.build:
                return
        elif not await asyncio.to_thread(connector_build.has_any_build):
            # It says nothing about itself, so it predates saying anything and
            # is old by construction — but only tell it to fetch a build if we
            # have one to give it.
            return
        device.update_pushed = True
        logger.warning(
            "device %s runs connector build %s for %s, not the one we serve — "
            "pushing self-update",
            device.device_id,
            msg.build or "<unreported>",
            msg.target or "<unreported>",
        )
        await device.send(device_link.update())

    # Overridable seam for logging/metrics; a no-op by default.
    def _on_version_skew(self, device_id: str, proto: int | None) -> None:
        pass


# Shared singleton: the connection-owner process keeps the local implementation;
# rolling business backends use its RPC facade.
from app.core.config import settings  # noqa: E402

if settings.device_connection_url:
    from app.domain.agent.device_hub_rpc import RemoteDeviceHub  # noqa: E402

    device_hub = RemoteDeviceHub(
        settings.device_connection_url, settings.device_connection_auth_secret
    )
else:
    device_hub = DeviceHub()
