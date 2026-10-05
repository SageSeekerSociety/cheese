"""Where the docs are: for the backend's own reads, and for the links it hands out.

The docs are one static site built twice (``docs/site/src/where.mjs``). With
``settings.docs_origin`` empty, the platform serves it under ``/docs/``; set, it
is the root of its own host and the platform's ``/docs/`` redirects there. The
index the backend reads holds paths within the site (``/accept#is-merge``) in
either build, so everything here that faces a person puts the docs' public
address in front (``public_url``), and everything else keeps the path.
"""

from urllib.parse import urlsplit

from app.core.config import settings

# The frontend container, by its compose name. With a docs host configured, its
# nginx answers this name with the docs host's server (frontend/nginx/), so the
# backend reads the very files readers get, at the same paths.
FRONTEND = "http://frontend"


def platform_origin() -> str:
    parts = urlsplit(settings.frontend_url)
    return f"{parts.scheme}://{parts.netloc}"


def origin() -> str:
    """The browser origin the docs are on: their own host, or the platform's."""
    return settings.docs_origin or platform_origin()


def base() -> str:
    """The path the site sits under on ``origin()``."""
    return "" if settings.docs_origin else "/docs"


def public_url(path: str) -> str:
    """A path within the site (``/accept#is-merge``), as a reader opens it."""
    return origin() + base() + path


def page_path(slug: str) -> str:
    """A page by name (``accept``, ``dev/turn``), as the index writes it."""
    return f"/{slug}"


def index_url() -> str:
    return settings.docs_index_url or f"{FRONTEND}{base()}/ask-index.json"


def dev_index_url() -> str:
    return settings.docs_dev_index_url or f"{FRONTEND}{base()}/dev/ask-index.json"


def on_docs_host(host_header: str | None) -> bool:
    """Whether a request reached the backend through the docs' own host.

    nginx passes the browser's Host through (``proxy_set_header Host $host``,
    which carries no port), so the name is what is compared."""
    host = (host_header or "").split(":", 1)[0].lower().rstrip(".")
    return bool(host) and host == (urlsplit(origin()).hostname or "")
