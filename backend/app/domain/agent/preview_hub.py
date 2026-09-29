"""The platform end of 运行环境预览's tunnel: which topic has a live preview, and
one multiplexed stream per browser request.

Symmetric to ``device_hub`` and kept the same way — I/O-free, depending only on a
one-method transport Protocol — so it is exercised with in-process fakes: no
WebSocket, no machine, no DB.

One tunnel per SEAT. A room may seat several teammates, each working in its own
checkout — possibly on its own machine — and each one that runs ``cheese serve``
starts its own helper. The helper authenticates with its turn's scoped cheese
token, whose claims name both the room (``t``) and the teammate (``a``), so a
tunnel is keyed by the two together. Keyed by the room alone, two teammates'
helpers took the slot from each other on every reconnect, once a second, and a
page load that fell into a gap saw ``preview unavailable``.

Which seat the room's preview shows is not decided here: it is whoever declared
the room's current app (the author of that artifact), and every browser-side
call names it.

Within one seat the NEWEST credential holds the tunnel: the same teammate
relaunched, or moved to another machine, and the older helper is the stale one.
It is hung up with ``CLOSE_SUPERSEDED`` and exits instead of redialling; one that
dials in holding an older credential than the live one is refused the same way.

The frame codec is imported from the machine-side helper rather than restated
here — ``preview_tunnel`` is stdlib-only by construction, so there is one
definition of the wire instead of two that drift.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from app.domain.agent import preview_tunnel as wire

logger = logging.getLogger(__name__)

# A stream is torn down if the machine says nothing for this long. Bounded on
# purpose: a browser left waiting on a request the machine will never answer
# looks exactly like a hung app, which is the most expensive failure to read.
STREAM_TIMEOUT_S = 30.0

# What a preview response may weigh before the platform refuses to hold it. The
# machine caps the same number on its side; this is the backstop for a helper
# that does not (an older one, a hand-run one).
MAX_BODY = 32 * 1024 * 1024

# A knock on the app is a liveness question, and a liveness question that takes
# thirty seconds has already answered itself. Short and separate from the request
# timeout: the panel asks this on every refresh, so a wedged app must not hold
# each of those open for the full window.
PROBE_TIMEOUT_S = 5.0


class PreviewTransport(Protocol):
    """A live preview tunnel. Satisfied by a ``fastapi.WebSocket`` adapter and
    trivially fakeable."""

    async def send_bytes(self, data: bytes) -> None: ...


@dataclass
class PreviewResponse:
    """What the app answered: enough to rebuild an HTTP response, nothing more."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes


class PreviewStream:
    """One browser request (or one browser WebSocket) and its whole life."""

    def __init__(self, machine: "PreviewMachine", stream_id: int) -> None:
        self._machine = machine
        self.id = stream_id
        self.inbox: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()

    async def send(self, op: int, payload: bytes = b"") -> None:
        await self._machine.send(op, self.id, payload)

    async def receive(
        self, timeout: float | None = STREAM_TIMEOUT_S
    ) -> tuple[int, bytes]:
        """The next frame from the machine. Bounded by default (see
        STREAM_TIMEOUT_S); ``None`` waits indefinitely, which only a proxied
        WebSocket wants — an HMR socket is silent until somebody edits a file."""
        return await asyncio.wait_for(self.inbox.get(), timeout=timeout)

    def close(self) -> None:
        self._machine.forget(self.id)


@dataclass
class PreviewMachine:
    topic_id: uuid.UUID
    seat: str
    transport: PreviewTransport
    # When the credential it dialled with was issued; see the module docstring.
    issued: int = 0
    streams: dict[int, PreviewStream] = field(default_factory=dict)
    next_stream: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, op: int, stream_id: int, payload: bytes = b"") -> None:
        # Serialised for the same reason the device channel serialises: a
        # WebSocket is not safe for concurrent writes, and every open stream
        # sends here.
        async with self.send_lock:
            await self.transport.send_bytes(wire.encode(op, stream_id, payload))

    def open(self) -> PreviewStream:
        self.next_stream += 1
        stream = PreviewStream(self, self.next_stream)
        self.streams[stream.id] = stream
        return stream

    def forget(self, stream_id: int) -> None:
        self.streams.pop(stream_id, None)

    def on_frame(self, data: bytes) -> None:
        """Route one frame this machine sent to the stream waiting for it.

        On the machine, not looked up by room: a helper being replaced is still
        delivering frames for a moment, and its stream ids mean nothing on the
        machine that replaced it.
        """
        try:
            op, stream_id, payload = wire.decode(data)
        except ValueError as exc:
            logger.warning(
                "preview machine for %s sent a runt frame: %s", self.topic_id, exc
            )
            return
        stream = self.streams.get(stream_id)
        if stream is None:
            return
        stream.inbox.put_nowait((op, payload))


_Seat = tuple[uuid.UUID, str]


