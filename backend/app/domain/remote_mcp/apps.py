"""MCP Apps (``io.modelcontextprotocol/ui``): a remote server's own HTML surface.

芝士's artifacts are rendered with its runtime injected into the page
(``app.api.preview_runtime``). An MCP App is not that: a remote server delivered
a View as a resource (``ui://…``, mime ``text/html;profile=mcp-app``) and speaks
to the host itself over JSON-RPC on postMessage. Nothing of ours is injected, and
the markup is a third party's — it gets its own origin and a tighter sandbox than
the room's artifacts (see ``app.api.preview_host``).

This module is the extension's pure rules and identifiers only: how a tool names
its View, and how a resource's locally-declared CSP is read back. The bytes are
fetched and served elsewhere (``app.domain.remote_mcp.service``, the preview host).
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

MIME = "text/html;profile=mcp-app"
EXTENSION = "io.modelcontextprotocol/ui"
SCHEME = "ui://"


def capabilities() -> dict:
    """What a client advertises in ``initialize`` to negotiate the extension."""
    return {"extensions": {EXTENSION: {"mimeTypes": [MIME]}}}


def is_ui_uri(uri: object) -> bool:
    """Whether ``uri`` is a View address, as the extension defines it."""
    return isinstance(uri, str) and uri.startswith(SCHEME)


def tool_resource_uri(tool: object) -> str | None:
    """The View a tool opens, from its ``_meta.ui.resourceUri``. None when the
    tool opens none, or names something that is not a ``ui://`` resource."""
    ui = _ui(tool)
    uri = ui.get("resourceUri") if ui else None
    return uri if is_ui_uri(uri) else None


def _ui(value: object) -> dict | None:
    """The ``ui`` object under a payload's ``_meta``, when it has one."""
    if not isinstance(value, dict):
        return None
    meta = value.get("_meta")
    ui = meta.get("ui") if isinstance(meta, dict) else None
    return ui if isinstance(ui, dict) else None


def pointer(server: str, uri: str) -> str:
    """How an mcp-app artifact's ``content`` says which server's View it is.

    An artifact's content is one text column, and this View is named by two
    things (which server, which resource); a JSON object carries both without a
    second column or a migration."""
    return json.dumps({"server": server, "uri": uri}, separators=(",", ":"))


def pointer_parts(content: str) -> tuple[str, str] | None:
    """``(server, uri)`` from an mcp-app artifact's ``content``, or None when it
    does not name a ``ui://`` resource on a named server."""
    try:
        found = json.loads(content)
    except ValueError:
        return None
    if not isinstance(found, dict):
        return None
    server, uri = found.get("server"), found.get("uri")
    if isinstance(server, str) and server and isinstance(uri, str) and is_ui_uri(uri):
        return server, uri
    return None


def declared_origins(contents: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(connect, resource)`` origins an App's HTML may reach.

    Read from the ``_meta.ui.csp`` a ``resources/read`` content entry carries:
    ``connectDomains`` is what the View may fetch, ``resourceDomains`` is where
    its scripts, styles and images may come from. Only real http(s) origins
    survive — a value that is not one cannot go into a CSP, and letting it
    through would either break the header or widen it.
    """
    ui = _ui(contents)
    csp = ui.get("csp") if ui else None
    csp = csp if isinstance(csp, dict) else {}

    def origins(key: str) -> tuple[str, ...]:
        declared = csp.get(key)
        if not isinstance(declared, list):
            return ()
        return tuple(
            origin for value in declared if (origin := _origin(value)) is not None
        )

    return origins("connectDomains"), origins("resourceDomains")


def _origin(value: object) -> str | None:
    """A bare ``scheme://host[:port]`` for a CSP source, or None if unusable."""
    if not isinstance(value, str):
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"
