"""``DeviceHub`` — the server end of the frozen ``link.Msg`` protocol (Act 2 step 2).

A transport-free re-implementation of what ``misc/web-claude/server`` proves: one
long-lived control channel per connected device, over which the server opens
*screens* (each a hosted CLI + cheeselet), relays a screen's raw terminal to
browser viewers, mirrors the cheeselet's variables, answers the cheeselet's calls,
and runs one-shot ``exec`` on the device. The wire shape is copied field-for-field
from the frozen ``cli/`` ``link.Msg`` contract (see ``docs/design/architecture.md`` §5);
the frozen ``cli/`` is the other end and never changes.

Everything here is I/O-free: it depends only on two tiny transport Protocols, so it
is exercised with in-process fakes — no WebSocket, no device, no DB. The connector
routes (Act 2 steps 3–5) adapt real WebSockets to these Protocols and resolve the
device→agent actor via ``domain/device``; the takeover/arbitration layer of §5
sits above this and is added in Phase D.
"""

import asyncio
import base64
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# The cheese wire-protocol version this server speaks (matches link.Version = 1).
PROTOCOL_VERSION = 1


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
    # A screen *is* an agent (一个 agent 是一个屏幕): it acts as one agent user and may
    # optionally belong to a project (project is a future wrapper; agents/chat are
    # project-independent). Attribution of the screen's cheese-api calls keys on
    # agent_user_id; viewer authz falls back to the device owner when project_id is None.
    project_id: int | None
    agent_user_id: int
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
    exec_pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, msg: dict[str, Any]) -> None:
        # Serialize sends to one device: a WebSocket is not safe for concurrent
        # writes, and the orchestrator, exec, and every viewer's fan-out all send
        # here. Without this, interleaved frames corrupt the channel.
        async with self.send_lock:
            if self.transport is not None:
                await self.transport.send_json(msg)


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
        await device.send({"t": "welcome", "v": PROTOCOL_VERSION})

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
        project_id: int | None,
        agent_user_id: int,
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> HubScreen:
        """Open a screen (an agent) on the device in ``project_id`` acting as
        ``agent_user_id``: mint its per-screen token and ship the cheeselet,
        mirroring web-claude's ``session.create``. The token becomes
        ``CHEESE_SCREEN`` inside the screen; ``env`` is injected into the screen's
        process environment — the orchestrator passes ``CHEESE_TOKEN`` here (the
        agent user's session token) so the agent's ``cheese api`` authenticates as
        itself, exactly like a human."""
        device = self._device(device_id)
        sid = "s" + uuid.uuid4().hex[:8]
        screen = HubScreen(
            sid=sid,
            device_id=device_id,
            command=command,
            token=uuid.uuid4().hex,
            project_id=project_id,
            agent_user_id=agent_user_id,
        )
        device.screens[sid] = screen
        self._screens[sid] = screen
        self._by_screen_token[screen.token] = screen
        message: dict[str, Any] = {
            "t": "session.create",
            "sid": sid,
            "command": command,
            "screen": screen.token,
            "cols": cols,
            "rows": rows,
            "source": cheeselet_source,
        }
        if env:
            message["env"] = env
        await device.send(message)
        return screen

    async def close_screen(self, device_id: str, sid: str) -> bool:
        """Close a screen: tell the device to end the session and forget it here.
        Returns whether the screen existed. Viewers are left to notice the socket
        drop; the device tears down the pty."""
        device = self._device(device_id)
        screen = device.screens.pop(sid, None)
        if screen is None:
            return False
        self._screens.pop(sid, None)
        self._by_screen_token.pop(screen.token, None)
        await device.send({"t": "session.close", "sid": sid})
        return True

    async def reload_driver(self, device_id: str, sid: str, source: str) -> None:
        """Hot-reload the cheeselet into a running screen — no restart, no lost
        CLI context (``script.load``)."""
        await self._device(device_id).send({"t": "script.load", "sid": sid, "source": source})

    async def call_screen(self, device_id: str, sid: str, name: str, args: list[Any]) -> None:
        """Server→cheeselet function call (say / choose / compact …). Fire-and-forget;
        the cheeselet's ``rpc.result`` is not awaited here."""
        device = self._device(device_id)
        device.call_seq += 1
        await device.send(
            {"t": "rpc.call", "sid": sid, "id": f"srv{device.call_seq}", "name": name, "args": args}
        )

    def screen_by_token(self, token: str) -> HubScreen | None:
        return self._by_screen_token.get(token)

    def screen(self, sid: str) -> HubScreen | None:
        """Look up a screen by id (for the viewer route's authorization + device
        routing). Read-only; does not create anything."""
        return self._screens.get(sid)

    def adopt_screen(
        self, *, sid: str, device_id: str, token: str, project_id: int | None, agent_user_id: int
    ) -> HubScreen:
        """Re-register a screen the cli is already running (after a server restart) —
        no ``session.create`` is sent; the tmux session already exists on the device."""
        device = self._device(device_id)
        screen = HubScreen(
            sid=sid,
            device_id=device_id,
            command=[],
            token=token,
            project_id=project_id,
            agent_user_id=agent_user_id,
        )
        device.screens[sid] = screen
        self._screens[sid] = screen
        self._by_screen_token[token] = screen
        return screen

    def all_online_screens(self) -> list[HubScreen]:
        return [s for s in self._screens.values() if self.is_online(s.device_id)]

    def screens_in_project(self, project_id: int) -> list[HubScreen]:
        """The live agent screens running in a project (for the workspace to show
        which agents are online and open their 现场). Only screens whose device is
        currently connected."""
        return [
            s
            for s in self._screens.values()
            if s.project_id == project_id and self.is_online(s.device_id)
        ]

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
        ``timeout`` bounds it device-side; if even that plus a margin elapses we tell
        the device to cancel and raise ``TimeoutError``."""
        device = self._device(device_id)
        device.exec_seq += 1
        eid = f"e{device.exec_seq}"
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        device.exec_pending[eid] = fut
        msg: dict[str, Any] = {"t": "exec", "id": eid, "command": argv, "timeout": int(timeout)}
        if cwd:
            msg["cwd"] = cwd
        if env:
            msg["env"] = env
        if stdin:
            msg["stdin"] = stdin
        await device.send(msg)
        try:
            return await asyncio.wait_for(fut, timeout=timeout + 5)
        except TimeoutError:
            await device.send({"t": "exec.cancel", "id": eid})
            raise
        finally:
            device.exec_pending.pop(eid, None)

    # -- viewers (browser <-> device screen) -------------------------------

    async def attach_viewer(
        self, device_id: str, sid: str, viewer: ViewerTransport, *, cols: int = 120, rows: int = 32
    ) -> None:
        """Attach a browser viewer to a screen. The first viewer triggers a
        ``screen.subscribe`` carrying its size (the device attaches a tmux client
        sized to it)."""
        screen = self._require_screen(device_id, sid)
        first = not screen.viewers
        screen.viewers.add(viewer)
        if first:
            await self._device(device_id).send(
                {"t": "screen.subscribe", "sid": sid, "cols": cols, "rows": rows}
            )

    async def detach_viewer(self, device_id: str, sid: str, viewer: ViewerTransport) -> None:
        screen = self._screens.get(sid)
        if screen is None:
            return
        screen.viewers.discard(viewer)
        # Route to the screen's own device, not the caller-supplied id, so a stale or
        # mismatched device_id can never send an unsubscribe to the wrong device.
        device = self._device(screen.device_id)
        # A leaving viewer is passive (screen returns to the live bottom).
        await device.send({"t": "var.set", "sid": sid, "name": "viewerLevel", "value": "passive"})
        if not screen.viewers:
            await device.send({"t": "screen.unsubscribe", "sid": sid})

    async def viewer_input(self, device_id: str, sid: str, data: bytes) -> None:
        await self._device(device_id).send(
            {"t": "screen.input", "sid": sid, "data": base64.b64encode(data).decode()}
        )

    async def viewer_resize(self, device_id: str, sid: str, cols: int, rows: int) -> None:
        await self._device(device_id).send(
            {"t": "screen.resize", "sid": sid, "cols": cols, "rows": rows}
        )

    async def viewer_control(self, device_id: str, sid: str, level: str) -> None:
        """The viewer's mode (passive / scroll / full) → the ``viewerLevel`` variable
        the cheeselet reads to decide when to sample the screen and when to buffer."""
        if level not in ("passive", "scroll", "full"):
            level = "passive"
        await self._device(device_id).send(
            {"t": "var.set", "sid": sid, "name": "viewerLevel", "value": level}
        )

    # -- device -> server (inbound) ----------------------------------------

    async def on_device_message(self, device_id: str, m: dict[str, Any]) -> None:
        """Dispatch one inbound ``link.Msg`` from the device — the server half of
        web-claude's ``handle_agent_message``."""
        device = self._device(device_id)
        t = m.get("t")
        sid = m.get("sid", "")
        screen = device.screens.get(sid)

        if t == "hello":
            device.proto = m.get("v")
            if device.proto not in (None, PROTOCOL_VERSION):
                # Never hard-fail on version skew — warn and keep going (the hook
                # for future protocol evolution).
                self._on_version_skew(device_id, device.proto)
            return
        if t in ("heartbeat", "session.ready"):
            return
        if t == "exec.result":
            fut = device.exec_pending.get(str(m.get("id")))
            if fut is not None and not fut.done():
                fut.set_result(
                    {
                        "stdout": m.get("stdout", ""),
                        "stderr": m.get("stderr", ""),
                        "exit": m.get("exit", 0),
                        "truncated": m.get("truncated", False),
                    }
                )
            return
        if t == "session.error":
            return
        if t == "var.push" and screen is not None:
            screen.vars[str(m.get("name"))] = m.get("value")
            return
        if t == "screen.data" and screen is not None:
            await self._fan_out_screen_data(screen, m.get("data", ""))
            return
        if t == "rpc.call":
            await self._handle_screen_call(device, screen, m)
            return

    async def _fan_out_screen_data(self, screen: HubScreen, data_b64: str) -> None:
        try:
            raw = base64.b64decode(data_b64)
        except (ValueError, TypeError):
            return
        dead: list[ViewerTransport] = []
        for viewer in list(screen.viewers):
            try:
                await viewer.send_bytes(raw)
            except Exception:
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
            except Exception as exc:  # a cheeselet call must never crash the channel
                error = str(exc) or exc.__class__.__name__
        await device.send(
            {"t": "rpc.result", "sid": m.get("sid", ""), "id": m.get("id"), "value": value, "error": error}
        )

    def _require_screen(self, device_id: str, sid: str) -> HubScreen:
        screen = self._device(device_id).screens.get(sid)
        if screen is None:
            raise KeyError(f"no screen {sid!r} on device {device_id!r}")
        return screen

    # Overridable seam for logging/metrics; a no-op by default.
    def _on_version_skew(self, device_id: str, proto: int | None) -> None:
        pass


async def _screen_ready(hub: "DeviceHub", screen: "HubScreen", args: list[Any]) -> Any:
    return {"ok": True}
