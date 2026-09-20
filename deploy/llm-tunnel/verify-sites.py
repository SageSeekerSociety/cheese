#!/usr/bin/env python3
"""Verify generated nginx routing on Mac mini using loopback upstreams only."""

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HERE = Path(__file__).resolve().parent


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({
            "upstream": self.server.upstream_name,
            "path": self.path,
            "host": self.headers.get("Host"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    nginx = sys.argv[1] if len(sys.argv) > 1 else shutil.which("nginx")
    if not nginx:
        raise SystemExit("Pass the installed nginx binary as the first argument.")
    scratch = HERE.parents[1] / "tmp"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sites-gateway-", dir=scratch) as temporary:
        root = Path(temporary)
        active = root / "active"
        active.mkdir()
        servers = []
        for name in ("backend", "terminator"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
            server.upstream_name = name
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        backend, terminator = servers
        (active / "backend.conf").write_text(
            f"upstream backend_active {{ server 127.0.0.1:{backend.server_port}; }}\n"
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        source = (HERE / "nginx.conf").read_text()
        source = source.replace("/etc/nginx/active", str(active))
        source = source.replace("listen 8081;", f"listen 127.0.0.1:{port};")
        source = source.replace("127.0.0.1:8091", f"127.0.0.1:{terminator.server_port}")
        config = root / "nginx.conf"
        config.write_text(f"pid {root}/nginx.pid;\nerror_log stderr;\n" + source)
        (root / "logs").mkdir()
        site_host = "0123456789abcdef0123456789abcdef.example.net"

        try:
            for mode in ("absent", "example.net", "--disable"):
                if mode != "absent":
                    subprocess.run(["bash", str(HERE / "configure-sites.sh"), mode, str(active)], check=True)
                    generated = active / "sites.conf"
                    generated.write_text(generated.read_text().replace(
                        "listen 8081;", f"listen 127.0.0.1:{port};"
                    ))
                command = [nginx, "-p", str(root) + "/", "-c", str(config)]
                subprocess.run([*command, "-t"], check=True)
                process = subprocess.Popen([*command, "-g", "daemon off;"])
                try:
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                                break
                        except OSError:
                            if process.poll() is not None or time.monotonic() >= deadline:
                                raise RuntimeError("Test nginx did not start")
                            time.sleep(0.05)
                    cases = [
                        ("app.example.com", "/llm/tunnel", "terminator"),
                        ("app.example.com", "/healthz", "backend"),
                        (site_host, "/llm/tunnel", "backend" if mode == "example.net" else "terminator"),
                        (site_host, "/assets/app.js?version=1", "backend"),
                    ]
                    for host, path, expected in cases:
                        request = urllib.request.Request(
                            f"http://127.0.0.1:{port}{path}", headers={"Host": host}
                        )
                        with urllib.request.urlopen(request, timeout=3) as response:
                            actual = json.load(response)
                        assert actual == {"upstream": expected, "path": path, "host": host}, actual
                        print(f"PASS {mode}: {host}{path} -> {expected}", flush=True)
                finally:
                    process.terminate()
                    process.wait(timeout=5)
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
    print("PASS: default, enabled and disabled routes; all listeners used loopback.")


if __name__ == "__main__":
    main()
