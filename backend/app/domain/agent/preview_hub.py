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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

import anyio

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


class PreviewAdmissionError(Exception):
    def __init__(
        self,
        state: Literal[
            "transport_unavailable",
            "app_unavailable",
            "instance_gone",
            "instance_identity_unsupported",
        ],
    ) -> None:
        super().__init__(state)
        self.state: Literal[
            "transport_unavailable",
            "app_unavailable",
            "instance_gone",
            "instance_identity_unsupported",
        ] = state


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


MAX_QUEUED_BYTES = 1024 * 1024
MAX_QUEUED_FRAMES = 256
SEND_TIMEOUT_S = 5.0
MAX_STREAMS = 64

# How many page requests a helper works on at once (its `_MAX_HTTP_STREAMS`).
# It answers the next one with "preview streams busy", and a page in a dev
# server's development mode asks for hundreds of modules at once: forwarding
# them all made every request past the sixteenth a 404 and the page a white
# frame. Requests wait here for a free slot instead of being refused there.
HTTP_STREAMS = 16
# A slot freed on this side can still be counted on the helper until it reads
# the close; a request that hits that window is told "busy" and tries again.
BUSY = b"preview streams busy"
BUSY_RETRY_S = 0.05


class PreviewStream:
    """One browser request (or one browser WebSocket) and its whole life."""

    def __init__(self, machine: "PreviewMachine", stream_id: int) -> None:
        self._machine = machine
        self.id = stream_id
        self.inbox: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(MAX_QUEUED_FRAMES)
        self._queued_bytes = 0
        self._closed = False
        self._terminal = False
        self._close_task: asyncio.Task[None] | None = None
        # Holds one of the machine's page-request slots until it is forgotten.
        self.slot = False

    @property
    def close_metadata(self) -> bool:
        return not self._terminal and "ws-close-v1" in self._machine.capabilities

    @property
    def maintenance(self) -> bool:
        return self._machine.maintenance

    async def send(self, op: int, payload: bytes = b"") -> None:
        await self._machine.send(op, self.id, payload)

    def offer(self, op: int, payload: bytes) -> None:
        if self._closed or self._terminal:
            return
        # Legacy RESP frames may include initial body bytes. decode_meta checks
        # their header separately; retain the existing bounded queue allowance.
        limit = wire._CHUNK if op == wire.OP_DATA else MAX_QUEUED_BYTES
        if (
            len(payload) > limit
            or self._queued_bytes + len(payload) > MAX_QUEUED_BYTES
            or self.inbox.full()
        ):
            self.terminate(b"preview consumer queue overflow")
            self._machine.cancel_later(self.id)
            return
        self._queued_bytes += len(payload)
        self.inbox.put_nowait((op, payload))

    def terminate(self, reason: bytes) -> None:
        if self._closed or self._terminal:
            return
        self._terminal = True
        while not self.inbox.empty():
            self.inbox.get_nowait()
        self._queued_bytes = len(reason)
        self.inbox.put_nowait((wire.OP_CLOSE, reason))
        self._machine.forget(self.id)

    async def receive(
        self, timeout: float | None = STREAM_TIMEOUT_S
    ) -> tuple[int, bytes]:
        item = await asyncio.wait_for(self.inbox.get(), timeout=timeout)
        self._queued_bytes -= len(item[1])
        return item

    def close(self) -> None:
        self._closed = True
        self._machine.forget(self.id)

    async def aclose(self, *, cancel: bool = True, payload: bytes = b"") -> None:
        if not self._closed:
            terminal = self._terminal
            self.close()
            if cancel and not terminal:
                self._close_task = asyncio.create_task(self._send_close(payload))
        if self._close_task is None:
            return
        cancelled = False
        # AnyIO disconnect scopes and direct asyncio task cancellation are
        # different paths. Own the bounded write through either, then propagate
        # direct cancellation only after the wire task has been reaped.
        with anyio.CancelScope(shield=True):
            while True:
                try:
                    await asyncio.shield(self._close_task)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                    if self._close_task.done():
                        self._close_task.result()
                        break
        if cancelled:
            raise asyncio.CancelledError

    async def _send_close(self, payload: bytes) -> None:
        try:
            await self.send(wire.OP_CLOSE, payload)
        except (OSError, RuntimeError, TimeoutError):
            pass


@dataclass
class PreviewHttpResponse:
    status: int
    headers: list[tuple[str, str]]
    stream: PreviewStream
    first: bytes = b""
    timeout: float = STREAM_TIMEOUT_S
    ended: bool = False

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        try:
            if self.first:
                yield self.first
                self.first = b""
            while not self.ended:
                op, payload = await self.stream.receive(self.timeout)
                if op == wire.OP_END:
                    self.ended = True
                    break
                if op != wire.OP_DATA:
                    raise ConnectionError(
                        f"preview response interrupted: {payload[:200]!r}"
                    )
                yield payload
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        await self.stream.aclose(cancel=not self.ended)


