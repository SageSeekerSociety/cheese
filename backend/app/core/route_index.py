"""Find a request's route among the few that could match it.

The app's router asks its entries one by one whether they match, and nearly
every entry is a whole included router (``include_router``), which in FastAPI
0.137 answers by walking every route inside it, saving and restoring scope keys
for each. A request for a route near the end of the list paid for all of that
before reaching it: measured on this app, about 2.7 ms to find the session
work-lease route and 2.9 ms for ``/users/me``, on every request.

A route can only match a path of its own shape: as many segments, its literal
segments where it has them (a `:path` parameter or a mount's prefix takes all
the rest). The router's entries are indexed by the shapes of the routes they
hold, in a tree walked segment by segment, and a request is matched only
against the entries holding a route of its shape, in the router's own order.
An entry that is not a plain route, mount or included router is matched for
every request. With the first segment alone, the 37 routers under `/topics`
were still asked in turn for each of their requests (0.48 ms for the work
lease).
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
# A route path, segment by segment: a literal segment, one parameter segment,
# or a `:path` parameter (or a mount's prefix) that takes all the rest.
_PARAM = object()
_REST = object()


def _pattern(path: str, *, mount: bool = False) -> tuple:
    parts: list[object] = []
    for segment in path.lstrip("/").split("/"):
        if ":path}" in segment:
            parts.append(_REST)
            return tuple(parts)
        parts.append(_PARAM if "{" in segment else segment)
    if mount:
        if parts == [""]:
            return (_REST,)
        parts.append(_REST)
    return tuple(parts)


def _patterns_of(route: BaseRoute) -> list[tuple] | None:
    """The shapes of every path ``route`` can match; None for any path."""
    if isinstance(route, _IncludedRouter):
        found: list[tuple] = []
        for context in route.effective_route_contexts():
            leaf = context.starlette_route or context.original_route
            path = context.path or getattr(leaf, "path", None)
            if isinstance(leaf, Host) or path is None:
                return _ANY
            found.append(_pattern(path, mount=isinstance(leaf, Mount)))
        return found
    path = getattr(route, "path", None)
    if isinstance(route, Host) or path is None:
        return _ANY
    return [_pattern(path, mount=isinstance(route, Mount))]


class _Node:
    __slots__ = ("literal", "param", "ends", "rest")

    def __init__(self) -> None:
        self.literal: dict[str, _Node] = {}
        self.param: _Node | None = None
        self.ends: set[int] = set()
        self.rest: set[int] = set()

    def add(self, pattern: tuple, entry: int) -> None:
        node = self
        for part in pattern:
            if part is _REST:
                node.rest.add(entry)
                return
            if part is _PARAM:
                node.param = node.param or _Node()
                node = node.param
            else:
                node = node.literal.setdefault(part, _Node())  # type: ignore[arg-type]
        node.ends.add(entry)

    def find(self, segments: list[str]) -> set[int]:
        found: set[int] = set()
        stack: list[tuple[_Node, int]] = [(self, 0)]
        while stack:
            node, at = stack.pop()
            found |= node.rest
            if at == len(segments):
                found |= node.ends
                continue
            segment = segments[at]
            child = node.literal.get(segment)
            if child is not None:
                stack.append((child, at + 1))
            if node.param is not None and segment:
                stack.append((node.param, at + 1))
        return found


class RouteIndex:
    """The router's entries by the shapes of the paths they match."""

    def __init__(self, router: Router) -> None:
        self._router = router
        self._built_for: tuple[int, ...] | None = None
        self._routes: list[BaseRoute] = []
        self._trie = _Node()
        self._anywhere: set[int] = set()

    def _build(self) -> None:
        routes = list(self._router.routes)
        trie = _Node()
        anywhere: set[int] = set()
        for entry, route in enumerate(routes):
            patterns = _patterns_of(route)
            if patterns is None:
                anywhere.add(entry)
                continue
            for pattern in patterns:
                trie.add(pattern, entry)
        self._routes, self._trie, self._anywhere = routes, trie, anywhere
        self._built_for = tuple(id(route) for route in routes)

    def candidates(self, scope: Scope) -> list[BaseRoute]:
        if self._built_for != tuple(id(route) for route in self._router.routes):
            self._build()
        segments = get_route_path(scope).lstrip("/").split("/")
        entries = self._trie.find(segments) | self._anywhere
        return [self._routes[entry] for entry in sorted(entries)]


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
