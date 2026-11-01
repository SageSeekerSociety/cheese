"""Find a request's route among the few that could match it.

The app's router asks its entries one by one whether they match, and nearly
every entry is a whole included router (``include_router``), which in FastAPI
0.137 answers by walking every route inside it, saving and restoring scope keys
for each. A request for a route near the end of the list paid for all of that
before reaching it: measured on this app, about 2.7 ms to find the session
work-lease route and 2.9 ms for ``/users/me``, on every request.

A route can only match a path whose first segment is its own first segment, so
the router's entries are indexed by the first segments of the routes they
hold. A request is matched against the entries listed under its first segment,
in the router's own order; an entry whose routes start with a parameter, or
that is not a plain route or included router, is listed under every segment.
Nothing is decided here that the router would decide otherwise: when no listed
entry matches in full, the request goes to the router's own loop, which gives
405s, slash redirects and 404s exactly as before (and finds a route added since
the index was built).
"""

from __future__ import annotations

from fastapi.routing import _IncludedRouter
from starlette._utils import get_route_path
from starlette.routing import BaseRoute, Host, Match, Mount, Router
from starlette.types import Receive, Scope, Send

_ANY = None


def _first_segment(path: str) -> str | None:
    """The literal first segment of a route path; None when it is a
    parameter, which can stand for any segment."""
    head = path.lstrip("/").split("/", 1)[0]
    return None if "{" in head else head


def _segments_of(route: BaseRoute) -> set[str] | None:
    """The first segments of every path ``route`` can match; None for any."""
    if isinstance(route, _IncludedRouter):
        found: set[str] = set()
        for context in route.effective_route_contexts():
            leaf = context.starlette_route or context.original_route
            if isinstance(leaf, Host):
                return _ANY
            path = context.path or getattr(leaf, "path", None)
            if path is None or (isinstance(leaf, Mount) and not path.strip("/")):
                return _ANY
            segment = _first_segment(path)
            if segment is None:
                return _ANY
            found.add(segment)
        return found
    path = getattr(route, "path", None)
    if isinstance(route, Host) or path is None:
        return _ANY
    if isinstance(route, Mount) and not path.strip("/"):
        return _ANY
    segment = _first_segment(path)
    return _ANY if segment is None else {segment}


class RouteIndex:
    """The router's entries by the first segment of the paths they match."""

    def __init__(self, router: Router) -> None:
        self._router = router
        self._built_for: tuple[int, ...] | None = None
        self._by_segment: dict[str, list[BaseRoute]] = {}
        self._anywhere: list[BaseRoute] = []

    def _build(self) -> None:
        routes = list(self._router.routes)
        segments = [_segments_of(route) for route in routes]
        named = set().union(*(s for s in segments if s is not None))
        self._by_segment = {
            name: [
                r
                for r, s in zip(routes, segments, strict=True)
                if s is None or name in s
            ]
            for name in named
        }
        self._anywhere = [r for r, s in zip(routes, segments, strict=True) if s is None]
        self._built_for = tuple(id(route) for route in routes)

    def candidates(self, scope: Scope) -> list[BaseRoute]:
        if self._built_for != tuple(id(route) for route in self._router.routes):
            self._build()
        segment = get_route_path(scope).lstrip("/").split("/", 1)[0]
        return self._by_segment.get(segment, self._anywhere)


def index_routes(router: Router) -> None:
    """Have ``router`` try only the entries that could match a request first.

    Only a router with no middleware of its own: the index stands where that
    middleware would."""
    full_loop = router.app
    if router.middleware_stack != full_loop:
        return
    index = RouteIndex(router)

    async def indexed(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            for route in index.candidates(scope):
                match, child_scope = route.matches(scope)
                if match == Match.FULL:
                    if "router" not in scope:
                        scope["router"] = router
                    scope.update(child_scope)
                    await route.handle(scope, receive, send)
                    return
        await full_loop(scope, receive, send)

    router.middleware_stack = indexed