@dataclass
class PreviewMachine:
    topic_id: uuid.UUID
    seat: str
    transport: PreviewTransport
    # When the credential it dialled with was issued; see the module docstring.
    issued: int = 0
    capabilities: frozenset[str] = frozenset()
    streams: dict[int, PreviewStream] = field(default_factory=dict)
    next_stream: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_cancels: set[asyncio.Task[None]] = field(default_factory=set)
    epoch: str = field(default_factory=lambda: uuid.uuid4().hex)
    maintenance: bool = False
    stopped: bool = False
    failed: asyncio.Event = field(default_factory=asyncio.Event)
    http_slots: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(HTTP_STREAMS)
    )

    async def send(self, op: int, stream_id: int, payload: bytes = b"") -> None:
        if self.stopped:
            raise ConnectionError("preview tunnel stopped")
        try:
            async with asyncio.timeout(SEND_TIMEOUT_S):
                async with self.send_lock:
                    if self.stopped:
                        raise ConnectionError("preview tunnel stopped")
                    await self.transport.send_bytes(wire.encode(op, stream_id, payload))
        except (TimeoutError, OSError, RuntimeError):
            self.stopped = True
            self.failed.set()
            for stream in list(self.streams.values()):
                stream.terminate(b"preview tunnel write failed")
            raise

    def cancel_later(self, stream_id: int) -> None:
        async def cancel() -> None:
            try:
                await self.send(wire.OP_CLOSE, stream_id)
            except (OSError, RuntimeError, TimeoutError):
                pass

        if self.stopped:
            return
        task = asyncio.create_task(cancel())
        self.pending_cancels.add(task)
        task.add_done_callback(self.pending_cancels.discard)

    async def drain(self) -> None:
        tasks = tuple(self.pending_cancels)
        for task in tasks:
            task.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
        self.pending_cancels.difference_update(tasks)

    def open(self) -> PreviewStream | None:
        if self.stopped or len(self.streams) >= MAX_STREAMS:
            return None
        self.next_stream += 1
        stream = PreviewStream(self, self.next_stream)
        self.streams[stream.id] = stream
        return stream

    async def open_http(self, timeout: float | None) -> PreviewStream | None:
        """A stream for one page request, once the helper has room for it."""
        try:
            await asyncio.wait_for(self.http_slots.acquire(), timeout)
        except TimeoutError:
            return None
        stream = self.open()
        if stream is None:
            self.http_slots.release()
            return None
        stream.slot = True
        return stream

    def forget(self, stream_id: int) -> None:
        stream = self.streams.pop(stream_id, None)
        if stream is not None and stream.slot:
            stream.slot = False
            self.http_slots.release()

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
        stream.offer(op, payload)


_Seat = tuple[uuid.UUID, str]


