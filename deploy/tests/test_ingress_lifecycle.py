#!/usr/bin/env python3
"""Exercise real nginx switches while established WebSockets remain usable."""

import base64
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX = [sys.argv[1] if len(sys.argv) > 1 else shutil.which("nginx") or "nginx"]
REQUEST_STARTED = threading.Event()


class Endpoint(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            REQUEST_STARTED.set()
            time.sleep(2)
        if self.headers.get("Upgrade", "").lower() == "websocket":
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            while header := self.rfile.read(2):
                length = header[1] & 127
                mask = self.rfile.read(4)
                data = self.rfile.read(length)
                payload = bytes(value ^ mask[i % 4] for i, value in enumerate(data))
                self.wfile.write(bytes([0x81, len(payload)]) + payload)
                self.wfile.flush()
            return
        body = json.dumps({"name": self.server.name, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def connect(port, path, host="app.example.test"):
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                  "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                  "Sec-WebSocket-Version: 13\r\n"
                  "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n").encode())
    response = b""
    while not response.endswith(b"\r\n\r\n"):
        part = sock.recv(1)
        assert part, response
        response += part
    assert b" 101 " in response.split(b"\r\n", 1)[0], response
    return sock


def echo(sock):
    payload = b"still-connected"
    mask = b"abcd"
    sock.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + bytes(
        value ^ mask[i % 4] for i, value in enumerate(payload)
    ))
    expected = bytes([0x81, len(payload)]) + payload
    response = b""
    while len(response) < len(expected):
        part = sock.recv(len(expected) - len(response))
        assert part, "established WebSocket was closed during application reload"
        response += part
    assert response == expected, response


