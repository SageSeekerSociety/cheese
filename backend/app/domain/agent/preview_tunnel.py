"""运行环境预览's machine half: the app the agent started, carried back out.

SHIPPED TO A MACHINE AND RUN THERE, so the same two rules as ``machine_tunnel``
hold and neither is negotiable: **stdlib only** (a machine has python3 and no
venv, and `pip install` on a box the platform does not own is not a deployment
step), and **no imports from ``app.*``** (there is no backend package there).
It lives in the repo as a real module anyway, so it is linted and unit-tested
like everything else; the launcher ships it by reading this file's own bytes.

Why it exists. A turn runs on someone's enrolled laptop or on a leased Cloud
box, and neither is the platform's: both sit behind NAT with zero inbound ports,
which is the whole point of the connector dialling OUT. So there is no address
the backend can open a socket to, and the old preview — which read a port the
backend's own docker had published on its own host — has nothing left to read.

What every machine already has is the path it uses for everything else: an
outbound WebSocket to the backend. This opens a SECOND one, and multiplexes the
browser's requests down it:

    browser ─► backend ─► this, on the machine ─► 127.0.0.1:<the declared port>

Deliberately a second connection rather than a second message type on the
connector's ``link.Msg`` channel. That channel is a turn's lifeline — prompts,
hooks, the 现场 relay — and it serialises every frame through one lock at each
end; a dev server's assets and HMR chatter would queue ahead of a prompt. Same
network path, same gateway, same dial-out story; separate failure domain.

**The address is never on the wire.** This process dials exactly one place: the
loopback port named in ``--port-file``, which only a process already running as
the agent on this machine can write (``cheese serve``). Nothing the backend
sends can change it. So a compromised backend cannot turn an enrolled laptop
into a port scanner, and a preview cannot be pointed at a second port by
anything reaching this end.

The frame codec at the top is imported by the BACKEND too (it is stdlib-only, so
importing it costs the backend nothing), which is what keeps one definition of
the wire instead of two that drift.
"""

import argparse
import base64
import http.client
import json
import logging
import os
import queue
import secrets
import socket
import ssl
import struct
import sys
import threading
import time
from urllib.parse import urlparse

logger = logging.getLogger("cheese.preview")

# --- the wire ------------------------------------------------------------------
#
# One binary WebSocket message per frame: op | stream id | payload. A stream is
# one browser request (or one browser WebSocket) and its whole life.

OP_REQ = 1  # backend → machine: an HTTP request, header + body in one frame
OP_WS_OPEN = 2  # backend → machine: open a WebSocket to the app
OP_WS_MSG = 3  # both ways: one WebSocket message
OP_CLOSE = 4  # both ways: this stream is over
OP_RESP = 5  # machine → backend: status + headers, body follows
OP_DATA = 6  # machine → backend: one body chunk
OP_END = 7  # machine → backend: the body is complete
OP_ERR = 8  # machine → backend: this stream failed, payload says why
OP_WS_OK = 9  # machine → backend: the app accepted the WebSocket

WS_TEXT = 0
WS_BINARY = 1

# The close code the backend hangs up with when this helper no longer carries its
# teammate's preview: a newer launch of the same teammate has taken the tunnel,
# or already holds it when this one dials. It is the one close that must NOT be
# redialled — the helper that replaced this one is alive, and dialling back in
# would take the tunnel from it, which is how two helpers used to knock each
# other off once a second.
CLOSE_SUPERSEDED = 4001

_HEAD = struct.Struct("!BI")


def encode(op: int, stream: int, payload: bytes = b"") -> bytes:
    return _HEAD.pack(op, stream) + payload


def decode(frame: bytes) -> tuple[int, int, bytes]:
    """``(op, stream, payload)``. Raises ``ValueError`` on a runt frame — the
    caller drops the connection rather than guessing at a truncated header."""
    if len(frame) < _HEAD.size:
        raise ValueError("preview frame shorter than its header")
    op, stream = _HEAD.unpack_from(frame)
    return op, stream, frame[_HEAD.size :]


def encode_meta(meta: dict, body: bytes = b"") -> bytes:
    """A frame payload carrying a JSON header and (optionally) bytes after it."""
    blob = json.dumps(meta, separators=(",", ":")).encode()
    return struct.pack("!I", len(blob)) + blob + body


