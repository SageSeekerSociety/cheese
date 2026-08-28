"""The platform end of 运行环境预览's tunnel: which topic has a live preview, and
one multiplexed stream per browser request.

Symmetric to ``device_hub`` and kept the same way — I/O-free, depending only on a
one-method transport Protocol — so it is exercised with in-process fakes: no
WebSocket, no machine, no DB.

One machine per topic. A topic runs on exactly one machine at a time, and the
helper authenticates with that turn's scoped cheese token, so the topic id in the
token's claims is the whole routing table. A second attach for the same topic
REPLACES the first (the topic moved to another machine, or a screen was
relaunched): keeping both would leave the browser talking to whichever helper the
dict happened to hold.

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
    transport: PreviewTransport
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


class PreviewHub:
    def __init__(self) -> None:
        self._machines: dict[uuid.UUID, PreviewMachine] = {}
        self._arrivals: dict[uuid.UUID, asyncio.Event] = {}

    # -- the machine's side ----------------------------------------------------

    def machine(self, topic_id: uuid.UUID) -> PreviewMachine | None:
        """Whoever is carrying this topic's preview right now. The caller that
        attaches a replacement reads this first, so it can hang up on the machine
        it is displacing rather than leave a socket nothing will ever speak on."""
        return self._machines.get(topic_id)

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
        self, topic_id: uuid.UUID, transport: PreviewTransport
    ) -> PreviewMachine:
        displaced = self._machines.get(topic_id)
        if displaced is not None:
            self._abandon(displaced)
        machine = PreviewMachine(topic_id=topic_id, transport=transport)
        self._machines[topic_id] = machine
        # POPPED, not merely set: a waiter already holds its own reference, and
        # leaving a set event in the table would make the NEXT wait return at once
        # for a machine that has since gone — the grace period silently skipped
        # exactly when it is needed.
        event = self._arrivals.pop(topic_id, None)
        if event is not None:
            event.set()
        return machine

    def detach(self, topic_id: uuid.UUID, transport: PreviewTransport) -> None:
        machine = self._machines.get(topic_id)
        # Only if it is still OURS: a replacement helper for the same topic has
        # already taken the slot, and the loser's teardown must not evict it.
        if machine is not None and machine.transport is transport:
            del self._machines[topic_id]
            self._abandon(machine)

    def is_online(self, topic_id: uuid.UUID) -> bool:
        return topic_id in self._machines

    async def wait_online(self, topic_id: uuid.UUID, timeout: float) -> bool:
        """Whether a helper is connected, waiting up to ``timeout`` for one.

        ``cheese serve`` starts the helper and then declares the preview in the
        same breath, so the declaration usually arrives while the tunnel is still
        upgrading. Without this wait the platform would refuse a preview that is
        one round trip from working.
        """
        if self.is_online(topic_id):
            return True
        event = self._arrivals.setdefault(topic_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        finally:
            if not self.is_online(topic_id):
                self._arrivals.pop(topic_id, None)
        return self.is_online(topic_id)

    def on_frame(self, topic_id: uuid.UUID, data: bytes) -> None:
        """Route one inbound frame to the stream that is waiting for it."""
        machine = self._machines.get(topic_id)
        if machine is None:
            return
        try:
            op, stream_id, payload = wire.decode(data)
        except ValueError as exc:
            logger.warning(
                "preview machine for %s sent a runt frame: %s", topic_id, exc
            )
            return
        stream = machine.streams.get(stream_id)
        if stream is None:
            return
        stream.inbox.put_nowait((op, payload))

    # -- the browser's side ----------------------------------------------------

    def open_stream(self, topic_id: uuid.UUID) -> PreviewStream | None:
        machine = self._machines.get(topic_id)
        return None if machine is None else machine.open()

    async def request(
        self,
        topic_id: uuid.UUID,
        *,
        method: str,
        path: str,
        headers: list[tuple[str, str]],
        body: bytes = b"",
        timeout: float = STREAM_TIMEOUT_S,
    ) -> PreviewResponse | None:
        """One HTTP request to the topic's app. None = there is no tunnel, the
        app did not answer, or it answered with more than we will hold."""
        stream = self.open_stream(topic_id)
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
        self, topic_id: uuid.UUID, *, timeout: float = PROBE_TIMEOUT_S
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
            method="GET",
            path="/",
            headers=[("host", "127.0.0.1")],
            timeout=timeout,
        )
        return response is not None and response.status < 500


# Shared singleton: the tunnel route, the preview proxy and the artifact route
# all import this instance.
preview_hub = PreviewHub()
