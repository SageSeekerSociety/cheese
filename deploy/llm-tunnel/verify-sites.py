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
        for name in ("backend", "terminator", "owner"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
            server.upstream_name = name
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        backend, terminator, owner = servers
        (active / "backend.conf").write_text(
            f"upstream backend_active {{ server 127.0.0.1:{backend.server_port}; }}\n"
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        source = (HERE / "nginx.conf").read_text()
        # Distribution builds may default to a root-owned absolute access log.
        temporary_paths = "\n".join(
            f"  {kind}_temp_path {root}/{kind};"
            for kind in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
        )
        source = source.replace("http {", f"http {{\n  access_log off;\n{temporary_paths}")
        source = source.replace("/etc/nginx/active", str(active))
        source = source.replace("listen 8081;", f"listen 127.0.0.1:{port};")
        source = source.replace("127.0.0.1:8091", f"127.0.0.1:{terminator.server_port}")
        source = source.replace("127.0.0.1:18085", f"127.0.0.1:{backend.server_port}")
        config = root / "nginx.conf"
        config.write_text(f"pid {root}/nginx.pid;\nerror_log stderr;\n" + source)
        (root / "logs").mkdir()
        site_host = "0123456789abcdef0123456789abcdef.example.net"
        # A preview content host is preview-<32hex>.<domain>, optionally with a
        # -<22hex> resource suffix; the split in sites.conf matches both.
        preview_host = "preview-0123456789abcdef0123456789abcdef.example.net"
        preview_resource_host = (
            "preview-0123456789abcdef0123456789abcdef-0123456789abcdef012345.example.net"
        )

        def write_preview_routing(mode):
            # nginx.conf includes this file unconditionally, so even the modes
            # that do not exercise the preview route need it to exist. `owner`
            # points the tunnel map at the owner's loopback port, `legacy` at the
            # active backend through app-router — exactly what the deploy writes.
            if mode == "owner":
                subprocess.run(
                    ["bash", str(HERE / "configure-preview.sh"), "owner", str(active), str(owner.server_port)],
                    check=True,
                )
            else:
                subprocess.run(
                    ["bash", str(HERE / "configure-preview.sh"), "legacy", str(active),
                     "18087", str(backend.server_port)],
                    check=True,
                )

        def write_sites(mode):
            if mode == "absent":
                (active / "sites.conf").unlink(missing_ok=True)
                return
            if mode == "example.net":
                subprocess.run(["bash", str(HERE / "configure-sites.sh"), "example.net", str(active)], check=True)
            elif mode == "example.net+owner":
                subprocess.run(
                    ["bash", str(HERE / "configure-sites.sh"), "example.net", str(active), str(owner.server_port)],
                    check=True,
                )
            elif mode == "--disable":
                subprocess.run(["bash", str(HERE / "configure-sites.sh"), "--disable", str(active)], check=True)
            else:
                raise AssertionError(mode)
            generated = active / "sites.conf"
            # The split's default target is the literal app-router port; move it
            # (and the listener) onto this run's loopback upstreams.
            generated.write_text(generated.read_text()
                                 .replace("listen 8081;", f"listen 127.0.0.1:{port};")
                                 .replace("127.0.0.1:18085", f"127.0.0.1:{backend.server_port}"))

        # Each scenario is (label, sites mode, preview-routing mode, cases).
        # `absent` must run before a sites.conf exists; the later scenarios
        # overwrite it, and `--disable` writes an empty file that behaves like
        # absent again.
        scenarios = [
            ("absent", "absent", "legacy", [
                ("app.example.com", "/llm/tunnel", "terminator"),
                ("app.example.com", "/healthz", "backend"),
                ("app.example.com", "/preview/tunnel", "backend"),
                ("app.example.com", "/api/preview/tunnel", "backend"),
                (site_host, "/llm/tunnel", "terminator"),
                (site_host, "/assets/app.js?version=1", "backend"),
                (preview_host, "/", "backend"),
            ]),
            ("example.net", "example.net", "legacy", [
                ("app.example.com", "/llm/tunnel", "terminator"),
                ("app.example.com", "/healthz", "backend"),
                (site_host, "/llm/tunnel", "backend"),
                (site_host, "/assets/app.js?version=1", "backend"),
                (preview_host, "/", "backend"),
            ]),
            ("--disable", "--disable", "legacy", [
                ("app.example.com", "/llm/tunnel", "terminator"),
                ("app.example.com", "/healthz", "backend"),
                (site_host, "/llm/tunnel", "terminator"),
                (site_host, "/assets/app.js?version=1", "backend"),
                (preview_host, "/", "backend"),
            ]),
            # The cutover: the preview tunnel, both spellings, and every preview
            # content host reach the owner; project hosts stay on the backend.
            ("preview-owner", "example.net+owner", "owner", [
                ("app.example.com", "/preview/tunnel", "owner"),
                ("app.example.com", "/api/preview/tunnel", "owner"),
                ("app.example.com", "/llm/tunnel", "terminator"),
                (site_host, "/", "backend"),
                (preview_host, "/", "owner"),
                (preview_resource_host, "/assets/app.js", "owner"),
                ("preview-0123456789abcdef0123456789abcdef.example.com", "/", "backend"),
            ]),
        ]

        try:
            for label, sites_mode, preview_mode, cases in scenarios:
                write_preview_routing(preview_mode)
                write_sites(sites_mode)
                command = [nginx, "-e", "stderr", "-p", str(root) + "/", "-c", str(config)]
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
                    for host, path, expected in cases:
                        request = urllib.request.Request(
                            f"http://127.0.0.1:{port}{path}", headers={"Host": host}
                        )
                        with urllib.request.urlopen(request, timeout=3) as response:
                            actual = json.load(response)
                        assert actual == {"upstream": expected, "path": path, "host": host}, actual
                        print(f"PASS {label}: {host}{path} -> {expected}", flush=True)
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
