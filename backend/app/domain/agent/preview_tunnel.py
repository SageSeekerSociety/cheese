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
    sock: socket.socket, write_lock: threading.Lock | None = None
) -> tuple[int, bytes] | None:
    """The next data message as ``(opcode, payload)``, reassembled; None when the
    peer closed. Fragments and control frames are handled here rather than by the
    caller: a peer may split a message across continuations and interleave a ping
    at any point, and treating either as data corrupts the stream.

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
            return None
        if opcode == _OP_PING:
            if write_lock is None:
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

    def get(self) -> tuple[int, str]:
        try:
            with open(self._path) as handle:
                raw, _, base = handle.read().strip().partition("\n")
        except OSError as exc:
            raise PreviewError(f"no preview port declared: {exc}") from exc
        if not raw.isdigit() or not (1 <= int(raw) <= 65535):
            raise PreviewError(f"{self._path} does not name a port")
        if base and (
            not base.startswith("/")
            or base.startswith("//")
            or any(c in base for c in "\r\n?#")
        ):
            raise PreviewError(f"{self._path} does not name a local mount")
        return int(raw), base.rstrip("/")


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


class Session:
    """One WebSocket to the backend, and the streams multiplexed over it."""

    def __init__(self, sock: socket.socket, ports: PortSource) -> None:
        self._sock = sock
        self._ports = ports
        self._write_lock = threading.Lock()
        # Each proxied WebSocket to the app is written by two threads — the one
        # relaying the browser's messages, and the one reading the app's, which
        # writes when it answers a ping. Its own lock keeps those two apart.
        self._ws_streams: dict[int, tuple[socket.socket, threading.Lock]] = {}
        self._lock = threading.Lock()

    def send(self, op: int, stream: int, payload: bytes = b"") -> None:
        """One frame to the backend, or nothing at all if the tunnel has gone.

        A dead tunnel is not this caller's problem to handle: every stream is
        writing into the same socket from its own thread, the reader is already
        tearing the session down, and there is nowhere for the news to go. Raising
        here only turned into unhandled exceptions on threads nobody watches —
        one per stream still in flight when a connection dropped.
        """
        try:
            with self._write_lock:
                send_frame(self._sock, encode(op, stream, payload))
        except OSError:
            return

    def serve(self) -> None:
        """Read frames until the backend goes away.

        Losing the connection RETURNS rather than raises: it is the normal end of
        a session (a redeploy, a laptop's wifi), the caller's only answer is to
        dial again, and an exception escaping here would be an unhandled one on
        a thread nobody is watching.
        """
        stop = threading.Event()
        threading.Thread(target=self._keepalive, args=(stop,), daemon=True).start()
        try:
            while True:
                message = recv_message(self._sock, self._write_lock)
                if message is None:
                    return
                _, data = message
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

    def _keepalive(self, stop: threading.Event) -> None:
        """Ping until the session ends — see _PING_INTERVAL_S."""
        while not stop.wait(_PING_INTERVAL_S):
            try:
                with self._write_lock:
                    send_frame(self._sock, b"", _OP_PING)
            except OSError:
                return

    def _dispatch(self, op: int, stream: int, payload: bytes) -> None:
        if op == OP_REQ:
            threading.Thread(
                target=self._serve_request, args=(stream, payload), daemon=True
            ).start()
        elif op == OP_WS_OPEN:
            threading.Thread(
                target=self._serve_ws, args=(stream, payload), daemon=True
            ).start()
        elif op == OP_WS_MSG:
            self._forward_ws(stream, payload)
        elif op == OP_CLOSE:
            self._drop_ws(stream)

    # -- HTTP ------------------------------------------------------------------

    def _serve_request(self, stream: int, payload: bytes) -> None:
        try:
            meta, body = decode_meta(payload)
        except ValueError as exc:
            self.send(OP_ERR, stream, str(exc).encode())
            return
        try:
            port, base = self._ports.get()
        except PreviewError as exc:
            self.send(OP_ERR, stream, str(exc).encode())
            return
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_REQUEST_TIMEOUT_S)
        try:
            headers = {k: v for k, v in meta.get("headers") or []}
            conn.request(
                meta.get("method", "GET"), base + meta.get("path", "/"), body, headers
            )
            response = conn.getresponse()
            self.send(
                OP_RESP,
                stream,
                encode_meta(
                    {
                        "status": response.status,
                        # ``http.client`` has already undone chunking and any
                        # content-encoding negotiation we asked for, so the
                        # framing headers describe a body that no longer exists.
                        # The backend re-derives them from what it actually sends.
                        "headers": [
                            [k, v]
                            for k, v in response.getheaders()
                            if k.lower() not in ("transfer-encoding", "content-length")
                        ],
                    }
                ),
            )
            sent = 0
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                sent += len(chunk)
                if sent > _MAX_BODY:
                    self.send(OP_ERR, stream, b"the response is too large to preview")
                    return
                self.send(OP_DATA, stream, chunk)
            self.send(OP_END, stream)
        except Exception as exc:  # noqa: BLE001 — see below
            # Everything, deliberately. The app is whatever the agent started, so
            # what comes back is not a shape this end can enumerate: a refused
            # connection, a truncated body, a status line that is not one. Every
            # such case has the same right answer — say so ON THE STREAM — and the
            # alternative is a thread dying quietly while the browser waits out a
            # timeout with nothing anywhere naming the cause.
            self.send(OP_ERR, stream, f"{exc.__class__.__name__}: {exc}".encode())
        finally:
            conn.close()

    # -- WebSocket (a dev server's HMR socket, mostly) --------------------------

    def _serve_ws(self, stream: int, payload: bytes) -> None:
        try:
            meta, _ = decode_meta(payload)
            port, base = self._ports.get()
        except (ValueError, PreviewError) as exc:
            self.send(OP_ERR, stream, str(exc).encode())
            return
        headers = {k: v for k, v in meta.get("headers") or []}
        try:
            upstream, answered = open_ws(
                f"ws://127.0.0.1:{port}{base}{meta.get('path', '/')}", headers=headers
            )
        except (OSError, PreviewError) as exc:
            self.send(OP_ERR, stream, f"{exc}".encode())
            return
        upstream_lock = threading.Lock()
        with self._lock:
            self._ws_streams[stream] = (upstream, upstream_lock)
        self.send(
            OP_WS_OK,
            stream,
            encode_meta({"subprotocol": answered.get("sec-websocket-protocol", "")}),
        )
        try:
            while True:
                message = recv_message(upstream, upstream_lock)
                if message is None:
                    break
                opcode, data = message
                kind = WS_TEXT if opcode == _OP_TEXT else WS_BINARY
                self.send(OP_WS_MSG, stream, bytes([kind]) + data)
        except (OSError, PreviewError):
            pass
        finally:
            self._drop_ws(stream)
            self.send(OP_CLOSE, stream)

    def _forward_ws(self, stream: int, payload: bytes) -> None:
        with self._lock:
            entry = self._ws_streams.get(stream)
        if entry is None or not payload:
            return
        upstream, upstream_lock = entry
        opcode = _OP_TEXT if payload[0] == WS_TEXT else _OP_BIN
        try:
            with upstream_lock:
                send_frame(upstream, payload[1:], opcode)
        except OSError:
            self._drop_ws(stream)

    def _drop_ws(self, stream: int) -> None:
        with self._lock:
            entry = self._ws_streams.pop(stream, None)
        if entry is not None:
            try:
                entry[0].close()
            except OSError:
                pass

    def _close_all(self) -> None:
        with self._lock:
            streams = list(self._ws_streams)
        for stream in streams:
            self._drop_ws(stream)
        try:
            self._sock.close()
        except OSError:
            pass


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
    """
    delay = _RECONNECT_START_S
    while True:
        try:
            sock, _ = open_ws(
                f"{url}{'&' if '?' in url else '?'}token={tokens.get()}",
                ca_path=ca_path,
                insecure=insecure,
                idle_timeout=_IDLE_TIMEOUT_S,
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
        Session(sock, ports).serve()
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