def decode_meta(payload: bytes) -> tuple[dict, bytes]:
    if len(payload) < 4:
        raise ValueError("preview frame carries no header length")
    (size,) = struct.unpack_from("!I", payload)
    if len(payload) < 4 + size:
        raise ValueError("preview frame header is truncated")
    meta = json.loads(payload[4 : 4 + size].decode())
    if not isinstance(meta, dict):
        raise ValueError("preview frame header is not an object")
    return meta, payload[4 + size :]


# --- the minimum WebSocket a client needs (RFC 6455) ---------------------------
#
# Duplicated from ``machine_tunnel`` rather than shared, and on purpose: that
# helper carries EVERY model request on EVERY machine, and a preview feature must
# not be able to reach into its process or its file. Two shipped files, two
# processes, two failure domains.

_OP_TEXT = 0x1
_OP_BIN = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

_CHUNK = 65536
_HANDSHAKE_TIMEOUT_S = 15.0


class PreviewError(RuntimeError):
    """The tunnel could not be established or a stream could not be served.
    ``retryable`` marks ABSENCE (nothing answered) as opposed to REFUSAL (it
    answered and said no) — only absence is worth waiting out."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _read_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise PreviewError("the socket closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(sock: socket.socket, payload: bytes, opcode: int = _OP_BIN) -> None:
    """One FIN frame, masked. A client MUST mask (RFC 6455 §5.3) — an unmasked
    client frame is a protocol error the server has to reject, which reads as a
    mysterious disconnect rather than as a bug here."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = secrets.token_bytes(4)
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def recv_message(
    sock: socket.socket, write_lock: threading.Lock | None = None, *, control=None
) -> tuple[int, bytes]:
    """The next data message as ``(opcode, payload)``, reassembled. A close from
    the peer comes back as ``(_OP_CLOSE, <its payload>)``: the payload carries
    the close code, and one of those codes (``CLOSE_SUPERSEDED``) changes what
    the caller does next. Fragments and control frames are handled here rather
    than by the caller: a peer may split a message across continuations and
    interleave a ping at any point, and treating either as data corrupts the
    stream.

    ``write_lock`` is the caller's lock over writes to this same socket. Answering
    a ping is a WRITE from the reading thread, so on any socket another thread
    also writes to, that lock has to cover the pong too — two ``sendall`` calls
    interleaving produce a frame neither side can parse.
    """
    payload = bytearray()
    kind = _OP_BIN
    first_frame = True
    while True:
        first, second = _read_exact(sock, 2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", _read_exact(sock, 2))
        elif length == 127:
            (length,) = struct.unpack("!Q", _read_exact(sock, 8))
        mask = _read_exact(sock, 4) if masked else b""
        data = _read_exact(sock, length) if length else b""
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

        if opcode == _OP_CLOSE:
            return opcode, data
        if opcode == _OP_PING:
            if control is not None:
                control(data, _OP_PONG)
            elif write_lock is None:
                send_frame(sock, data, _OP_PONG)
            else:
                with write_lock:
                    send_frame(sock, data, _OP_PONG)
            continue
        if opcode == _OP_PONG:
            continue

        if first_frame and opcode in (_OP_TEXT, _OP_BIN):
            kind = opcode
            first_frame = False
        payload.extend(data)
        if fin:
            return kind, bytes(payload)


def open_ws(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    ca_path: str | None = None,
    insecure: bool = False,
    timeout: float = _HANDSHAKE_TIMEOUT_S,
    idle_timeout: float | None = None,
    publish=None,
) -> tuple[socket.socket, dict[str, str]]:
    """A connected, upgraded WebSocket, plus the response headers (lower-cased).

    ``idle_timeout`` bounds how long the socket may go silent once upgraded. Only
    a connection this end HEARTBEATS may set one — otherwise a quiet peer is
    perfectly healthy (an HMR socket says nothing until somebody edits a file).
    Where a heartbeat does run, silence past the window is the only way to notice
    a half-open connection: a sleeping laptop or a dropped wifi leaves a socket
    that reads as open forever and delivers nothing.
    """
    parsed = urlparse(url)
    secure = parsed.scheme in ("wss", "https")
    host = parsed.hostname or ""
    port = parsed.port or (443 if secure else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    if publish is not None:
        # Owned streams only dial the declared IPv4 loopback port.
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(timeout)
        publish(raw)
        try:
            raw.connect((host, port))
        except BaseException:
            _shutdown(raw)
            raise
    else:
        raw = socket.create_connection((host, port), timeout=timeout)
    if secure:
        if insecure:
            context = ssl._create_unverified_context()  # noqa: S323 — opt-in only
        else:
            context = ssl.create_default_context(cafile=ca_path or None)
        raw = context.wrap_socket(raw, server_hostname=host)

    key = base64.b64encode(secrets.token_bytes(16)).decode()
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {parsed.netloc}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    for name, value in (headers or {}).items():
        lines.append(f"{name}: {value}")
    raw.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

    head = bytearray()
    while b"\r\n\r\n" not in head:
        chunk = raw.recv(1)
        if not chunk:
            raw.close()
            raise PreviewError("the peer closed during the upgrade", retryable=True)
        head.extend(chunk)
    text = head.decode(errors="replace")
    status = text.split("\r\n", 1)[0]
    if " 101" not in status:
        raw.close()
        # 403 = the token no longer proves this topic, 404 = a backend without
        # this route, 502/503/504 = the gateway cannot reach it right now. Only
        # the last family is absence worth waiting out.
        raise PreviewError(
            f"upgrade refused: {status}",
            retryable=any(f" {code} " in status for code in (502, 503, 504)),
        )
    answered: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            answered[name.strip().lower()] = value.strip()
    raw.settimeout(idle_timeout)
    return raw, answered


# --- what this machine will dial ----------------------------------------------


class PortSource:
    """The one loopback port this helper is allowed to reach, read fresh for
    EVERY stream.

    Deliberately not a value captured at startup. ``cheese serve`` writes the
    file, and an agent that restarts its dev server on a different port must not
    have to wait for a new turn to relaunch this process. Reading per stream also
    keeps the fact that matters true by construction: the port comes from the
    machine's own disk, never from the wire.
    """

    def __init__(self, path: str) -> None:
        self._path = path

    def get(self) -> int:
        try:
            with open(self._path) as handle:
                # Older helpers stored a mount on line two; only the port
                # survives an upgrade because content hosts preserve app paths.
                raw = handle.readline().strip()
        except OSError as exc:
            raise PreviewError(f"no preview port declared: {exc}") from exc
        if not raw.isdigit() or not (1 <= int(raw) <= 65535):
            raise PreviewError(f"{self._path} does not name a port")
        return int(raw)


class TokenSource:
    """The scoped cheese token, read fresh for EVERY connection — the launcher
    rewrites the file each turn, so a refreshed token reaches a still-running
    helper without restarting it."""

    def __init__(self, path: str) -> None:
        self._path = path

    def get(self) -> str:
        try:
            with open(self._path) as handle:
                return handle.read().strip()
        except OSError as exc:
            raise PreviewError(f"could not read {self._path}: {exc}") from exc


# --- one connection's worth of work -------------------------------------------

# The backend buffers a response before handing it to the browser, so an
# unbounded body would be its memory, not this machine's. Cut it here, where the
# reason can be reported on the stream instead of surfacing as a dead panel.
_MAX_BODY = 32 * 1024 * 1024
_REQUEST_TIMEOUT_S = 30.0

# A preview nobody is looking at is a connection with no traffic on it, and the
# gateway in front of the backend closes an idle upgraded connection (an hour on
# ours; less on some paths in between). Without a heartbeat the tunnel would go
# quietly down every time a room stopped watching, and the panel would report the
# machine as offline until the next turn relaunched the helper. Comfortably under
# any of those windows.
_PING_INTERVAL_S = 30.0

# How long the backend may say nothing at all before this end calls the
# connection dead and dials again. Only meaningful BECAUSE of the ping above: the
# peer answers every one, so silence past this window is not a quiet room, it is
# a socket that will never deliver anything again (a slept laptop, a dropped
# wifi) and reads as perfectly open until something asks.
_IDLE_TIMEOUT_S = 90.0


_MAX_HTTP_STREAMS = 16
_MAX_WS_STREAMS = 16
_MAX_OUTBOX_BYTES = 1024 * 1024
_WRITE_TIMEOUT_S = 5.0
_DRAIN_TIMEOUT_S = 5.0
CAPS_HEADER = "x-cheese-preview-caps"
CAPABILITIES = frozenset(
    {"http-stream-v1", "http-cancel-v1", "raw-http-v1", "ws-close-v1"}
)


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def close_payload(code: int, reason: str = "") -> bytes:
    raw = reason.encode("utf-8")[:123]
    raw = raw.decode("utf-8", errors="ignore").encode("utf-8")
    return struct.pack("!H", code) + raw


def parse_close(payload: bytes) -> tuple[int, str]:
    if not payload:
        return 1011, "upstream closed without status"
    if len(payload) == 1 or len(payload) > 125:
        return 1002, "invalid close length"
    code = struct.unpack("!H", payload[:2])[0]
    if (
        code
        not in {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014}
        and not 3000 <= code <= 4999
    ):
        return 1002, "invalid close code"
    try:
        reason = payload[2:].decode("utf-8")
    except UnicodeError:
        return 1007, "invalid close UTF-8"
    return code, reason


class _StreamState:
    def __init__(self, kind: int) -> None:
        self.kind = kind
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.socket: socket.socket | None = None
        self.worker: threading.Thread | None = None
        self.writer: threading.Thread | None = None
        self.outbox: queue.Queue = queue.Queue(32)
        self.queued_bytes = 0
        self.closing = False
        self.write_lock = threading.Lock()


class _OwnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, port: int, publish):
        super().__init__("127.0.0.1", port, timeout=_REQUEST_TIMEOUT_S)
        self._publish = publish

    def connect(self) -> None:
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(_REQUEST_TIMEOUT_S)
        self._publish(raw)
        try:
            raw.connect(("127.0.0.1", self.port))
            self.sock = raw
        except BaseException:
            _shutdown(raw)
            raise


class Session:
    """One WebSocket to the backend, and the streams multiplexed over it."""

    def __init__(
        self, sock: socket.socket, ports: PortSource, *, capabilities=frozenset()
    ) -> None:
        self._sock = sock
        self._ports = ports
        self.capabilities = CAPABILITIES.intersection(capabilities)
        self._write_lock = threading.Lock()
        self._streams: dict[int, _StreamState] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self.drained = True
        # Why the backend let go of this helper, when it said it no longer wants
        # it (``CLOSE_SUPERSEDED``); None for every other end of a session.
        self.superseded: str | None = None

    def send(self, op: int, stream: int, payload: bytes = b"") -> None:
        """One frame to the backend, or nothing at all if the tunnel has gone.

        A dead tunnel is not this caller's problem to handle: every stream is
        writing into the same socket from its own thread, the reader is already
        tearing the session down, and there is nowhere for the news to go. Raising
        here only turned into unhandled exceptions on threads nobody watches —
        one per stream still in flight when a connection dropped.
        """
        self._send_tunnel(encode(op, stream, payload), _OP_BIN)

    def _send_tunnel(self, payload: bytes, opcode: int) -> None:
        if self._stopped.is_set():
            return
        if not self._write_lock.acquire(timeout=_WRITE_TIMEOUT_S):
            self._stop()
            return
        timer = threading.Timer(_WRITE_TIMEOUT_S, self._stop)
        timer.daemon = True
        try:
            timer.start()
            send_frame(self._sock, payload, opcode)
        except OSError:
            self._stop()
        finally:
            timer.cancel()
            self._write_lock.release()

    def serve(self) -> None:
        """Read frames until the backend goes away.

        Losing the connection RETURNS rather than raises: it is the normal end of
        a session (a redeploy, a laptop's wifi), the caller's usual answer is to
        dial again, and an exception escaping here would be an unhandled one on
        a thread nobody is watching. The one end that is not answered by dialling
        again is recorded in ``superseded``.
        """
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._keepalive, args=(stop,), daemon=True)
        heartbeat.start()
        try:
            while True:
                kind, data = recv_message(self._sock, control=self._send_tunnel)
                if kind == _OP_CLOSE:
                    if data[:2] == struct.pack("!H", CLOSE_SUPERSEDED):
                        self.superseded = data[2:].decode(errors="replace")
                    return
                try:
                    op, stream, payload = decode(data)
                except ValueError as exc:
                    logger.warning("dropping a malformed frame: %s", exc)
                    continue
                self._dispatch(op, stream, payload)
        except (OSError, PreviewError) as exc:
            logger.info("preview tunnel closed: %s", exc)
        finally:
            stop.set()
            self._close_all()
            heartbeat.join(timeout=_DRAIN_TIMEOUT_S)
            self.drained = self.drained and not heartbeat.is_alive()

    def _keepalive(self, stop: threading.Event) -> None:
        """Ping until the session ends — see _PING_INTERVAL_S."""
        while not stop.wait(_PING_INTERVAL_S):
            self._send_tunnel(b"", _OP_PING)
            if self._stopped.is_set():
                return

    def _publish(self, state: _StreamState, sock: socket.socket) -> None:
        with self._lock:
            if not state.cancelled.is_set() and not self._stopped.is_set():
                state.socket = sock
                return
        _shutdown(sock)
        raise PreviewError("stream cancelled")

    def _cancel(self, stream: int) -> None:
        with self._lock:
            state = self._streams.get(stream)
            if state is None or state.cancelled.is_set():
                return
            state.cancelled.set()
            sock = state.socket
        if sock is not None:
            _shutdown(sock)

    def _stop(self) -> None:
        self._stopped.set()
        _shutdown(self._sock)
        with self._lock:
            streams = list(self._streams)
        for sid in streams:
            self._cancel(sid)

    def _run_stream(self, stream: int, payload: bytes, state: _StreamState) -> None:
        try:
            if state.kind == OP_REQ:
                self._serve_request(stream, payload, state)
            else:
                self._serve_ws(stream, payload, state)
        finally:
            self._cancel(stream)
            if state.writer is not None:
                state.writer.join(timeout=_DRAIN_TIMEOUT_S)
            if state.writer is not None and state.writer.is_alive():
                self.drained = False
                self._stop()
            else:
                with self._lock:
                    if self._streams.get(stream) is state:
                        del self._streams[stream]
                state.done.set()

    def _dispatch(self, op: int, stream: int, payload: bytes) -> None:
        if op in (OP_REQ, OP_WS_OPEN):
            with self._lock:
                limit = _MAX_HTTP_STREAMS if op == OP_REQ else _MAX_WS_STREAMS
                busy = (
                    self._stopped.is_set()
                    or stream in self._streams
                    or sum(state.kind == op for state in self._streams.values())
                    >= limit
                )
                if not busy:
                    state = _StreamState(op)
                    self._streams[stream] = state
            if busy:
                self.send(OP_ERR, stream, b"preview streams busy")
                return
            state.worker = threading.Thread(
                target=self._run_stream, args=(stream, payload, state), daemon=True
            )
            state.worker.start()
        elif op == OP_WS_MSG:
            self._forward_ws(stream, payload)
        elif op == OP_CLOSE:
            if payload and "ws-close-v1" in self.capabilities:
                with self._lock:
                    state = self._streams.get(stream)
                    if (
                        state is not None
                        and state.kind == OP_WS_OPEN
                        and not state.cancelled.is_set()
                    ):
                        if state.closing:
                            return
                        closing = b"\x02" + close_payload(*parse_close(payload))
                        try:
                            state.outbox.put_nowait(closing)
                            state.closing = True
                            state.queued_bytes += len(closing)
                            return
                        except queue.Full:
                            pass
            self._cancel(stream)

    # -- HTTP ------------------------------------------------------------------

    def _serve_request(self, stream: int, payload: bytes, state: _StreamState) -> None:
        try:
            meta, body = decode_meta(payload)
        except ValueError as exc:
            self.send(OP_ERR, stream, str(exc).encode())
            return
        try:
            port = self._ports.get()
        except PreviewError as exc:
            self.send(OP_ERR, stream, str(exc).encode())
            return
        conn = _OwnedHTTPConnection(port, lambda sock: self._publish(state, sock))
        try:
            headers = {k: v for k, v in meta.get("headers") or []}
            conn.request(
                meta.get("method", "GET"), meta.get("path", "/"), body, headers
            )
            response = conn.getresponse()
            self.send(
                OP_RESP,
                stream,
                encode_meta(
                    {
                        "status": response.status,
                        # http.client undoes chunk framing, not gzip encoding.
                        "headers": [
                            [k, v]
                            for k, v in response.getheaders()
                            if k.lower() != "transfer-encoding"
                        ],
                    }
                ),
            )
            sent = 0
            while not state.cancelled.is_set():
                chunk = response.read1(_CHUNK)
                if not chunk:
                    break
                sent += len(chunk)
                if "http-stream-v1" not in self.capabilities and sent > _MAX_BODY:
                    self.send(OP_ERR, stream, b"the response is too large to preview")
                    return
                if not state.cancelled.is_set():
                    self.send(OP_DATA, stream, chunk)
            if not state.cancelled.is_set():
                if response.length not in (None, 0):
                    raise http.client.IncompleteRead(b"", response.length)
                self.send(OP_END, stream)
        except Exception as exc:  # noqa: BLE001 — see below
            # Everything, deliberately. The app is whatever the agent started, so
            # what comes back is not a shape this end can enumerate: a refused
            # connection, a truncated body, a status line that is not one. Every
            # such case has the same right answer — say so ON THE STREAM — and the
            # alternative is a thread dying quietly while the browser waits out a
            # timeout with nothing anywhere naming the cause.
            if not state.cancelled.is_set():
                self.send(OP_ERR, stream, f"{exc.__class__.__name__}: {exc}".encode())
        finally:
            conn.close()

    # -- WebSocket (a dev server's HMR socket, mostly) --------------------------

    def _serve_ws(self, stream: int, payload: bytes, state: _StreamState) -> None:
        try:
            meta, _ = decode_meta(payload)
            port = self._ports.get()
        except (ValueError, PreviewError) as exc:
            self.send(OP_ERR, stream, str(exc).encode())
            return
        headers = {k: v for k, v in meta.get("headers") or []}
        try:
            upstream, answered = open_ws(
                f"ws://127.0.0.1:{port}{meta.get('path', '/')}",
                headers=headers,
                publish=lambda sock: self._publish(state, sock),
            )
        except (OSError, PreviewError) as exc:
            self.send(OP_ERR, stream, f"{exc}".encode())
            return
        state.writer = threading.Thread(
            target=self._ws_writer, args=(stream, state), daemon=True
        )
        state.writer.start()
        self.send(
            OP_WS_OK,
            stream,
            encode_meta({"subprotocol": answered.get("sec-websocket-protocol", "")}),
        )
        outcome = close_payload(1011, "upstream disconnected without close")
        try:
            while not state.cancelled.is_set():
                opcode, data = recv_message(
                    upstream,
                    control=lambda data, _op: self._enqueue_ws(stream, b"\x03" + data),
                )
                if opcode == _OP_CLOSE:
                    code, reason = parse_close(data)
                    outcome = close_payload(code, reason)
                    break
                kind = WS_TEXT if opcode == _OP_TEXT else WS_BINARY
                self.send(OP_WS_MSG, stream, bytes([kind]) + data)
        except (OSError, PreviewError):
            pass
        finally:
            if not state.cancelled.is_set():
                self.send(
                    OP_CLOSE,
                    stream,
                    outcome if "ws-close-v1" in self.capabilities else b"",
                )
            self._cancel(stream)

    def _ws_writer(self, stream: int, state: _StreamState) -> None:
        while not state.cancelled.is_set():
            try:
                payload = state.outbox.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._lock:
                state.queued_bytes -= len(payload)
            try:
                with state.write_lock:
                    opcode = (
                        _OP_CLOSE
                        if payload[0] == 2
                        else (
                            _OP_PONG
                            if payload[0] == 3
                            else (_OP_TEXT if payload[0] == WS_TEXT else _OP_BIN)
                        )
                    )
                    timer = threading.Timer(
                        _WRITE_TIMEOUT_S, lambda: self._cancel(stream)
                    )
                    timer.daemon = True
                    try:
                        timer.start()
                        send_frame(state.socket, payload[1:], opcode)
                    finally:
                        timer.cancel()
                if opcode == _OP_CLOSE:
                    self._cancel(stream)
                    return
            except OSError:
                self._cancel(stream)

    def _forward_ws(self, stream: int, payload: bytes) -> None:
        if not payload or payload[0] not in (WS_TEXT, WS_BINARY):
            self._cancel(stream)
            return
        self._enqueue_ws(stream, payload)

    def _enqueue_ws(self, stream: int, payload: bytes) -> None:
        overflow = False
        with self._lock:
            state = self._streams.get(stream)
            if (
                state is None
                or state.kind != OP_WS_OPEN
                or state.cancelled.is_set()
                or state.closing
            ):
                return
            if (
                len(payload) + state.queued_bytes > _MAX_OUTBOX_BYTES
                or state.outbox.full()
            ):
                overflow = True
            else:
                state.queued_bytes += len(payload)
                state.outbox.put_nowait(payload)
        if overflow:
            self._cancel(stream)

    def _close_all(self) -> None:
        with self._lock:
            states = list(self._streams.values())
        self._stop()
        deadline = time.monotonic() + _DRAIN_TIMEOUT_S
        for state in states:
            if (
                state.worker is not None
                and state.worker is not threading.current_thread()
            ):
                state.worker.join(timeout=max(0, deadline - time.monotonic()))
        if any(not state.done.is_set() for state in states):
            self.drained = False
            logger.error("preview workers did not stop before drain deadline")


