#!/usr/bin/env python3
"""Give a program that only speaks HTTP to a URL the sandbox's platform socket.

A sandbox reaches the platform on `$CHEESE_PLATFORM_SOCKET`, a Unix socket the
connector serves outside it, and holds no credential of its own: the platform
knows whose a request is by which socket it arrived on. Most clients (the
review's open-code-review among them) take a base URL, not a socket path. This
listens on a loopback port and passes each model request (`/llm/...`) to the
socket, so the client is given `http://127.0.0.1:<port>/<prefix>` and reaches
nothing else the socket serves.

On a machine a person enrolled the sandbox shares the machine's network, so
the port is reachable by other programs there; the prefix is random per run
and a request without it is refused, which keeps it the caller's own. It
lives only as long as the program that started it.

Usage: platform_loopback.py   (prints the base URL on its first line, then
serves until its parent ends it). Standard library only.
"""

import os
import secrets
import socket
import sys
import threading

HEAD_LIMIT = 64 * 1024
#: A connection that has not sent its request head by then is dropped: until
#: the prefix is checked, anything on the machine could be holding it.
HEAD_TIMEOUT_S = float(os.environ.get("PLATFORM_LOOPBACK_HEAD_TIMEOUT_S", "10"))
#: What a client of this helper is for. The socket serves more (`/whoami`);
#: a holder of the prefix reaches only the model channel.
SERVED = b"/llm/"


def pipe(source, sink):
    try:
        while chunk := source.recv(65536):
            sink.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            sink.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def serve(client, prefix, platform):
    with client:
        head = b""
        client.settimeout(HEAD_TIMEOUT_S)
        try:
            while b"\r\n\r\n" not in head:
                chunk = client.recv(65536)
                if not chunk or len(head) > HEAD_LIMIT:
                    return
                head += chunk
        except OSError:
            return
        # A streamed answer may take minutes between bytes.
        client.settimeout(None)
        line, _, rest = head.partition(b"\r\n")
        parts = line.split(b" ")
        if len(parts) != 3 or not parts[1].startswith(prefix + SERVED):
            client.sendall(b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n")
            return
        parts[1] = parts[1][len(prefix) :]
        upstream = socket.socket(socket.AF_UNIX)
        try:
            upstream.connect(platform)
        except OSError:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\ncontent-length: 0\r\n\r\n")
            upstream.close()
            return
        with upstream:
            upstream.sendall(b" ".join(parts) + b"\r\n" + rest)
            back = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
            back.start()
            pipe(client, upstream)
            back.join()


def main():
    platform = os.environ.get("CHEESE_PLATFORM_SOCKET", "")
    if not platform:
        sys.exit("CHEESE_PLATFORM_SOCKET is not set; run inside a task sandbox")
    prefix = b"/" + secrets.token_urlsafe(24).encode()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    print(f"http://127.0.0.1:{listener.getsockname()[1]}{prefix.decode()}", flush=True)
    while True:
        client, _ = listener.accept()
        threading.Thread(
            target=serve, args=(client, prefix, platform), daemon=True
        ).start()


if __name__ == "__main__":
    main()