class PreviewHub:
    def __init__(self) -> None:
        self._machines: dict[_Seat, PreviewMachine] = {}
        self._arrivals: dict[_Seat, asyncio.Event] = {}

    # -- the machine's side ----------------------------------------------------

    def machine(self, topic_id: uuid.UUID, seat: str) -> PreviewMachine | None:
        """Whoever is carrying this seat's preview right now. The caller that
        attaches a replacement reads this first, so it can hang up on the machine
        it is displacing rather than leave a socket nothing will ever speak on."""
        return self._machines.get((topic_id, seat))

    def _abandon(self, machine: PreviewMachine) -> None:
        """Tell everything still waiting on this machine that it is gone.

        Nothing else ever will. A proxied WebSocket waits on its stream with no
        deadline — an HMR socket is silent for as long as nobody edits a file —
        so a machine that disappears would otherwise leave one browser socket
        held open per viewer, forever, with no frame ever arriving to end it.
        """
        for stream in list(machine.streams.values()):
            stream.inbox.put_nowait((wire.OP_CLOSE, b"the machine went away"))
        machine.streams.clear()

    def attach(
        self,
        topic_id: uuid.UUID,
        seat: str,
        transport: PreviewTransport,
        *,
        issued: int = 0,
    ) -> PreviewMachine | None:
        """Give this seat's tunnel to ``transport``; None when it may not have it.

        Refused only for a credential OLDER than the live one's. An equal one
        wins: that is the same helper redialling after a dropped connection,
        whose previous socket the backend may not have noticed is dead yet.
        """
        key = (topic_id, seat)
        displaced = self._machines.get(key)
        if displaced is not None:
            if issued < displaced.issued:
                return None
            self._abandon(displaced)
        machine = PreviewMachine(
            topic_id=topic_id, seat=seat, transport=transport, issued=issued
        )
        self._machines[key] = machine
        # POPPED, not merely set: a waiter already holds its own reference, and
        # leaving a set event in the table would make the NEXT wait return at once
        # for a machine that has since gone — the grace period silently skipped
        # exactly when it is needed.
        event = self._arrivals.pop(key, None)
        if event is not None:
            event.set()
        return machine

    def detach(self, machine: PreviewMachine) -> None:
        key = (machine.topic_id, machine.seat)
        # Only if it is still OURS: a replacement helper for the same seat has
        # already taken the slot, and the loser's teardown must not evict it.
        if self._machines.get(key) is machine:
            del self._machines[key]
            self._abandon(machine)

    def is_online(self, topic_id: uuid.UUID, seat: str) -> bool:
        return (topic_id, seat) in self._machines

    async def wait_online(self, topic_id: uuid.UUID, seat: str, timeout: float) -> bool:
        """Whether a helper is connected, waiting up to ``timeout`` for one.

        ``cheese serve`` starts the helper and then declares the preview in the
        same breath, so the declaration usually arrives while the tunnel is still
        upgrading. Without this wait the platform would refuse a preview that is
        one round trip from working.
        """
        key = (topic_id, seat)
        if self.is_online(topic_id, seat):
            return True
        event = self._arrivals.setdefault(key, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        finally:
            if not self.is_online(topic_id, seat):
                self._arrivals.pop(key, None)
        return self.is_online(topic_id, seat)

    # -- the browser's side ----------------------------------------------------

    def open_stream(self, topic_id: uuid.UUID, seat: str) -> PreviewStream | None:
        machine = self._machines.get((topic_id, seat))
        return None if machine is None else machine.open()

    async def request(
        self,
        topic_id: uuid.UUID,
        seat: str,
        *,
        method: str,
        path: str,
        headers: list[tuple[str, str]],
        body: bytes = b"",
        timeout: float = STREAM_TIMEOUT_S,
    ) -> PreviewResponse | None:
        """One HTTP request to the topic's app. None = there is no tunnel, the
        app did not answer, or it answered with more than we will hold."""
        stream = self.open_stream(topic_id, seat)
        if stream is None:
            return None
        try:
            await stream.send(
                wire.OP_REQ,
                wire.encode_meta(
                    {
                        "method": method,
                        "path": path,
                        "headers": [list(h) for h in headers],
                    },
                    body,
                ),
            )
            op, payload = await stream.receive(timeout)
            if op != wire.OP_RESP:
                logger.info("preview request on %s failed: %s", topic_id, payload[:200])
                return None
            meta, first = wire.decode_meta(payload)
            chunks = [first]
            size = len(first)
            while size <= MAX_BODY:
                op, payload = await stream.receive(timeout)
                if op == wire.OP_END:
                    break
                if op != wire.OP_DATA:
                    logger.info(
                        "preview body on %s ended early: %s", topic_id, payload[:200]
                    )
                    return None
                size += len(payload)
                chunks.append(payload)
            if size > MAX_BODY:
                logger.info("preview body on %s is too large to hold", topic_id)
                return None
            return PreviewResponse(
                status=int(meta.get("status", 502)),
                headers=[(str(k), str(v)) for k, v in meta.get("headers") or []],
                body=b"".join(chunks),
            )
        except (TimeoutError, ValueError, OSError, RuntimeError) as exc:
            logger.info("preview request on %s did not complete: %s", topic_id, exc)
            return None
        finally:
            stream.close()

    async def probe(
        self, topic_id: uuid.UUID, seat: str, *, timeout: float = PROBE_TIMEOUT_S
    ) -> bool:
        """Whether something actually answers on the declared port right now.

        A connected helper is not the same as a running app: the agent's dev
        server exits and the tunnel stays up, which is what used to render as a
        white frame with nothing saying why. Any failure is a "no" — this decides
        between a real embed and an honest fallback, never between working and
        broken.
        """
        response = await self.request(
            topic_id,
            seat,
            method="GET",
            path="/",
            headers=[("host", "127.0.0.1")],
            timeout=timeout,
        )
        return response is not None and response.status < 500


# Shared singleton: the tunnel route, the preview proxy and the artifact route
# all import this instance.
preview_hub = PreviewHub()
