"""Check the CONNECT listener rejects an unauthenticated CONNECT, with no
upstream call."""

import socket


def check(connect_port: int = 8444) -> None:
    with socket.create_connection(("127.0.0.1", connect_port), timeout=2) as connection:
        connection.sendall(
            b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n"
            b"Host: api.anthropic.com:443\r\n\r\n"
        )
        response = connection.makefile("rb").readline(4096)
        if response.split()[:2] != [b"HTTP/1.1", b"407"]:
            raise RuntimeError(
                "CONNECT listener did not reject an unauthenticated request"
            )


if __name__ == "__main__":
    check()
