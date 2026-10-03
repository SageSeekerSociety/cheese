"""``DeviceHub`` — the server end of the frozen ``link.Msg`` protocol (P3).

One long-lived control channel per connected device, over which the server opens
*screens* (each running a harness's runner), relays a screen's raw terminal
bytes to browser viewers (relayed, never parsed), reaches a screen's runner
(``call_executor``), and runs one-shot ``exec`` on the device. The wire shape is
the ``cli/`` ``link.Msg`` contract (``device_link``).

Ported from the reference ``app/agent/hub.py`` and kept I/O-free: it depends only on
two tiny transport Protocols, so it is exercised with in-process fakes — no
WebSocket, no device, no DB. What the agent *does* is read from its runner's
journal, NOT from the screen, so the raw screen channel here serves only the
human viewer.

Our adaptations vs the reference:
  * a screen carries our identity shape — ``agent_user_id: uuid`` + ``agent_handle``
    (the authorship key) — plus its ``project_id``/``topic_id``.
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


class DeviceUnreachable(DeviceOffline):
    """链路根本不在，所以这一帧一个字节都没有写出去。

    和 ``DeviceOffline`` 本身的差别只有一个，而那一个决定平台事后敢不敢重做这件事：
    这一档是**发出之前**就失败的，那台机器没见过这次调用。一次已经写进 socket 的调用
    在等结果的时候链路断了，也是 ``DeviceOffline``（``drop_transport`` 把在飞的
    future 全置成它）—— 那一档里那件事做没做过，这一侧不知道。

    所以只有这一档能被记成 ``failed``「确定没发生」（``agent/dispatch_log.py``）。
    一个子类，因为除此之外它和链路不在是同一件事：所有 ``except DeviceOffline`` 照旧。
    """


class LinkInterrupted(DeviceOffline):
    """链路断在一次调用的半路：那一帧已经出去了，答复回不来了。

    那台机器可能已经把这件事做完了，也可能没有，这一侧不知道。所以它不能像
    ``DeviceUnreachable`` 那样被当成「确定没发生」，也不该说成「机器不在」：
    链路断了的机器多半几秒后就回来（``RECONNECT_GRACE_S``），下一次调用照样
    能用。告诉调用方的只有一件事 —— 这一次的结果不知道。
    """


def offline_headers(exc: DeviceOffline) -> dict[str, str]:
    """How a ``DeviceOffline`` crosses HTTP, as the 409 that carries it.

    ``X-Device-Id`` says the machine is not there; ``X-Device-Link:
    interrupted`` that the link went down under this very call
    (``LinkInterrupted``), so its outcome is unknown. The connection owner, the
    backend and the executor client each read the pair the same way."""
    headers = {"X-Device-Id": exc.device_id}
    if isinstance(exc, LinkInterrupted):
        headers["X-Device-Link"] = "interrupted"
    return headers


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

    def __init__(self, message: str, *, failure_code: str | None = None) -> None:
        super().__init__(message)
        self.failure_code = failure_code


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


PROTOCOL_VERSION = device_link.PROTOCOL_VERSION

# How often a connector says it is there (`pingPeriod` in
# cli/internal/link/link.go), and how long its link may go unheard before it is
# taken as lost: three missed beats (`DeviceHub.silence_allowed`).
HEARTBEAT_S = 15.0
# How much longer than a command's own deadline `exec` waits for the machine's
# reply before it gives up and raises `TimeoutError`.
EXEC_REPLY_SLACK_S = 5
LINK_SILENCE_S = 3 * HEARTBEAT_S
# How long after its link drops a machine counts as reconnecting rather than
# gone. A connector redials at once and then after 1s, 2s, 4s, 5s
# (`cli/internal/link/link.go`), so a link that blinks is back within a few
# seconds; three of those attempts fit here. A call that finds no link inside
# this window waits for the machine to say hello again, instead of reporting a
# machine that is on its way back as offline. Past it, the machine is away.
RECONNECT_GRACE_S = 15.0


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
    # agent_user_id.
    agent_user_id: int
    agent_handle: str
    project_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    resource_id: uuid.UUID | None = None
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
    # Last time the device sent any frame (hello/heartbeat/…).
    last_seen: float = 0.0
    # When this machine's last link dropped (loop time); None while it is linked
    # and for a machine this process has never seen linked. See
    # `RECONNECT_GRACE_S`.
    dropped_at: float | None = None
    # Set by `hello` on the current link, cleared when that link drops: what a
    # call waiting out a reconnect waits on. Hello rather than attach, because
    # what a call needs (`executor` above) is only known from hello.
    announced: asyncio.Event = field(default_factory=asyncio.Event)
    # Whether the connector on THIS connection sends heartbeats. One that does
    # and then falls silent for ``LINK_SILENCE_S`` has lost its link, whatever
    # the socket says (`silence_allowed`). Reset on every attach.
    heartbeats: bool = False
    # The frame being written to the link right now, if any (`send`): dropping
    # the link ends its wait.
    writing: asyncio.Timeout | None = None
    screens: dict[str, HubScreen] = field(default_factory=dict)
    exec_seq: int = 0
    # 本机目录授权: one counter for both directions of the localfs channel (a grant
    # push and a read/write/list op), since they share one result future map.
    local_fs_seq: int = 0
    exec_pending: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict
    )
    executor_pending: dict[str, tuple[asyncio.Future[Any], bytearray]] = field(
        default_factory=dict
    )
    session_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    local_fs_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
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
            # A write to a peer that stopped reading does not fail: the server
            # waits for its buffer to drain, which on a machine that went to
            # sleep behind the proxy is until the socket finally closes —
            # minutes. It holds this lock all the while, so every later frame to
            # the machine, a reconnect's welcome included, queues behind it. The
            # link being dropped is what ends that wait.
            stall = asyncio.timeout(None)
            try:
                async with stall:
                    self.writing = stall
                    try:
                        await transport.send_json(msg)
                    finally:
                        self.writing = None
            except TimeoutError:
                if stall.expired():
                    raise LinkInterrupted(self.device_id) from None
                raise
            except ConnectionError as exc:
                # The receive loop only learns of a dead link when the peer says
                # so; a peer that vanished never does, and then the socket stays
                # attached for good — every caller that trusts ``is_online`` sends
                # into it and fails, once a minute for a storage sweep. A failed
                # send is the proof the receive loop never gets.
                self.drop_transport(transport)
                raise LinkInterrupted(self.device_id) from exc

    def drop_transport(self, transport: DeviceTransport) -> bool:
        """Forget ``transport`` if it is still the live one; fail what waited on it.

        Everything that waits on an answer from the machine fails here, at once:
        an answer can only come back over this link, so a call left waiting
        would wait out its own timeout — eleven minutes for an executor install
        — for nothing."""
        if self.transport is not transport:
            return False
        self.transport = None
        self.dropped_at = asyncio.get_running_loop().time()
        self.announced.clear()
        if self.writing is not None:
            # Expire it now: the write is abandoned and ``send`` says offline.
            self.writing.reschedule(0)
        waiting = [
            *(future for future, _ in self.executor_pending.values()),
            *self.session_pending.values(),
            *self.exec_pending.values(),
            *self.local_fs_pending.values(),
        ]
        for future in waiting:
            if not future.done():
                future.set_exception(LinkInterrupted(self.device_id))
        return True


class DeviceHub:
    def __init__(self) -> None:
        self._devices: dict[str, HubDevice] = {}
        self._screens: dict[str, HubScreen] = {}  # sid -> screen (across devices)
        self._by_screen_token: dict[str, HubScreen] = {}

    def _device(self, device_id: str) -> HubDevice:
        return self._devices.setdefault(device_id, HubDevice(device_id=device_id))

    async def _linked(self, device: HubDevice) -> bool:
        """Whether ``device`` has a link to send on, waiting out a reconnect.

        A machine whose link dropped less than ``RECONNECT_GRACE_S`` ago is
        waited for until it says hello again; one that has been away longer, or
        that this process has never seen linked (it started after the machine
        went, or the machine never came), is answered at once. Each caller then
        raises exactly what it raised before for a machine with no link.

        A machine that has dialled back in but not yet said hello is still on
        its way: what it can run is only known from that hello."""
        if device.dropped_at is None:
            return device.transport is not None
        if device.transport is not None and device.announced.is_set():
            return True
        loop = asyncio.get_running_loop()
        remaining = RECONNECT_GRACE_S - (loop.time() - device.dropped_at)
        if remaining > 0:
            try:
                async with asyncio.timeout(remaining):
                    await device.announced.wait()
            except TimeoutError:
                pass
        return device.transport is not None

    # -- device connection -------------------------------------------------

    async def attach_device(
        self, device_id: str, transport: DeviceTransport, *, name: str = ""
    ) -> None:
        device = self._device(device_id)
        if device.transport is not None and device.transport is not transport:
            device.drop_transport(device.transport)
        device.transport = transport
        device.heartbeats = False
        device.connection_generation += 1
        device.executor = False
        if name:
            device.name = name
        device.update_pushed = False
        await device.send(device_link.welcome())

    async def detach_device(self, device_id: str, transport: DeviceTransport) -> None:
        device = self._devices.get(device_id)
        if device is not None:
            device.drop_transport(transport)

    def is_online(self, device_id: str) -> bool:
        device = self._devices.get(device_id)
        return device is not None and device.transport is not None

    def reconnecting(self, device_id: str) -> bool:
        """Whether the machine has no link but lost it less than
        ``RECONNECT_GRACE_S`` ago: a call to it now waits for it to come back
        (``_linked``) rather than failing, so whoever decides whether to make
        that call should not read it as away."""
        device = self._devices.get(device_id)
        if device is None or device.transport is not None:
            return False
        if device.dropped_at is None:
            return False
        elapsed = asyncio.get_event_loop().time() - device.dropped_at
        return elapsed < RECONNECT_GRACE_S

    def silence_allowed(self, device_id: str) -> float | None:
        """How long the link may go without a frame from the machine before it
        is taken as lost; None when silence says nothing about it.

        The socket cannot answer this. A machine that sleeps, or loses its
        network, behind the proxy leaves the proxy holding the connection open;
        the server's keepalive notices, but closing a socket whose buffer will
        not drain waits until the proxy gives up, which is when the machine
        wakes. Until then the machine read as online, every call to it waited
        for an answer that could not come, and the room's tools hung for as
        long as the machine slept. A connector that heartbeats is heard from
        every ``HEARTBEAT_S``; one that is not heard from for three beats is
        gone. A connector that has sent none on this connection is one built
        before heartbeats, and it is told to update when it says hello.
        """
        device = self._devices.get(device_id)
        if device is None or not device.heartbeats:
            return None
        return LINK_SILENCE_S

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
        An adopt-create is
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
        return True

    async def session_request(
        self, device_id: str, message: dict[str, Any], *, timeout: float = 30
    ) -> Any:
        device = self._device(device_id)
        if not await self._linked(device):
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

    # -- 本机目录授权 (server -> device, awaited) ---------------------------

    async def push_local_fs_grants(
        self,
        device_id: str,
        grants: list[dict[str, Any]],
        *,
        timeout: float = 20,
    ) -> dict[str, Any]:
        """Hand this machine its grant set and await the fingerprint it now holds.

        Raises ``DeviceOffline`` at once when the machine has no link — rather
        than sending into the void and timing out. That is not a failure of the
        grant: the platform is the authoritative record either way, and the set is
        pushed again when the device reattaches. A caller that has just recorded a
        grant must therefore catch this and carry on, which is exactly the
        degradation 「本机离线时项目照常可用」 describes.
        """
        device = self._device(device_id)
        if device.transport is None:
            raise DeviceOffline(device_id)
        device.local_fs_seq += 1
        grants_id = f"g{device.local_fs_seq}"
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        device.local_fs_pending[grants_id] = future
        try:
            await device.send(
                device_link.local_fs_grants(
                    grants_id=grants_id, device_id=device_id, grants=grants
                )
            )
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            device.local_fs_pending.pop(grants_id, None)
            _read_a_failure_nobody_awaited(future)

    async def local_fs_op(
        self,
        device_id: str,
        op: dict[str, Any],
        *,
        timeout: float = 60,
    ) -> dict[str, Any]:
        """Read, write or list inside a granted directory on this machine.

        Returns the device's ``localfs.Reply``. A refusal comes back as a normal
        result with ``decision == "denied"`` — it is an answer, not a transport
        failure, and the reason in it is what the person is shown.
        """
        device = self._device(device_id)
        if not await self._linked(device):
            raise DeviceOffline(device_id)
        device.local_fs_seq += 1
        op_id = f"o{device.local_fs_seq}"
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        device.local_fs_pending[op_id] = future
        try:
            await device.send(device_link.local_fs_op(op_id=op_id, op=op))
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            device.local_fs_pending.pop(op_id, None)
            _read_a_failure_nobody_awaited(future)

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

        Raises ``DeviceOffline`` when the device has no link and is not on its
        way back (``_linked``), rather than sending into the void and timing out
        ``timeout``+5s later."""
        device = self._device(device_id)
        if not await self._linked(device):
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
            return await asyncio.wait_for(fut, timeout=timeout + EXEC_REPLY_SLACK_S)
        except TimeoutError:
            await device.send(device_link.exec_cancel(eid))
            raise
        finally:
            device.exec_pending.pop(eid, None)
            _read_a_failure_nobody_awaited(fut)

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
        if not await self._linked(device):
            # 发出之前。往下每一步都可能是「已经出去了」，所以这条分界只在这里。
            raise DeviceUnreachable(device_id)
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
                device_link.execution_call(
                    call_id=identifier,
                    state=state,
                    method=method,
                    params=params,
                    timeout=int(timeout),
                )
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
            device.dropped_at = None
            device.announced.set()
            if device.proto not in (None, PROTOCOL_VERSION):
                self._on_version_skew(device_id, device.proto)
            await self._update_if_stale(device, msg)
            return
        if msg.t == "session.error":
            # The device could not start (or attach) this screen. There is no
            # pending future to fail — the turn discovers the loss when its runner
            # never answers — but dropping the one frame that says WHY turns that
            # into a blank error, so at least keep the reason in the log.
            logger.warning(
                "device %s screen %s reported session.error: %s",
                device_id,
                msg.sid,
                msg.error,
            )
            return
        if msg.t == "heartbeat":
            device.heartbeats = True
            return
        if msg.t == "session.ready":
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
        if msg.t == "session.result":
            fut = device.session_pending.get(msg.id)
            if fut is not None and not fut.done():
                if msg.error:
                    fut.set_exception(DeviceCallError(msg.error))
                else:
                    fut.set_result(msg.value)
            return
        if msg.t in ("localfs.grants.result", "localfs.op.result"):
            # One map for both: the id is unique per device across the channel, and
            # an o/g prefix already says which kind answered.
            fut = device.local_fs_pending.get(msg.id)
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
        unrelated — a call that never returned — and no error anywhere names a
        version. Left to a person to notice, a machine stays behind for as long
        as nobody looks: one ran a build two weeks older than a frame it was
        being sent, and every one of those frames went into a silence.

        Only ever on a definite answer. A connector that identifies itself but
        whose bytes we cannot compare is left alone — telling it to update on a
        half-answer would re-exec the machine on every reconnect and never
        converge, which is worse than the drift.

        The build to be on is the one the origin publishes, the file the
        machine's self-update downloads (`connector_build.published_digest`),
        not this process's own copy, which lags it between owner releases.
        """
        if device.update_pushed:
            return
        if msg.build or msg.target:
            if not (msg.build and msg.target):
                return
            published = await connector_build.published_digest(msg.target)
            if published is None or published == msg.build:
                return
        elif not await asyncio.to_thread(connector_build.has_any_build):
            # It says nothing about itself, so it predates saying anything and
            # is old by construction — but only tell it to fetch a build if we
            # have one to give it.
            return
        device.update_pushed = True
        logger.warning(
            "device %s runs connector build %s for %s, not the published one — "
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
