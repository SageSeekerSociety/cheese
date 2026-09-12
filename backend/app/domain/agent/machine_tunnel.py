"""The machine half of the tunnel: a local CONNECT proxy that rides a WebSocket.

SHIPPED TO A MACHINE AND RUN THERE. Two rules follow from that and neither is
negotiable: **stdlib only** (a MicroCloud machine has python3 and no venv, and
`pip install` on a box the platform does not own is not a deployment step), and
**no imports from `app.*`** (there is no backend package on that machine). It
lives in the repo as a real module anyway, so it is linted and unit-tested like
everything else; the launcher ships it by reading this file's own bytes.

Why it exists. A remote machine's Claude Code can only be steered to the meter
by `HTTPS_PROXY` — rewriting /etc/hosts needs root it does not have, and
pointing `ANTHROPIC_BASE_URL` at an HTTP endpoint flips the CLI into API-key
mode where the OAuth token is ignored, so no subscription turn is possible at
all. `HTTPS_PROXY` needs something that speaks CONNECT, and measured 2026-08-14
the machine cannot open a TCP connection to the meter's listener: packets to the
box's 8444 are dropped before its NIC, at a hypervisor bridge the box cannot
see or change.

What the machine can reach is the gateway that already carries the connector.
An L7 proxy will not forward CONNECT, but it forwards a WebSocket — so:

    claude ──CONNECT──► this, on 127.0.0.1 ──WebSocket──► gateway ──► backend ──► meter

This end speaks the minimum WebSocket a client needs (RFC 6455: masked frames
out, fragments and control frames handled in), and otherwise moves bytes. It
does NOT answer the CONNECT itself — the meter does, through the pipe, so the
meter's own auth and host allowlist still decide every request exactly as they
would for a caller that reached it directly.
"""

import argparse
import base64
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

logger = logging.getLogger("cheese.tunnel")

_OP_CONT = 0x0
_OP_BIN = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

# One read of the local socket. Matches the backend side; a turn's frames are
# small and the pipe is latency-sensitive.
_CHUNK = 65536
_HANDSHAKE_TIMEOUT_S = 15.0


