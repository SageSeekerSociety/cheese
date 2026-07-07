"""Reconstruct the public origin (``scheme://host[:port]``) a request came in on.

The backend usually sits behind an edge (the vite dev proxy, or a prod ingress) that
rewrites the request's own host to an internal target (``localhost:8080``). To build
URLs the *client* will use — the cli ``base`` baked into ``install.sh`` and the
device-approve link — we need the origin the browser/curl actually hit, not the internal
one. The edge forwards it as ``X-Forwarded-Host`` / ``X-Forwarded-Proto`` (the vite dev
proxy is configured to do the same); we fall back to the ``Host`` header, then to the
request URL. This keeps the code independent of how the site is reached.
"""

from fastapi import Request


def public_origin(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host")
    host = forwarded_host or request.headers.get("host") or request.url.netloc
    # X-Forwarded-Host may carry a comma-separated chain; the first is the client-facing one.
    host = host.split(",")[0].strip()
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    return f"{scheme}://{host}"
