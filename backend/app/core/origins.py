"""The origins this deployment is served under, and which one a request is on.

``FRONTEND_URL`` is the canonical origin; ``FRONTEND_ALIASES`` are the others.
A redirect inside a browser flow goes back to the origin the browser is on
(``of_request``); a link read later, outside the request — mail, notifications
— stays on the canonical one.

The request's origin is read from its ``Host``, which every front door passes
through unchanged. Only configured origins are ever returned, so a forged
``Host`` chooses among this deployment's own origins and nothing else.
"""

from urllib.parse import urlsplit

from starlette.requests import HTTPConnection

from app.core.config import browser_origin, settings


def canonical() -> str:
    return browser_origin(settings.frontend_url)


def all_origins() -> tuple[str, ...]:
    """The canonical origin first, then every alias."""
    return (canonical(), *settings.frontend_aliases)


def is_ours(origin: str | None) -> bool:
    """``origin`` is exactly one of this deployment's origins, written as a
    browser writes an Origin header: nothing before or after it."""
    return origin in all_origins()


def of_request(request: HTTPConnection) -> str:
    """The origin ``request`` arrived on, when it is one of ours; the canonical
    origin otherwise (an internal call, an API host of its own)."""
    host = (request.headers.get("host") or "").strip().lower()
    for origin in all_origins():
        if urlsplit(origin).netloc == host:
            return origin
    return canonical()