class PreviewHub:
    def __init__(self) -> None:
        self.accepting = True
        self._machines: dict[_Seat, PreviewMachine] = {}
        self._arrivals: dict[_Seat, asyncio.Event] = {}
        self._waiting: dict[_Seat, int] = {}

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
        machine.stopped = True
        for task in machine.pending_cancels:
            task.cancel()
        for stream in list(machine.streams.values()):
            stream.terminate(b"the machine went away")
        machine.streams.clear()

    def attach(
        self,
        topic_id: uuid.UUID,
        seat: str,
        transport: PreviewTransport,
        *,
        issued: int = 0,
        capabilities: frozenset[str] = frozenset(),
    ) -> PreviewMachine | None:
        """Give this seat's tunnel to ``transport``; None when it may not have it.

        Refused only for a credential OLDER than the live one's. An equal one
        wins: that is the same helper redialling after a dropped connection,
        whose previous socket the backend may not have noticed is dead yet.
        """
        if not self.accepting:
            return None
        key = (topic_id, seat)
        displaced = self._machines.get(key)
        if displaced is not None:
            if issued < displaced.issued:
                return None
            self._abandon(displaced)
        machine = PreviewMachine(
            topic_id=topic_id,
            seat=seat,
            transport=transport,
            issued=issued,
            capabilities=wire.CAPABILITIES.intersection(capabilities),
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
        machine = self._machines.get((topic_id, seat))
        return self.accepting and machine is not None and not machine.stopped

    async def wait_online(self, topic_id: uuid.UUID, seat: str, timeout: float) -> bool:
        """Whether a helper is connected, waiting up to ``timeout`` for one.

        ``cheese serve`` starts the helper and then declares the preview in the
        same breath, so the declaration usually arrives while the tunnel is still
        upgrading. Without this wait the platform would refuse a preview that is
        one round trip from working.
        """
        if not self.accepting:
            return False
        key = (topic_id, seat)
        if self.is_online(topic_id, seat):
            return True
        event = self._arrivals.setdefault(key, asyncio.Event())
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        finally:
            remaining = self._waiting[key] - 1
            if remaining:
                self._waiting[key] = remaining
            else:
                self._waiting.pop(key)
                if self._arrivals.get(key) is event:
                    self._arrivals.pop(key)
        return self.is_online(topic_id, seat)

    async def shutdown(self) -> None:
        """Stop admission and wake waiters; ordinary loss lets helpers redial."""
        self.accepting = False
        for event in self._arrivals.values():
            event.set()
        self._arrivals.clear()
        machines = tuple(self._machines.values())
        for machine in machines:
            machine.maintenance = True
            self.detach(machine)
            machine.failed.set()
        await asyncio.gather(*(machine.drain() for machine in machines))

    # -- the browser's side ----------------------------------------------------

    def open_stream(self, topic_id: uuid.UUID, seat: str) -> PreviewStream | None:
        machine = self._machines.get((topic_id, seat))
        return None if machine is None or not self.accepting else machine.open()

    async def request_stream(
        self,
        topic_id: uuid.UUID,
        seat: str,
        *,
        method: str,
        path: str,
        headers: list[tuple[str, str]],
        body: bytes = b"",
        timeout: float = STREAM_TIMEOUT_S,
        instance: str | None = None,
        inspect_instance: bool = False,
        machine: PreviewMachine | None = None,
        typed: bool = False,
    ) -> PreviewHttpResponse | None:
        """Return at RESP; the caller owns the body and cancellation."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            # A captured inspection must never jump to a replacement connection.
            if machine is not None:
                if not self.accepting or self.machine(topic_id, seat) is not machine:
                    return None
                target: PreviewMachine | None = machine
            else:
                target = (
                    self._machines.get((topic_id, seat)) if self.accepting else None
                )
            stream = (
                await target.open_http(max(0.0, deadline - loop.time()))
                if target is not None
                else None
            )
            if stream is None:
                if typed:
                    raise PreviewAdmissionError("transport_unavailable")
                return None
            transferred = False
            busy = False
            try:
                if (
                    instance or inspect_instance
                ) and "instance-v1" not in stream._machine.capabilities:
                    if typed:
                        raise PreviewAdmissionError("instance_identity_unsupported")
                    return None
                await stream.send(
                    wire.OP_REQ,
                    wire.encode_meta(
                        {
                            "method": method,
                            "path": path,
                            "headers": [list(h) for h in headers],
                            **({"instance": instance} if instance else {}),
                            **({"inspect_instance": True} if inspect_instance else {}),
                        },
                        body,
                    ),
                )
                op, payload = await stream.receive(max(0.0, deadline - loop.time()))
                if op == wire.OP_ERR and payload == BUSY and loop.time() < deadline:
                    busy = True
                    continue
                if op != wire.OP_RESP:
                    if typed:
                        raise PreviewAdmissionError(
                            "instance_gone"
                            if op == wire.OP_ERR and payload == b"preview instance gone"
                            else "app_unavailable"
                            if not stream._machine.stopped
                            else "transport_unavailable"
                        )
                    logger.info(
                        "preview request on %s failed: %s", topic_id, payload[:200]
                    )
                    return None
                meta, first = wire.decode_meta(payload)
                response = PreviewHttpResponse(
                    status=int(meta.get("status", 502)),
                    headers=[(str(k), str(v)) for k, v in meta.get("headers") or []],
                    stream=stream,
                    first=first,
                    timeout=timeout,
                )
                transferred = True
                return response
            except (TimeoutError, ValueError, OSError, RuntimeError) as exc:
                logger.info("preview request on %s did not complete: %s", topic_id, exc)
                if typed:
                    raise PreviewAdmissionError("transport_unavailable") from exc
                return None
            finally:
                if not transferred:
                    # The helper never took a refused stream, so there is
                    # nothing on its side to close.
                    await stream.aclose(cancel=not busy)
                if busy:
                    await asyncio.sleep(BUSY_RETRY_S)

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
        response = await self.request_stream(
            topic_id,
            seat,
            method=method,
            path=path,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        if response is None:
            return None
        try:
            chunks = []
            size = 0
            async for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BODY:
                    return None
                chunks.append(chunk)
            return PreviewResponse(response.status, response.headers, b"".join(chunks))
        except (TimeoutError, ValueError, OSError, RuntimeError) as exc:
            logger.info("preview buffered response interrupted: %s", exc)
            return None
        finally:
            await response.aclose()

    async def instance(self, topic_id: uuid.UUID, seat: str) -> str | None:
        response = await self.request_stream(
            topic_id,
            seat,
            method="HEAD",
            path="/",
            headers=[],
            inspect_instance=True,
            timeout=PROBE_TIMEOUT_S,
        )
        if response is None:
            return None
        try:
            value = dict(response.headers).get("x-cheese-instance", "")
            return (
                value
                if len(value) == 64 and all(c in "0123456789abcdef" for c in value)
                else None
            )
        finally:
            await response.aclose()

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