class TunnelError(RuntimeError):
    """The WebSocket could not be established. Carries a reason the caller can
    log — a machine failing here has no other way to say why. ``retryable``
    marks ABSENCE (the gateway/backend was not there to answer: 502/503/504,
    a connection cut mid-upgrade) as opposed to REFUSAL (it answered and said
    no: bad token, missing route) — only absence is worth waiting out."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _is_retryable(exc: BaseException) -> bool:
    """Whether waiting and trying again can possibly change the outcome.
    Network-level failures always can (the other end may come back); a
    TunnelError only when its raise site marked it so."""
    if isinstance(exc, TunnelError):
        return exc.retryable
    return isinstance(exc, OSError)


# How long a CONNECT client is held while the gateway/backend is unreachable —
# a deploy swaps the backend container in well under this, so the swap reads
# as one slow request instead of ECONNREFUSED ("Unable to connect to API" on
# the claude side, measured 2026-08-17 across 7 deploys). Bounded on purpose:
# past the window we still close, because a claude waiting forever on a
# CONNECT looks like a stalled model — the most expensive failure to diagnose.
_OPEN_RETRY_WINDOW_S = 60.0
_OPEN_RETRY_START_S = 1.0
_OPEN_RETRY_CAP_S = 8.0


def _read_exact(sock: socket.socket, count: int) -> bytes:
    """Exactly ``count`` bytes, or raise. TCP splits wherever it likes, and a
    short read misparsed as a frame header desynchronises the stream for good."""
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise TunnelError("the tunnel closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(sock: socket.socket, payload: bytes, opcode: int = _OP_BIN) -> None:
    """One FIN frame, masked. A client MUST mask (RFC 6455 §5.3) — an unmasked
    client frame is a protocol error the server is required to reject, which
    reads as a mysterious disconnect rather than as a bug here."""
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
    # The pipe sends up to 64 KiB per frame; keep masking out of a Python byte loop.
    repeated_mask = (mask * ((length + 3) // 4))[:length]
    masked = (
        int.from_bytes(payload, "big") ^ int.from_bytes(repeated_mask, "big")
    ).to_bytes(length, "big")
    sock.sendall(bytes(header) + masked)


def recv_message(sock: socket.socket) -> bytes | None:
    """The next data message, reassembled. None when the peer closed.

    Fragments and control frames are handled here rather than by the caller:
    a server may split a large body across continuation frames, and it may
    interleave a ping at any point — treating either as data corrupts the
    stream, and the corruption surfaces far away as a broken model response.
    """
    payload = bytearray()
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
        # A server must NOT mask; if one does, honour it rather than corrupting.
        mask = _read_exact(sock, 4) if masked else b""
        data = _read_exact(sock, length) if length else b""
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

        if opcode == _OP_CLOSE:
            return None
        if opcode == _OP_PING:
            send_frame(sock, data, _OP_PONG)
            continue
        if opcode == _OP_PONG:
            continue

        payload.extend(data)
        if fin:
            return bytes(payload)


def open_tunnel(
    url: str, token: str, *, ca_path: str | None = None, insecure: bool = False
) -> socket.socket:
    """A connected, upgraded WebSocket to the backend's tunnel endpoint."""
    parsed = urlparse(url)
    secure = parsed.scheme in ("wss", "https")
    host = parsed.hostname or ""
    port = parsed.port or (443 if secure else 80)
    path = parsed.path or "/"
    query = f"token={token}"
    if parsed.query:
        query = f"{parsed.query}&{query}"

    raw = socket.create_connection((host, port), timeout=_HANDSHAKE_TIMEOUT_S)
    # Separate TLS records must not wait for the preceding packet's delayed ACK.
    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if secure:
        if insecure:
            context = ssl._create_unverified_context()  # noqa: S323 — opt-in only
        else:
            context = ssl.create_default_context(cafile=ca_path or None)
        raw = context.wrap_socket(raw, server_hostname=host)

    key = base64.b64encode(secrets.token_bytes(16)).decode()
    request = (
        f"GET {path}?{query} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    raw.sendall(request.encode())

    header = bytearray()
    while b"\r\n\r\n" not in header:
        chunk = raw.recv(1)
        if not chunk:
            raw.close()
            # A cut mid-upgrade is the deploy window's shape, not a verdict.
            raise TunnelError(
                "the gateway closed the connection during the upgrade",
                retryable=True,
            )
        header.extend(chunk)
    status = header.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 101" not in status:
        raw.close()
        # The status line is the whole diagnosis: 403 = bad scoped token, 404 =
        # backend without this route, 502 = gateway cannot reach the backend.
        # Only the gateway-cannot-reach-it family is absence worth waiting out.
        raise TunnelError(
            f"upgrade refused: {status}",
            retryable=any(f" {code} " in status for code in (502, 503, 504)),
        )
    raw.settimeout(None)
    return raw


_HEAD_END = b"\r\n\r\n"
_MAX_HEAD = 16384


def _read_request_head(sock: socket.socket) -> bytes:
    """The client's first request head, up to and including the blank line.

    Bounded: a peer that never sends the terminator would otherwise buy an
    unbounded buffer on a machine we do not own."""
    buffer = bytearray()
    while _HEAD_END not in buffer:
        if len(buffer) > _MAX_HEAD:
            raise TunnelError("request head too large")
        chunk = sock.recv(4096)
        if not chunk:
            raise TunnelError("client closed before sending a request")
        buffer.extend(chunk)
    return bytes(buffer)


def _with_proxy_auth(head: bytes, token: str) -> bytes:
    """`head` with a Proxy-Authorization carrying the scoped token.

    Replaces any existing one rather than appending: two of them is a malformed
    request, and the client's own (there is none today) would not be the one the
    meter accepts anyway.
    """
    credential = base64.b64encode(f"cheese:{token}".encode()).decode()
    lines = [
        line
        for line in head.split(b"\r\n")
        if not line.lower().startswith(b"proxy-authorization:")
    ]
    # Insert after the request line so the head stays readable in a capture.
    lines.insert(1, f"Proxy-Authorization: Basic {credential}".encode())
    return b"\r\n".join(lines)


def _pump_local_to_ws(local: socket.socket, ws: socket.socket) -> None:
    try:
        while True:
            data = local.recv(_CHUNK)
            if not data:
                return
            send_frame(ws, data)
    except OSError:
        return


def _pump_ws_to_local(ws: socket.socket, local: socket.socket) -> None:
    try:
        while True:
            message = recv_message(ws)
            if message is None:
                return
            local.sendall(message)
    except (OSError, TunnelError):
        return


class TokenSource:
    """Where the scoped token comes from, read fresh for EVERY connection.

    Deliberately not a value captured at startup. A scoped token has a session
    lifetime, this helper outlives one, and `claude` reads its own credential
    exactly once at launch — so a token baked into a long-running process is the
    shape of #385: the process keeps running, the credential dies under it, and
    every turn afterwards is refused by the meter with nothing to relaunch,
    because the helper looks perfectly healthy. Reading per connection means the
    launcher can drop a fresh token in place and the next turn just works.
    """

    def __init__(self, value: str = "", path: str | None = None) -> None:
        self._value = value
        self._path = path

    def get(self) -> str:
        if self._path:
            try:
                with open(self._path) as handle:
                    return handle.read().strip()
            except OSError as exc:
                raise TunnelError(f"could not read {self._path}: {exc}") from exc
        return self._value


def _open_with_patience(
    url: str,
    token: "str | TokenSource",
    *,
    ca_path: str | None = None,
    insecure: bool = False,
    window_s: float = _OPEN_RETRY_WINDOW_S,
    start_delay_s: float = _OPEN_RETRY_START_S,
) -> "tuple[socket.socket, str]":
    """Open the WebSocket, riding out a restarting backend (#551 止血).

    A deploy swaps the backend container for tens of seconds; a CONNECT
    arriving in that window used to be answered with a closed socket, which
    claude renders as "Unable to connect to API". Absence (connection refused,
    502 from the gateway, a cut mid-upgrade) is retried with backoff inside a
    bounded window while the client is held; refusal (bad token, missing
    route) still fails on the first attempt. The token is re-resolved per
    attempt so a launcher-refreshed token takes effect mid-window."""
    deadline = time.monotonic() + window_s
    delay = start_delay_s
    attempt = 0
    while True:
        attempt += 1
        try:
            secret = token.get() if isinstance(token, TokenSource) else token
            if not secret:
                raise TunnelError("no scoped token available")
            return (
                open_tunnel(url, secret, ca_path=ca_path, insecure=insecure),
                secret,
            )
        except (OSError, TunnelError) as exc:
            if not _is_retryable(exc) or time.monotonic() + delay > deadline:
                raise
            logger.warning(
                "tunnel open failed (attempt %d: %s) — holding the client, "
                "retrying in %.1fs",
                attempt,
                exc,
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, _OPEN_RETRY_CAP_S)


def handle_connection(
    local: socket.socket,
    url: str,
    token: "str | TokenSource",
    *,
    ca_path: str | None = None,
    insecure: bool = False,
) -> None:
    """One accepted CONNECT client gets one WebSocket, for its whole life.

    Per connection, not pooled: the meter's listener treats each CONNECT as its
    own tunnel to one upstream host, so sharing a socket between two of them
    would interleave two unrelated TLS streams into one.
    """
    ws = None
    try:
        ws, secret = _open_with_patience(url, token, ca_path=ca_path, insecure=insecure)
    except (OSError, TunnelError) as exc:
        logger.warning("tunnel could not be opened: %s", exc)
        # Close rather than hang: `claude` waiting on a CONNECT that will never
        # be answered looks like a stalled model, which is the one failure mode
        # that costs an operator the most time to diagnose. (Absence — a
        # restarting backend — was already waited out above, bounded.)
        local.close()
        return

    # Stamp the scoped token onto the CONNECT before anything else crosses.
    #
    # HTTPS_PROXY is a bare loopback URL with no userinfo, deliberately: a
    # credential baked into it is read once by claude at startup and cannot be
    # refreshed under a running process (#385). But the meter still demands a
    # scoped token as the proxy password and answers 407 without one — measured
    # 2026-08-14, a turn died with exactly that. The token has to enter the
    # stream somewhere, and this is the only place that holds it AND sees the
    # request: read per connection from a file the launcher rewrites.
    try:
        head = _read_request_head(local)
    except (OSError, TunnelError) as exc:
        logger.warning("could not read the CONNECT request: %s", exc)
        local.close()
        ws.close()
        return
    send_frame(ws, _with_proxy_auth(head, secret))

    up = threading.Thread(target=_pump_local_to_ws, args=(local, ws), daemon=True)
    up.start()
    _pump_ws_to_local(ws, local)
    # Either direction ending ends the pair — a half-open pipe leaves the caller
    # waiting on a response that can no longer arrive.
    for sock in (local, ws):
        try:
            sock.close()
        except OSError:
            pass
    up.join(timeout=1)


def serve(
    listen_port: int,
    url: str,
    token: "str | TokenSource",
    *,
    ca_path: str | None = None,
    insecure: bool = False,
    listen_host: str = "127.0.0.1",
) -> None:
    """Accept CONNECT clients forever. Loopback only by default: this endpoint
    carries a scoped token in its own configuration, so anything that can reach
    it can spend the project's budget — and on a shared machine that must mean
    only processes already running as this user."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((listen_host, listen_port))
    server.listen(64)
    logger.info("tunnel listening on %s:%s → %s", listen_host, listen_port, url)
    while True:
        client, _ = server.accept()
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(
            target=handle_connection,
            args=(client, url, token),
            kwargs={"ca_path": ca_path, "insecure": insecure},
            daemon=True,
        ).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--url", required=True, help="wss://…/llm/tunnel")
    parser.add_argument(
        "--token-file",
        default=os.environ.get("CHEESE_TUNNEL_TOKEN_FILE") or None,
        help="file holding the scoped cheese token, re-read per connection. "
        "PREFERRED: it keeps the token out of the machine's process list, and "
        "lets a refreshed token take effect without restarting this helper",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("CHEESE_TUNNEL_TOKEN", ""),
        help="the token inline; only for a one-off run, since it can never be "
        "refreshed under a running process",
    )
    parser.add_argument("--ca", default=os.environ.get("CHEESE_TUNNEL_CA") or None)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args(argv)
    if not (args.token_file or args.token):
        print("a scoped cheese token is required", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    source = TokenSource(value=args.token, path=args.token_file)
    serve(args.port, args.url, source, ca_path=args.ca, insecure=args.insecure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
