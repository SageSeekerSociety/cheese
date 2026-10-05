#!/usr/bin/env python3
"""scripts/ops/edge-asset-sync.py against a fake origin.

The job runs for real in a subprocess, pointed at a local HTTP server that
plays the dev box's front door: an index.html and service worker naming some
/assets/ files. What is checked is only what Caddy will see on disk.
"""
import gzip
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "edge-asset-sync.py"

FILES = {
    "entry-AAAA1111.js": b"console.log('entry');\n" * 200,
    "style-BBBB2222.css": b"body{color:red}\n" * 100,
    "font-CCCC3333.woff2": bytes(range(256)) * 40,
    "lazy-DDDD4444.js": b"export const lazy = 1;\n",
}
INDEX = (
    b'<script type="module" src="/assets/entry-AAAA1111.js"></script>'
    b'<link rel="stylesheet" href="/assets/style-BBBB2222.css">'
    b'<link rel="preload" href="/assets/font-CCCC3333.woff2">'
    b'<link rel="modulepreload" href="/assets/broken-EEEE5555.js">'
)
SW = b'precacheAndRoute([{url:"assets/lazy-DDDD4444.js",revision:null},{url:"index.html",revision:"1"}])'
hits: dict[str, int] = {}


class Origin(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        hits[self.path] = hits.get(self.path, 0) + 1
        if self.headers.get("Host") != "okcheese.com":
            self.send_error(421)
            return
        if self.path == "/":
            body = INDEX
        elif self.path == "/sw.js":
            body = SW
        elif self.path.startswith("/assets/") and self.path[8:] in FILES:
            body = FILES[self.path[8:]]
        else:
            self.send_error(404)  # broken-EEEE5555.js: named, but the origin has no such file
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def run_until(root: Path, port: int, done, extra_env=None, timeout=20):
    env = dict(os.environ, EDGE_ROOT=str(root), EDGE_ORIGINS=f"127.0.0.1:1 127.0.0.1:{port}",
               EDGE_POLL="1", **(extra_env or {}))
    proc = subprocess.Popen([sys.executable, str(SCRIPT)], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    try:
        end = time.time() + timeout
        while time.time() < end and not done():
            time.sleep(0.2)
        time.sleep(1.5)  # one more poll, to see that it stays put
    finally:
        proc.terminate()
        out = proc.communicate(timeout=10)[0]
    return out


def main() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    failures = []

    def check(cond, what):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            failures.append(what)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assets = root / "assets"
        assets.mkdir()
        # A file from an older build, referenced by nothing, past its keep time.
        old = assets / "old-ZZZZ9999.js"
        old.write_bytes(b"old")
        os.utime(old, (time.time() - 3 * 86400, time.time() - 3 * 86400))
        # One from an older build still inside its keep time.
        recent = assets / "recent-YYYY8888.js"
        recent.write_bytes(b"recent")

        out = run_until(root, port, lambda: all((assets / n).exists() for n in FILES),
                        extra_env={"EDGE_KEEP_DAYS": "1"})

        for name, body in FILES.items():
            check((assets / name).read_bytes() == body if (assets / name).exists() else False,
                  f"{name} copied byte for byte")
        check(gzip.decompress((assets / "entry-AAAA1111.js.gz").read_bytes()) == FILES["entry-AAAA1111.js"],
              "a compressible file gets a gzip twin with the same content")
        check(not (assets / "font-CCCC3333.woff2.gz").exists(), "an already-compressed font gets no gzip twin")
        check(not (assets / "broken-EEEE5555.js").exists(), "a file the origin answers 404 for is not published")
        check(not any(p.name.startswith(".tmp-") for p in assets.iterdir()), "no temporary files left behind")
        check(not old.exists(), "an unreferenced file past its keep time is removed")
        check(recent.exists(), "an unreferenced file inside its keep time stays for older tabs")
        check(hits.get("/assets/entry-AAAA1111.js") == 1, "a file already copied is not fetched again")
        check("added entry-AAAA1111.js" in out, "each added file is logged")

        # The first origin in EDGE_ORIGINS refuses connections; the job used the second.
        check(hits.get("/", 0) >= 2, "polling continues past a dead first origin")

    if failures:
        print(f"{len(failures)} failed")
        sys.exit(1)
    print("all passed")


if __name__ == "__main__":
    main()
