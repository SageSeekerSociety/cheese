#!/usr/bin/env python3
"""Keep a copy of okcheese.com's built /assets/ on the Hong Kong box.

The page's hashed files never change once built, so Caddy can answer
/assets/<name> from this copy instead of sending every request through the
reverse SSH tunnels to the dev box. This job only adds files: every POLL
seconds it reads the live index.html and service worker from the dev box
(through the same tunnels Caddy uses), and fetches any /assets/ file they
name that is not here yet. A file not here yet is still served, by Caddy's
fallback to the tunnels, so a deploy never waits on this job.

A file is published under its final name only after its size matched the
origin's Content-Length; a gzip twin is written beside compressible files for
Caddy's `precompressed gzip`. Files nobody has referenced for KEEP_DAYS are
removed, so a tab still running an older build keeps finding its chunks for
that long.

Installed by hand on etrip as /usr/local/libexec/cheese-edge/edge-asset-sync.py
(unit cheese-edge-asset-sync.service); see docs/infrastructure.md, "Public
edge".
"""
import gzip
import http.client
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.environ.get("EDGE_ROOT", "/srv/okcheese-edge")
ASSETS = os.path.join(ROOT, "assets")
# The tunnels' plain-HTTP ends, the same upstreams Caddy's okcheese site uses.
ORIGINS = [p for p in os.environ.get("EDGE_ORIGINS", "127.0.0.1:18453 127.0.0.1:18454").split()]
HOST = os.environ.get("EDGE_HOST", "okcheese.com")
POLL = int(os.environ.get("EDGE_POLL", "15"))
KEEP_DAYS = int(os.environ.get("EDGE_KEEP_DAYS", "14"))
WORKERS = int(os.environ.get("EDGE_WORKERS", "2"))
COMPRESSIBLE = (".js", ".css", ".svg", ".json", ".webmanifest", ".txt", ".html", ".mjs", ".map")
NAME = re.compile(r"^[A-Za-z0-9._@-]+$")

etags: dict[str, str] = {}
bodies: dict[str, bytes] = {}


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def get(path: str, conditional: bool = False) -> tuple[int, dict, bytes]:
    """GET from the first origin that answers; raises if none does."""
    err = None
    for origin in ORIGINS:
        host, port = origin.rsplit(":", 1)
        conn = http.client.HTTPConnection(host, int(port), timeout=60)
        headers = {"Host": HOST, "Accept-Encoding": "identity", "User-Agent": "cheese-edge-asset-sync"}
        if conditional and path in etags:
            headers["If-None-Match"] = etags[path]
        try:
            conn.request("GET", path, headers=headers)
            r = conn.getresponse()
            body = r.read()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, body
        except OSError as e:
            err = e
        finally:
            conn.close()
    raise OSError(f"no origin answered {path}: {err}")


def referenced() -> set[str] | None:
    """Asset names the live index.html and service worker point at."""
    names: set[str] = set()
    for path, pattern in (("/", r"/assets/([^\"'\s)?#]+)"), ("/sw.js", r"url:\"assets/([^\"]+)\"")):
        status, headers, body = get(path, conditional=True)
        if status == 304:
            body = bodies[path]
        elif status == 200:
            bodies[path] = body
            if "etag" in headers:
                etags[path] = headers["etag"]
        else:
            log(f"skip poll: {path} answered {status}")
            return None
        names.update(re.findall(pattern, body.decode("utf-8", "replace")))
    return {n for n in names if NAME.match(n)}


def fetch(name: str) -> None:
    status, headers, body = get(f"/assets/{name}")
    if status != 200:
        log(f"fail {name}: status {status}")
        return
    want = headers.get("content-length")
    if want is None or int(want) != len(body) or headers.get("content-encoding", "identity") != "identity":
        log(f"fail {name}: got {len(body)} bytes, content-length {want}, encoding {headers.get('content-encoding')}")
        return
    if name.endswith(COMPRESSIBLE):
        write(name + ".gz", gzip.compress(body, compresslevel=9, mtime=0))
    write(name, body)  # last, so the plain name appears only with its twin
    log(f"added {name} {len(body)}B")


def write(name: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=ASSETS, prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(ASSETS, name))


def prune(live: set[str]) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for entry in os.scandir(ASSETS):
        base = entry.name[:-3] if entry.name.endswith(".gz") else entry.name
        if entry.name.startswith(".tmp-") and entry.stat().st_mtime < time.time() - 3600:
            os.unlink(entry.path)
        elif base in live:
            os.utime(entry.path)  # still referenced: restart its clock
        elif entry.stat().st_mtime < cutoff:
            os.unlink(entry.path)
            log(f"pruned {entry.name}")


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    last_prune = 0.0
    with ThreadPoolExecutor(WORKERS) as pool:
        while True:
            try:
                live = referenced()
                if live is not None:
                    missing = sorted(n for n in live if not os.path.exists(os.path.join(ASSETS, n)))
                    if missing:
                        log(f"poll: {len(live)} referenced, fetching {len(missing)}")
                        list(pool.map(lambda n: _safe(fetch, n), missing))
                    if time.time() - last_prune > 3600:
                        prune(live)
                        last_prune = time.time()
            except OSError as e:
                log(f"poll failed: {e}")
            time.sleep(POLL)


def _safe(fn, arg) -> None:
    try:
        fn(arg)
    except OSError as e:
        log(f"fail {arg}: {e}")


if __name__ == "__main__":
    sys.exit(main())