# --- the process --------------------------------------------------------------

_RECONNECT_START_S = 1.0
_RECONNECT_CAP_S = 15.0


def run(
    url: str,
    tokens: TokenSource,
    ports: PortSource,
    *,
    ca_path: str | None = None,
    insecure: bool = False,
) -> int:
    """Hold the tunnel open, reconnecting while the backend is merely absent.

    A REFUSAL ends the process instead of looping: the only refusal this end can
    get is a token that no longer proves which topic it speaks for, and a helper
    that outlived its topic should go rather than knock forever. The launcher
    starts a fresh one on the next turn that wants a preview.

    Being SUPERSEDED ends it too, for the opposite reason: the backend has a
    newer helper for the same teammate, and redialling would take the tunnel
    back from the one that is supposed to have it.
    """
    delay = _RECONNECT_START_S
    while True:
        try:
            sock, answered = open_ws(
                f"{url}{'&' if '?' in url else '?'}token={tokens.get()}",
                ca_path=ca_path,
                insecure=insecure,
                idle_timeout=_IDLE_TIMEOUT_S,
                headers={CAPS_HEADER: ",".join(sorted(CAPABILITIES))},
            )
        except (OSError, PreviewError) as exc:
            retryable = isinstance(exc, OSError) or (
                isinstance(exc, PreviewError) and exc.retryable
            )
            if not retryable:
                logger.error("preview tunnel refused: %s", exc)
                return 1
            logger.warning(
                "preview tunnel unreachable (%s) — retrying in %.0fs", exc, delay
            )
            time.sleep(delay)
            delay = min(delay * 2, _RECONNECT_CAP_S)
            continue
        logger.info("preview tunnel up")
        delay = _RECONNECT_START_S
        session = Session(
            sock, ports, capabilities=answered.get(CAPS_HEADER, "").split(",")
        )
        session.serve()
        if not session.drained:
            logger.error("preview helper cannot redial with unfinished workers")
            return 1
        if session.superseded is not None:
            logger.info(
                "preview tunnel handed over, not redialling: %s", session.superseded
            )
            return 0
        time.sleep(_RECONNECT_START_S)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行环境预览 machine helper")
    parser.add_argument("--url", required=True, help="wss://…/preview/tunnel")
    parser.add_argument(
        "--token-file",
        default=os.environ.get("CHEESE_PREVIEW_TOKEN_FILE") or None,
        required=False,
        help="file holding the scoped cheese token, re-read per connection",
    )
    parser.add_argument(
        "--port-file",
        default=os.environ.get("CHEESE_PREVIEW_PORT_FILE") or None,
        required=False,
        help="file holding the loopback port `cheese serve` declared, re-read "
        "per stream. It is the ONLY address this process ever dials",
    )
    parser.add_argument("--ca", default=os.environ.get("CHEESE_PREVIEW_CA") or None)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args(argv)
    if not args.token_file or not args.port_file:
        print("--token-file and --port-file are both required", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    return run(
        args.url,
        TokenSource(args.token_file),
        PortSource(args.port_file),
        ca_path=args.ca,
        insecure=args.insecure,
    )


if __name__ == "__main__":
    raise SystemExit(main())
