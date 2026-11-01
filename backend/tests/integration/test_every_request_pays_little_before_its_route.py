"""What every request pays before its route runs.

Two fixed costs sat in front of every route:

- each ``@app.middleware("http")`` is a Starlette ``BaseHTTPMiddleware``, which
  runs the rest of the request in a task of its own and relays the response
  through a memory stream, about half a millisecond a layer;
- the app's router asked its entries one by one whether they matched, and an
  included router answers by walking all of its own routes, so a route near
  the end of the list cost milliseconds to find (`app/core/route_index.py`).
"""

import re

from fastapi.routing import APIRoute, APIWebSocketRoute, _IncludedRouter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from app.main import app


def test_only_the_request_context_layer_relays_through_a_task():
    stack = app.middleware_stack or app.build_middleware_stack()
    relays = []
    node = stack
    while node is not None:
        if isinstance(node, BaseHTTPMiddleware):
            relays.append(node.dispatch_func.__name__)
        node = getattr(node, "app", None)

    assert relays == ["request_context"]


def test_a_request_asks_only_the_routers_holding_its_first_segment(
    client, monkeypatch
):
    asked: list[object] = []
    matches = _IncludedRouter.matches

    def counted(self, scope):
        asked.append(self)
        return matches(self, scope)

    monkeypatch.setattr(_IncludedRouter, "matches", counted)
    client.get("/users/me")

    holding = [
        route
        for route in app.router.routes
        if isinstance(route, _IncludedRouter)
        and any(
            (context.path or "").startswith("/users")
            for context in route.effective_route_contexts()
        )
    ]
    assert asked
    assert len(asked) <= len(holding)
    assert len(holding) < len(app.router.routes) / 4


def _sample(path: str) -> str:
    return re.sub(r"\{[^}:]+(:path)?\}", "x1", path)


def _first_full(routes, scope):
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route
    return None


def test_every_route_is_found_where_the_whole_list_finds_it():
    """For every route the app serves, the indexed lookup picks the same entry
    the router's own loop picks."""
    # deferred-import: the two tests above are proven red against a tree
    # without this module.
    from app.core.route_index import RouteIndex

    index = RouteIndex(app.router)
    checked = 0
    for top in app.router.routes:
        contexts = (
            list(top.effective_route_contexts())
            if isinstance(top, _IncludedRouter)
            else []
        )
        for context in contexts:
            leaf = context.original_route
            path = context.path or getattr(leaf, "path", "")
            if isinstance(leaf, APIWebSocketRoute):
                kinds = [("websocket", None)]
            elif isinstance(leaf, APIRoute):
                kinds = [("http", method) for method in leaf.methods]
            else:
                continue
            for kind, method in kinds:
                scope = {
                    "type": kind,
                    "path": _sample(path),
                    "root_path": "",
                    "headers": [],
                    "query_string": b"",
                    "app": app,
                }
                if method:
                    scope["method"] = method
                whole = _first_full(app.router.routes, dict(scope))
                indexed = _first_full(index.candidates(scope), dict(scope))
                assert indexed is whole, (method, path)
                checked += 1

    assert checked > 500
