#!/usr/bin/env python3
"""The public names' TLS ends at the box's front door, behind PROXY protocol.

A request that arrives the way the Hong Kong relay forwards it — a PROXY
protocol header, then TLS — reaches the application with the client address
from that header, a request for an alias name is sent to the canonical one,
and a box without a certificate gets no TLS listener at all.
"""

import json
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX = sys.argv[1] if len(sys.argv) > 1 else shutil.which("nginx") or "nginx"
CLIENT = "203.0.113.7"


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(dict(self.headers)).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def configure(active: Path, plain_port: int) -> str:
    subprocess.run(
        ["bash", str(ROOT / "deploy/llm-tunnel/configure-frontend.sh"), str(active), "18086", str(plain_port)],
        check=True,
    )
    return (active / "sites-frontend.conf").read_text()


def request_through_relay(port: int, host: str = "okcheese.com", path: str = "/") -> tuple[bytes, bytes]:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        raw.sendall(f"PROXY TCP4 {CLIENT} 127.0.0.1 40000 443\r\n".encode())
        with context.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            response = b""
            while chunk := tls.recv(65536):
                response += chunk
    head, _, body = response.partition(b"\r\n\r\n")
    return head, body


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        active = root / "active"
        active.mkdir()

        assert "ssl" not in configure(active, free_port()), "TLS listener without a certificate"

        (active / "tls").mkdir()
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=okcheese.com", "-keyout", str(active / "tls/privkey.pem"),
             "-out", str(active / "tls/fullchain.pem")],
            check=True, capture_output=True,
        )
        app = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        plain, tls = free_port(), free_port()
        site = configure(active, plain)
        site = (site.replace("127.0.0.1:18086", f"127.0.0.1:{app.server_port}")
                    .replace("127.0.0.1:18443", f"127.0.0.1:{tls}")
                    .replace("/etc/nginx/active", str(active)))
        (active / "sites-frontend.conf").write_text(site)
        temp_paths = "".join(
            f"  {kind}_temp_path {root}/{kind};\n"
            for kind in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
        )
        config = root / "nginx.conf"
        config.write_text(
            f"pid {root}/nginx.pid;\nerror_log {root}/error.log;\nevents {{}}\nhttp {{\n"
            f"  access_log off;\n{temp_paths}"
            "  map $http_upgrade $connection_upgrade { default upgrade; \"\" close; }\n"
            f"  include {active}/sites-frontend.conf;\n}}\n"
        )
        command = [NGINX, "-e", "stderr", "-p", f"{root}/", "-c", str(config)]
        subprocess.run([*command, "-t"], check=True)
        subprocess.run(command, check=True)
        try:
            head, body = request_through_relay(tls)
            assert head.startswith(b"HTTP/1.1 200"), head
            assert b"strict-transport-security" in head.lower(), head
            headers = {k.lower(): v for k, v in json.loads(body).items()}
            forwarded = [part.strip() for part in headers["x-forwarded-for"].split(",")]
            assert forwarded[0] == CLIENT, headers
            assert headers["host"] == "okcheese.com", headers

            for alias in ("www.okcheese.com", "hk.okcheese.com", "WWW.okcheese.com"):
                head, _ = request_through_relay(tls, alias, "/projects/1?tab=site")
                assert head.startswith(b"HTTP/1.1 301"), head
                assert b"\r\nlocation: https://okcheese.com/projects/1?tab=site" in head.lower(), head
        finally:
            subprocess.run([*command, "-s", "stop"], check=False)
            app.shutdown()
    print("front door TLS: ok")


if __name__ == "__main__":
    main()
