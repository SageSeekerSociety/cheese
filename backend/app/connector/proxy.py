"""The takeover ARBITRATION + terminal relay (contract §1 and §5).

cheesed is a transparent, always-read-write relay -- it has no read-only
mode. What a browser viewer experiences as "read-only" is really
COMMAND-EMISSION ARBITRATION owned by the backend orchestrator, implemented
entirely HERE:

  * cheesed's OUTPUT (binary webtty frames) is fanned out to every viewer
    attached to the session -- everyone always sees the live terminal.
  * A viewer's INPUT (binary webtty frames) is forwarded to cheesed ONLY when
    that viewer is the session's current ``controller``. A non-controller's
    input is silently dropped -- that drop IS the arbitration; cheesed itself
    never knows about it and never sees a "read-only" concept.
  * ``{"t":"takeover","on":...}`` text control from a viewer grants or
    releases command authority; on change we tell cheesed to pause/resume its
    auto-driver (``{"t":"takeover","on":...}``, contract §5) and broadcast the
    new state to every viewer so their UI reflects who is in control.

Scrolling needs none of this: it never depends on ``controller`` state.
Local scrollback lives in each viewer's own xterm.js buffer, and a
controller's mouse-wheel frames are just ordinary INPUT frames forwarded like
any other controller keystroke.

``SessionHub`` only depends on the minimal ``Transport`` Protocol, so it is
testable with a fake in-process transport -- no real WebSocket or cheesed
process required.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.connector.sessions import Session, SessionStore


class Transport(Protocol):
    """Minimal duplex message sink. Satisfied by ``fastapi.WebSocket`` (see
    ``router.py``'s ``_WebSocketTransport`` adapter) and trivially fakeable in
    tests."""

    async def send_text(self, data: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...


@dataclass
class _SessionChannels:
    agent: Transport | None = None
    viewers: dict[str, Transport] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionHub:
    """In-memory registry of live transports per ``session_id``, plus the
    takeover-arbitration logic. One instance is shared process-wide (see
    ``wiring.py``); it is the only stateful piece of the relay -- cheesed and
    the browser stay dumb endpoints."""

    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions
        self._channels: dict[str, _SessionChannels] = {}

    def _channels_for(self, session_id: str) -> _SessionChannels:
        return self._channels.setdefault(session_id, _SessionChannels())

    # -- registration ------------------------------------------------------

    async def attach_agent(self, session_id: str, transport: Transport) -> None:
        channels = self._channels_for(session_id)
        async with channels.lock:
            channels.agent = transport

    async def detach_agent(self, session_id: str, transport: Transport) -> None:
        channels = self._channels_for(session_id)
        async with channels.lock:
            if channels.agent is transport:
                channels.agent = None

    async def attach_viewer(self, session_id: str, viewer_id: str, transport: Transport) -> None:
        channels = self._channels_for(session_id)
        async with channels.lock:
            channels.viewers[viewer_id] = transport
        await self._notify_agent_viewer_presence(session_id)

    async def detach_viewer(self, session_id: str, viewer_id: str) -> None:
        channels = self._channels_for(session_id)
        async with channels.lock:
            channels.viewers.pop(viewer_id, None)
        session = await self._sessions.get(session_id)
        if session is not None and session.controller == viewer_id:
            await self.release_takeover(session_id, viewer_id)
        await self._notify_agent_viewer_presence(session_id)

    # -- data plane ----------------------------------------------------------

    async def on_agent_binary(self, session_id: str, data: bytes) -> None:
        """cheesed OUTPUT frame: fan out to every attached viewer."""
        channels = self._channels_for(session_id)
        for viewer in list(channels.viewers.values()):
            await viewer.send_bytes(data)

    async def on_viewer_binary(self, session_id: str, viewer_id: str, data: bytes) -> None:
        """Viewer webtty frame from the browser. A ResizeTerminal frame (webtty
        opcode '3') is viewport GEOMETRY, not state-changing input, so it is
        forwarded from ANY viewer -- otherwise a read-only viewer (the default,
        non-controller state) could never size the terminal to its window and
        would see a garbled, wrong-width screen. Everything else (keystrokes,
        mouse, ping) is forwarded ONLY from the current controller; that
        drop-if-not-controller check IS the read-only arbitration (contract §1)."""
        is_resize = len(data) >= 1 and data[0:1] == b"3"
        if not is_resize:
            session = await self._sessions.get(session_id)
            if session is None or session.controller != viewer_id:
                return
        channels = self._channels_for(session_id)
        if channels.agent is not None:
            await channels.agent.send_bytes(data)

    async def on_agent_text(self, session_id: str, text: str) -> None:
        """cheesed's control-plane text (hello/heartbeat/status). Currently we
        relay ``status`` updates to viewers verbatim; other message types are
        internal bookkeeping cheesed sends and are ignored here."""
        payload = _try_parse_json(text)
        if payload is not None and payload.get("t") == "status":
            await self._broadcast_text(session_id, text)

    # -- control plane: takeover -------------------------------------------

    async def request_takeover(self, session_id: str, viewer_id: str, on: bool) -> bool:
        """Grant or release command authority for ``viewer_id``. Returns the
        resulting ``granted`` state for THIS viewer (always ``False`` on
        release)."""
        if on:
            granted = await self.grant_takeover(session_id, viewer_id)
        else:
            await self.release_takeover(session_id, viewer_id)
            granted = False
        # Always ack the requesting viewer, even when grant/release was a no-op
        # (e.g. a stale controller id after the viewer's WS reconnected, which
        # otherwise leaves the button spinner hanging forever with no reply).
        await self._send_takeover_to_viewer(session_id, viewer_id)
        return granted

    async def grant_takeover(self, session_id: str, viewer_id: str) -> bool:
        session = await self._sessions.set_controller(session_id, viewer_id)
        await self._send_agent_takeover(session_id, on=True)
        await self._broadcast_takeover(session_id, session)
        return True

    async def release_takeover(self, session_id: str, viewer_id: str) -> None:
        session = await self._sessions.get(session_id)
        if session is None or session.controller != viewer_id:
            return
        session = await self._sessions.set_controller(session_id, None)
        await self._send_agent_takeover(session_id, on=False)
        await self._broadcast_takeover(session_id, session)

    async def _send_takeover_to_viewer(self, session_id: str, viewer_id: str) -> None:
        """Send the current takeover state to one specific viewer (the one that
        just requested a change), so its UI always gets a reply."""
        channels = self._channels_for(session_id)
        viewer = channels.viewers.get(viewer_id)
        if viewer is None:
            return
        session = await self._sessions.get(session_id)
        controller = session.controller if session is not None else None
        await viewer.send_text(
            json.dumps(
                {"t": "takeover", "on": controller is not None, "granted": controller == viewer_id}
            )
        )

    async def _send_agent_takeover(self, session_id: str, *, on: bool) -> None:
        channels = self._channels_for(session_id)
        if channels.agent is not None:
            await channels.agent.send_text(json.dumps({"t": "takeover", "on": on}))

    async def _notify_agent_viewer_presence(self, session_id: str) -> None:
        channels = self._channels_for(session_id)
        if channels.agent is not None:
            present = bool(channels.viewers)
            await channels.agent.send_text(json.dumps({"t": "viewer", "present": present}))

    async def _broadcast_takeover(self, session_id: str, session: Session) -> None:
        channels = self._channels_for(session_id)
        for viewer_id, viewer in list(channels.viewers.items()):
            granted = session.controller == viewer_id
            await viewer.send_text(
                json.dumps(
                    {"t": "takeover", "on": session.controller is not None, "granted": granted}
                )
            )

    async def _broadcast_text(self, session_id: str, text: str) -> None:
        channels = self._channels_for(session_id)
        for viewer in list(channels.viewers.values()):
            await viewer.send_text(text)


def _try_parse_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
