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
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.agent import device_link

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = device_link.PROTOCOL_VERSION


class DeviceTransport(Protocol):
    """A live device control channel. Satisfied by a ``fastapi.WebSocket`` adapter
    and trivially fakeable."""

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
    viewers: set[ViewerTransport] = field(default_factory=set)


@dataclass
class HubDevice:
    device_id: str
    transport: DeviceTransport | None = None
    proto: int | None = None
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
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, msg: dict[str, Any]) -> None:
        # Serialize sends to one device: a WebSocket is not safe for concurrent
        # writes, and the orchestrator, exec, and every viewer's fan-out all send
        # here. Without this, interleaved frames corrupt the channel.
        async with self.send_lock:
            if self.transport is not None:
                await self.transport.send_json(msg)


class DeviceHub:
    def __init__(self) -> None:
        self._devices: dict[str, HubDevice] = {}
        self._screens: dict[str, HubScreen] = {}  # sid -> screen (across devices)
        self._by_screen_token: dict[str, HubScreen] = {}

    def _device(self, device_id: str) -> HubDevice:
        return self._devices.setdefault(device_id, HubDevice(device_id=device_id))

    # -- device connection -------------------------------------------------

    async def attach_device(self, device_id: str, transport: DeviceTransport) -> None:
        device = self._device(device_id)
        device.transport = transport
        await device.send(device_link.welcome())

    async def detach_device(self, device_id: str, transport: DeviceTransport) -> None:
        device = self._devices.get(device_id)
        if device is not None and device.transport is transport:
            device.transport = None
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
        hook_key: str = "",
    ) -> HubScreen:
        """Re-register a screen the *device* is still running after the server lost its
        in-memory state (a restart). The frozen cli auto-reconnects its control channel
        and re-announces the screens it kept alive; adopting rebinds the sid + its
        screen token to the agent identity so viewers/attribution work again without
        restarting the screen. Idempotent per (device, sid).

        NOTE (P3 Phase B, item 5 skeleton): the caller that reconstructs the identity
        from the DB (agent_user_id/handle/project/topic per persisted screen row) and
        replays the device's re-announce into this is not yet wired — see
        ``connector.agent_socket``'s inbound loop. The mechanism is here and tested;
        the persistence + replay is the remaining TODO.
        """
        device = self._device(device_id)
        existing = device.screens.get(sid)
        if existing is not None:
            return existing
        screen = HubScreen(
            sid=sid,
            device_id=device_id,
            command=command or [],
            token=token,
            agent_user_id=agent_user_id,
            agent_handle=agent_handle,
            project_id=project_id,
            topic_id=topic_id,
            hook_key=hook_key,
        )
        device.screens[sid] = screen
        self._screens[sid] = screen
        self._by_screen_token[token] = screen
        return screen

    async def close_screen(self, device_id: str, sid: str) -> bool:
        """Close a screen: tell the device to end the session and forget it here."""
        device = self._device(device_id)
        screen = device.screens.pop(sid, None)
        if screen is None:
            return False
        self._screens.pop(sid, None)
        self._by_screen_token.pop(screen.token, None)
        from app.domain.agent.harness.claude_code import (
            drop_screen_subscriptions,
        )

        await drop_screen_subscriptions(screen)
        await device.send(device_link.session_close(sid))
        return True

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
        """Every screen bound to a topic, ACROSS devices and whether or not the
        device is online — the reverse lookup the lifecycle reaper needs to free a
        topic's screen when it is archived/accepted (``topic_id`` is globally unique,
        so this is the whole set). Offline devices are included on purpose: closing
        their screen still forgets it here, so an archived topic leaves no stale
        registry entry to re-surface if the device reconnects."""
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
        """One-shot command on the device → ``{stdout, stderr, exit, truncated}``."""
        device = self._device(device_id)
        device.exec_seq += 1
        eid = f"e{device.exec_seq}"
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        device.exec_pending[eid] = fut
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
        try:
            return await asyncio.wait_for(fut, timeout=timeout + 5)
        except TimeoutError:
            await device.send(device_link.exec_cancel(eid))
            raise
        finally:
            device.exec_pending.pop(eid, None)

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
            if device.proto not in (None, PROTOCOL_VERSION):
                self._on_version_skew(device_id, device.proto)
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
        if msg.t == "screen.data" and screen is not None:
            await self._fan_out_screen_data(screen, msg.decoded_data())
            return
        if msg.t == "rpc.result":
            fut = device.call_pending.get(msg.id)
            if fut is not None and not fut.done():
                if msg.error:
                    fut.set_exception(RuntimeError(msg.error))
                else:
                    fut.set_result(msg.value)
            return
        if msg.t == "file.result":
            fut = device.file_pending.get(msg.id)
            if fut is not None and not fut.done():
                if msg.error:
                    fut.set_exception(RuntimeError(msg.error))
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

    # Overridable seam for logging/metrics; a no-op by default.
    def _on_version_skew(self, device_id: str, proto: int | None) -> None:
        pass


# Shared singleton: the connector route and the DeviceChannel import this instance.
device_hub = DeviceHub()
