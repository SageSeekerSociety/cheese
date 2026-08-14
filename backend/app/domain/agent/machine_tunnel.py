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
    log — a machine failing here has no other way to say why."""


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
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
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
            raise TunnelError("the gateway closed the connection during the upgrade")
        header.extend(chunk)
    status = header.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 101" not in status:
        raw.close()
        # The status line is the whole diagnosis: 403 = bad scoped token, 404 =
        # backend without this route, 502 = gateway cannot reach the backend.
        raise TunnelError(f"upgrade refused: {status}")
    raw.settimeout(None)
    return raw


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


def handle_connection(
    local: socket.socket,
    url: str,
    token: str,
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
        ws = open_tunnel(url, token, ca_path=ca_path, insecure=insecure)
    except (OSError, TunnelError) as exc:
        logger.warning("tunnel could not be opened: %s", exc)
        # Close rather than hang: `claude` waiting on a CONNECT that will never
        # be answered looks like a stalled model, which is the one failure mode
        # that costs an operator the most time to diagnose.
        local.close()
        return

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
    token: str,
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
        "--token",
        default=os.environ.get("CHEESE_TUNNEL_TOKEN", ""),
        help="scoped cheese token; prefer CHEESE_TUNNEL_TOKEN so it stays out "
        "of the machine's process list",
    )
    parser.add_argument("--ca", default=os.environ.get("CHEESE_TUNNEL_CA") or None)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args(argv)
    if not args.token:
        print("a scoped cheese token is required", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    serve(args.port, args.url, args.token, ca_path=args.ca, insecure=args.insecure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
