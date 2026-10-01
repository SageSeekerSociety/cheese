"""The egress proxy private chats reach the network through.

A private chat's container sits on a Docker network with no route out
(``private.py``). The one thing it can reach there is this proxy, shared by
every private chat on the session host and attached to an ordinary network as
well. It speaks HTTP (``CONNECT`` included, so HTTPS) and SOCKS5 on one port.

For every connection it resolves the destination itself and decides with the
same rules ``/fetch`` uses (``addresses.py``): when the host's resolver answers
only with fake-IP placeholders the name is asked again over DNS over HTTPS, and
the connection is refused unless every address is public. It then connects to
an address it vetted, never to the name again, so a name cannot change its
answer between the check and the connection. The one internal destination it
allows is the platform endpoint the containers are given (``CHEESE_API``), and
only that exact ``host:port``.

Standard library only: this runs from the executor image.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import selectors
import socket
import struct
import sys
import threading
import urllib.parse
import urllib.request

try:
    from app.domain.fetch.addresses import (
        NotPublic,
        needs_real_addresses,
        over_https_answers,
        vetted,
    )
except ImportError:  # In the image, beside addresses.py.
    from addresses import (  # type: ignore[no-redef]
        NotPublic,
        needs_real_addresses,
        over_https_answers,
        vetted,
    )

PORT = 3128
#: Connections handled at once; more wait in the listen backlog.
CONNECTIONS = 256
HEAD_LIMIT = 64 * 1024


def _system_addresses(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise NotPublic(f"{host} does not resolve") from exc
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class Policy:
    """Where a connection may go: the public internet, and the platform."""

    def __init__(self, resolver: str, platform: tuple[str, int] | None = None):
        self.resolver = resolver
        self.platform = (platform[0].lower(), platform[1]) if platform else None

    def _over_https(self, host: str) -> list[str]:
        query = urllib.parse.urlencode({"name": host, "type": "A"})
        request = urllib.request.Request(
            f"{self.resolver}?{query}", headers={"accept": "application/dns-json"}
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=8) as response:
                return over_https_answers(json.load(response))
        except Exception as exc:  # noqa: BLE001 — unknown means refused
            raise NotPublic(f"{host} could not be resolved to a real address") from exc

    def addresses(self, host: str, port: int) -> list[str]:
        """The addresses a connection to ``host:port`` may be made to."""
        host = host.strip("[]")
        if self.platform == (host.lower(), port):
            return _system_addresses(host, port)
        addresses = _system_addresses(host, port)
        if needs_real_addresses(addresses):
            addresses = self._over_https(host)
        return vetted(host, addresses)


def _connect(addresses: list[str], port: int) -> socket.socket:
    error: OSError | None = None
    for address in addresses:
        try:
            upstream = socket.create_connection((address, port), timeout=10)
        except OSError as exc:
            error = exc
            continue
        upstream.settimeout(None)
        return upstream
    raise error or OSError("no address to connect to")


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    peers = {client: upstream, upstream: client}
    with selectors.DefaultSelector() as selector:
        for sock in peers:
            selector.register(sock, selectors.EVENT_READ)
        reading = set(peers)
        while reading:
            for key, _ in selector.select():
                source = key.fileobj
                assert isinstance(source, socket.socket)
                try:
                    data = source.recv(65536)
                except OSError:
                    data = b""
                if not data:
                    selector.unregister(source)
                    reading.discard(source)
                    try:
                        peers[source].shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                try:
                    peers[source].sendall(data)
                except OSError:
                    return


def _exactly(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("client went away")
        data += chunk
    return data


def _socks(client: socket.socket, policy: Policy) -> None:
    _, methods = _exactly(client, 2)
    _exactly(client, methods)
    client.sendall(b"\x05\x00")  # no authentication
    _, command, _, kind = _exactly(client, 4)
    if kind == 1:
        host = socket.inet_ntoa(_exactly(client, 4))
    elif kind == 3:
        host = _exactly(client, _exactly(client, 1)[0]).decode("idna")
    elif kind == 4:
        host = str(ipaddress.IPv6Address(_exactly(client, 16)))
    else:
        client.sendall(b"\x05\x08\x00\x01" + bytes(6))
        return
    (port,) = struct.unpack(">H", _exactly(client, 2))
    if command != 1:  # CONNECT only
        client.sendall(b"\x05\x07\x00\x01" + bytes(6))
        return
    try:
        upstream = _connect(policy.addresses(host, port), port)
    except NotPublic:
        client.sendall(b"\x05\x02\x00\x01" + bytes(6))  # not allowed by ruleset
        return
    except OSError:
        client.sendall(b"\x05\x05\x00\x01" + bytes(6))  # connection refused
        return
    with upstream:
        client.sendall(b"\x05\x00\x00\x01" + bytes(6))
        _relay(client, upstream)


def _refuse(client: socket.socket, status: str, reason: str) -> None:
    body = (reason + "\n").encode()
    client.sendall(
        f"HTTP/1.1 {status}\r\ncontent-type: text/plain\r\n"
        f"content-length: {len(body)}\r\nconnection: close\r\n\r\n".encode()
        + body
    )


def _http(client: socket.socket, policy: Policy) -> None:
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = client.recv(8192)
        if not chunk or len(head) > HEAD_LIMIT:
            return
        head += chunk
    head, rest = head.split(b"\r\n\r\n", 1)
    request_line, *headers = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = request_line.split(" ")
    except ValueError:
        return _refuse(client, "400 Bad Request", "malformed request line")
    if method == "CONNECT":
        host, _, port_text = target.rpartition(":")
        forward = None
    else:
        parts = urllib.parse.urlsplit(target)
        if parts.scheme != "http" or not parts.hostname:
            return _refuse(client, "400 Bad Request", "only http:// URLs and CONNECT")
        host, port_text = parts.hostname, str(parts.port or 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        kept = [h for h in headers if not h.lower().startswith("proxy-")]
        forward = "\r\n".join([f"{method} {path} {version}", *kept]).encode()
        forward += b"\r\n\r\n" + rest
    try:
        port = int(port_text)
        upstream = _connect(policy.addresses(host, port), port)
    except NotPublic as exc:
        return _refuse(client, "403 Forbidden", str(exc))
    except (OSError, ValueError) as exc:
        return _refuse(client, "502 Bad Gateway", str(exc))
    with upstream:
        if forward is None:
            client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
        else:
            upstream.sendall(forward)
        _relay(client, upstream)


def handle(client: socket.socket, policy: Policy) -> None:
    with client:
        try:
            first = client.recv(1, socket.MSG_PEEK)
            if first == b"\x05":
                _socks(client, policy)
            elif first:
                _http(client, policy)
        except (OSError, ValueError, UnicodeError):
            pass


def serve(listener: socket.socket, policy: Policy, clients=()) -> None:
    """Accept connections from ``clients`` (networks) for as long as it runs."""
    slots = threading.BoundedSemaphore(CONNECTIONS)

    def one(client):
        try:
            handle(client, policy)
        finally:
            slots.release()

    while True:
        client, peer = listener.accept()
        address = ipaddress.ip_address(peer[0])
        if clients and not any(address in network for network in clients):
            client.close()
            continue
        if not slots.acquire(timeout=30):
            client.close()
            continue
        try:
            threading.Thread(target=one, args=(client,), daemon=True).start()
        except RuntimeError as exc:  # out of threads: this one waits for a retry
            print(f"egress: {exc}", file=sys.stderr, flush=True)
            slots.release()
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolver", required=True)
    parser.add_argument("--platform", help="host:port of the platform endpoint")
    parser.add_argument("--client", action="append", default=[])
    args = parser.parse_args(argv)
    platform = None
    if args.platform:
        host, _, port = args.platform.rpartition(":")
        platform = (host.strip("[]"), int(port))
    listener = socket.create_server(("0.0.0.0", PORT), backlog=512)
    serve(
        listener,
        Policy(args.resolver, platform),
        [ipaddress.ip_network(c) for c in args.client],
    )


if __name__ == "__main__":
    main()
