"""``DeviceHub`` — the server end of the frozen ``link.Msg`` protocol (P3).

One long-lived control channel per connected device, over which the server opens
*screens* (each a hosted ``claude`` + minimal cheeselet), relays a screen's raw
terminal bytes to browser viewers (the 现场 — relayed, never parsed), answers the
cheeselet's calls, and runs one-shot ``exec`` on the device. The wire shape is the
frozen ``cli/`` ``link.Msg`` contract (``device_link``); the frozen cli is the other
end and never changes.

Ported from the reference ``app/agent/hub.py`` and kept I/O-free: it depends only on
two tiny transport Protocols, so it is exercised with in-process fakes — no
WebSocket, no device, no DB. Perception of what the agent *does* flows through our
Claude Code hooks (``hook_events``), NOT through reading the screen; the cheeselet is
therefore minimal (start ``claude`` + type prompts), and the raw screen channel here
serves only the human viewer.

Our adaptations vs the reference:
  * a screen carries our identity shape — ``agent_user_id: uuid`` + ``agent_handle``
    (the authorship key) — plus its ``project_id``/``topic_id`` and a ``hook_key``
    (the token the device's ``cheese-hook`` posts under, so hooks route to the turn);
  * ``call_screen`` returns the call id so the caller (DeviceProvider) can correlate
    an ``rpc.result`` (used to await a ``prompt`` acknowledgement).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.agent import device_link

PROTOCOL_VERSION = device_link.PROTOCOL_VERSION


class DeviceTransport(Protocol):
    """A live device control channel. Satisfied by a ``fastapi.WebSocket`` adapter
    and trivially fakeable."""

    async def send_json(self, msg: dict[str, Any]) -> None: ...


class ViewerTransport(Protocol):
    """A browser terminal viewer. It only ever *receives* raw screen bytes."""

    async def send_bytes(self, data: bytes) -> None: ...


# A cheeselet→server function: (hub, screen, args) -> value. Errors are raised.
ScreenFn = Callable[["DeviceHub", "HubScreen", list[Any]], Awaitable[Any]]


@dataclass
class HubScreen:
    sid: str
    device_id: str
    command: list[str]
    token: str  # the CHEESE_SCREEN value injected into the screen
    # A screen *is* an agent (一个 agent 是一个屏幕): it acts as one agent-user in its
    # project/topic. Attribution of the screen's cheese-api calls keys on
    # agent_user_id; hooks route by hook_key (see DeviceProvider).
    agent_user_id: uuid.UUID
    agent_handle: str
    project_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    hook_key: str = ""
    vars: dict[str, Any] = field(default_factory=dict)
    viewers: set[ViewerTransport] = field(default_factory=set)


@dataclass
class HubDevice:
    device_id: str
    transport: DeviceTransport | None = None
    proto: int | None = None
    screens: dict[str, HubScreen] = field(default_factory=dict)
    exec_seq: int = 0
    call_seq: int = 0
    exec_pending: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict
    )
    call_pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, msg: dict[str, Any]) -> None:
        # Serialize sends to one device: a WebSocket is not safe for concurrent
        # writes, and the orchestrator, exec, and every viewer's fan-out all send
        # here. Without this, interleaved frames corrupt the channel.
        async with self.send_lock:
            if self.transport is not None:
                await self.transport.send_json(msg)


async def _screen_ready(hub: "DeviceHub", screen: "HubScreen", args: list[Any]) -> Any:
    return {"ok": True}


class DeviceHub:
    def __init__(self, screen_fns: dict[str, ScreenFn] | None = None) -> None:
        self._devices: dict[str, HubDevice] = {}
        self._screens: dict[str, HubScreen] = {}  # sid -> screen (across devices)
        self._by_screen_token: dict[str, HubScreen] = {}
        self._fns: dict[str, ScreenFn] = {"screenReady": _screen_ready}
        if screen_fns:
            self._fns.update(screen_fns)

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
        cheeselet_source: str,
        *,
        agent_user_id: uuid.UUID,
        agent_handle: str,
        project_id: uuid.UUID | None = None,
        topic_id: uuid.UUID | None = None,
        hook_key: str = "",
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> HubScreen:
        """Open a screen (an agent) on the device acting as ``agent_user_id``: mint
        its per-screen token, ship the cheeselet + command, and inject ``env`` into
        the screen process. The token becomes ``CHEESE_SCREEN`` inside the screen so a
        call from within it proves which screen it belongs to."""
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
                source=cheeselet_source,
                env=env,
            )
        )
        return screen

    async def close_screen(self, device_id: str, sid: str) -> bool:
        """Close a screen: tell the device to end the session and forget it here."""
        device = self._device(device_id)
        screen = device.screens.pop(sid, None)
        if screen is None:
            return False
        self._screens.pop(sid, None)
        self._by_screen_token.pop(screen.token, None)
        await device.send(device_link.session_close(sid))
        return True

    async def reload_driver(self, device_id: str, sid: str, source: str) -> None:
        """Hot-reload the cheeselet into a running screen (``script.load``)."""
        await self._device(device_id).send(device_link.script_load(sid, source))

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

    async def call_screen(
        self, device_id: str, sid: str, name: str, args: list[Any]
    ) -> str:
        """Server→cheeselet function call (e.g. ``prompt``). Returns the call id so
        the caller may await the matching ``rpc.result`` via ``await_call``."""
        device = self._device(device_id)
        device.call_seq += 1
        call_id = f"srv{device.call_seq}"
        await device.send(device_link.rpc_call(sid, call_id, name, args))
        return call_id

    async def await_call(
        self, device_id: str, call_id: str, *, timeout: float = 30
    ) -> Any:
        """Await the cheeselet's ``rpc.result`` for a prior ``call_screen``. Raises
        on device error or timeout."""
        device = self._device(device_id)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        device.call_pending[call_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            device.call_pending.pop(call_id, None)

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
        msg = device_link.LinkMsg.parse(m)
        screen = device.screens.get(msg.sid)

        if msg.t == "hello":
            device.proto = msg.v
            if device.proto not in (None, PROTOCOL_VERSION):
                self._on_version_skew(device_id, device.proto)
            return
        if msg.t in ("heartbeat", "session.ready", "session.error"):
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
        if msg.t == "var.push" and screen is not None:
            screen.vars[msg.name] = msg.value
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
        if msg.t == "rpc.call":
            await self._handle_screen_call(device, screen, m)
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

    async def _handle_screen_call(
        self, device: HubDevice, screen: HubScreen | None, m: dict[str, Any]
    ) -> None:
        name = str(m.get("name", ""))
        args = m.get("args", [])
        value: Any = None
        error = ""
        fn = self._fns.get(name)
        if fn is None or screen is None:
            error = f"unknown function {name!r}"
        else:
            try:
                value = await fn(self, screen, args if isinstance(args, list) else [])
            except Exception as exc:  # noqa: BLE001 — a cheeselet call must not crash the channel
                error = str(exc) or exc.__class__.__name__
        await device.send(
            device_link.rpc_result(
                str(m.get("sid", "")), str(m.get("id", "")), value, error
            )
        )

    def _require_screen(self, device_id: str, sid: str) -> HubScreen:
        screen = self._device(device_id).screens.get(sid)
        if screen is None:
            raise KeyError(f"no screen {sid!r} on device {device_id!r}")
        return screen

    # Overridable seam for logging/metrics; a no-op by default.
    def _on_version_skew(self, device_id: str, proto: int | None) -> None:
        pass


# Shared singleton: the connector route and the DeviceProvider import this instance.
device_hub = DeviceHub()