def main():
    scratch = ROOT / ".tmp"
    scratch.mkdir(exist_ok=True)
    servers, sockets, commands = [], [], []
    with tempfile.TemporaryDirectory(prefix="ingress-lifecycle-", dir=scratch) as temporary:
        root = Path(temporary)
        active = root / "active"
        active.mkdir()
        (root / "logs").mkdir()
        for name in ("old", "new", "owner", "preview"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
            server.name = name
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        old, new, owner, preview = servers
        api, public, app_api, app_frontend = [free_port() for _ in range(4)]
        # The preview content host: preview-<32hex>.<domain>, and the same with a
        # -<22hex> resource suffix. Project hosts keep the plain form.
        preview_host = "preview-0123456789abcdef0123456789abcdef.example.net"
        preview_resource_host = (
            "preview-0123456789abcdef0123456789abcdef-0123456789abcdef012345.example.net"
        )
        replacements = {
            "/etc/nginx/active": str(active), "listen 8081;": f"listen 127.0.0.1:{api};",
            "127.0.0.1:18085": f"127.0.0.1:{app_api}",
            "127.0.0.1:18086": f"127.0.0.1:{app_frontend}",
            "127.0.0.1:18083": f"127.0.0.1:{owner.server_port}",
            "127.0.0.1:8091": f"127.0.0.1:{owner.server_port}",
            "127.0.0.1:8093": f"127.0.0.1:{owner.server_port}",
        }

        # The box's own frontend ports live in frontend.conf, which every
        # app-router.conf includes, and follow every frontend switch.
        box_port, box_direct = free_port(), free_port()

        def configure(target):
            (active / "backend.conf").write_text(
                f"upstream backend_active {{ server 127.0.0.1:{target.server_port}; }}\n"
            )
            subprocess.run(["bash", str(ROOT / "deploy/llm-tunnel/configure-frontend-upstream.sh"),
                            str(active), str(target.server_port), str(box_port),
                            f"127.0.0.1:{box_direct}"], check=True)

        configure(old)
        # The front door carries the preview tunnel to the owner too (its 4th
        # argument is the owner's port), and machine tunnel traffic must survive
        # the rolling frontend/backend switches below.
        subprocess.run(["bash", str(ROOT / "deploy/llm-tunnel/configure-frontend.sh"),
                        str(active), "18086", str(public), str(preview.server_port)], check=True)
        frontend = active / "sites-frontend.conf"
        content = frontend.read_text()
        for source, target in replacements.items():
            content = content.replace(source, target)
        frontend.write_text(content)
        # api-front itself routes the preview tunnel through the map written by
        # configure-preview.sh, and splits preview content hosts inside the
        # content-domain wildcard server.
        subprocess.run(["bash", str(ROOT / "deploy/llm-tunnel/configure-preview.sh"),
                        "owner", str(active), str(preview.server_port)], check=True)
        subprocess.run(["bash", str(ROOT / "deploy/llm-tunnel/configure-sites.sh"),
                        "example.net", str(active), str(preview.server_port)], check=True)
        sites = active / "sites.conf"
        sites.write_text(sites.read_text()
                         .replace("listen 8081;", f"listen 127.0.0.1:{api};")
                         .replace("127.0.0.1:18085", f"127.0.0.1:{app_api}"))
        try:
            for name in ("app-router", "nginx"):
                content = (ROOT / f"deploy/llm-tunnel/{name}.conf").read_text()
                for source, target in replacements.items():
                    content = content.replace(source, target)
                temporary_paths = "\n".join(
                    f"  {kind}_temp_path {root}/{name}-{kind};"
                    for kind in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
                )
                content = content.replace("http {", f"http {{\n  access_log off;\n{temporary_paths}")
                config = root / f"{name}.conf"
                config.write_text(f"pid {root}/{name}.pid;\nerror_log {root}/{name}.log;\n" + content)
                command = [*NGINX, "-e", "stderr", "-p", str(root) + "/", "-c", str(config)]
                subprocess.run([*command, "-t"], check=True)
                subprocess.run(command, check=True)
                commands.append(command)
            for port, paths in (
                (api, ["/connector/agent", "/api/connector/agent", "/connector/session/s/screen", "/api/connector/session/s/screen", "/llm/tunnel", "/preview/tunnel", "/api/preview/tunnel"]),
                # A connector connected with <site>/connector dials the
                # unprefixed channel; the desktop app does exactly that.
                (public, ["/connector/agent", "/api/connector/agent", "/connector/session/s/screen", "/api/connector/session/s/screen", "/api/llm/tunnel", "/api/forge/events/ws", "/preview/tunnel", "/api/preview/tunnel"]),
            ):
                for path in paths:
                    sockets.append(connect(port, path))
            # A preview content host dials api-front directly (the Cloudflare
            # tunnel lands on :8081); its content WebSocket must ride the owner
            # too, both with and without the resource suffix.
            sockets.append(connect(api, "/", host=preview_host))
            sockets.append(connect(api, "/assets/app.js", host=preview_resource_host))
            for sock in sockets:
                echo(sock)
            for target in (new, old):
                def slow_request():
                    with urllib.request.urlopen(f"http://127.0.0.1:{api}/slow", timeout=5) as response:
                        return json.load(response)

                REQUEST_STARTED.clear()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    request = pool.submit(slow_request)
                    assert REQUEST_STARTED.wait(3), "old upstream did not receive request"
                    configure(target)
                    subprocess.run([*commands[0], "-s", "reload"], check=True)
                    previous = old if target is new else new
                    assert request.result()["name"] == previous.name
                # Existing workers have a real 30-second shutdown deadline.
                # Wait beyond it before accepting connection survival.
                deadline = time.monotonic() + 32
                while time.monotonic() < deadline:
                    for sock in sockets:
                        echo(sock)
                    time.sleep(0.25)
                for port in (api, public, box_port, box_direct):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
                        assert json.load(response)["name"] == target.name
                # The preview owner is never the business target, so its routes
                # hold across the switch: the tunnel (both spellings, on api-front
                # and on the front door) and every preview content host, while a
                # project host still lands on whatever business target is serving.
                for port, path, host, expected in (
                    (api, "/preview/tunnel", "app.example.test", "preview"),
                    (api, "/api/preview/tunnel", "app.example.test", "preview"),
                    (public, "/preview/tunnel", "app.example.test", "preview"),
                    (public, "/api/preview/tunnel", "app.example.test", "preview"),
                    (api, "/", preview_host, "preview"),
                    (api, "/assets/app.js", preview_resource_host, "preview"),
                    (api, "/", "example.net", target.name),
                ):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}{path}", headers={"Host": host}
                    )
                    with urllib.request.urlopen(request, timeout=3) as response:
                        actual = json.load(response)["name"]
                    assert actual == expected, f"{host}{path} -> {actual}, want {expected}"
                # The owner's private RPC never crosses nginx, on either origin.
                for port, host in ((api, "app.example.test"), (api, preview_host), (public, "app.example.test")):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/_internal/preview/v1/inspect",
                        headers={"Host": host},
                    )
                    try:
                        urllib.request.urlopen(request, timeout=3)
                        raise AssertionError(f"private preview RPC reachable via {host}")
                    except urllib.error.HTTPError as exc:
                        assert exc.code == 404, (host, exc.code)
                print(f"PASS: business traffic switched to {target.name}; {len(sockets)} existing WebSockets survived 32s", flush=True)
        finally:
            for sock in sockets:
                sock.close()
            for command in reversed(commands):
                subprocess.run([*command, "-s", "quit"], check=False)
            for server in servers:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    main()
