"""Every pattern in the cheese write gate names a route that exists.

The gate (`app.main._CHEESE_WRITE_PATHS`) is a list of regexes written as text.
A pattern that stops matching does not fail anything: the route it was meant to
close is simply open. #370 flattened a prefix and every pattern went dead at
once. So the patterns are checked against the live route table here, by method
and by path, the way the gate itself reads a request.
"""

from __future__ import annotations

import re

from fastapi.routing import APIRoute

from app.main import _CHEESE_WRITE_PATHS, app

_PARAM = re.compile(r"\{[^}]+\}")


def _http_routes(node) -> list[APIRoute]:  # type: ignore[no-untyped-def]
    """Effective routes. FastAPI 0.137 keeps an included router as one
    `_IncludedRouter` in `app.routes`; its contexts carry the prefixed path."""
    if hasattr(node, "effective_route_contexts"):
        return [
            ctx
            for ctx in node.effective_route_contexts()
            if isinstance(ctx.original_route, APIRoute)
        ]
    return [node] if isinstance(node, APIRoute) else []


def _concrete(path: str) -> str:
    return _PARAM.sub("x", path)


def test_each_pattern_matches_a_route_with_its_method() -> None:
    routes = [r for entry in app.routes for r in _http_routes(entry)]
    dead = [
        (method, rx.pattern)
        for method, rx in _CHEESE_WRITE_PATHS
        if not any(
            method in (r.methods or ()) and rx.match(_concrete(r.path))
            for r in routes
        )
    ]
    assert not dead, f"gate patterns that close no route: {dead}"
