"""Which requests the api pool may answer (`app.core.process_role`).

The pool answers every plain `GET` except the families that read the realtime
process's own state. That list is kept by hand, so two things hold it to the
app: each family still names a mounted route, and no `GET` route that asks for
a realtime-only object by dependency is left to the pool.
"""

import re

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute

from app.api import deps
from app.core.process_role import REALTIME_GET_PATTERNS, served_by_pool
from app.domain.agent.realtime.broker import get_broker
from app.main import app

#: The realtime process's own objects. `get_chat_service` is not among them:
#: plain reads use it for its session factory, and what it holds of running
#: work is reached through the runner and the compute pool below.
REALTIME_ONLY = {
    get_broker,
    deps.get_work_runner,
    deps.get_session_host,
    deps.get_compute_pool,
    deps.get_consumptions,
}


def _calls(dependant: Dependant) -> set:
    found = {dependant.call} if dependant.call is not None else set()
    for sub in dependant.dependencies:
        found |= _calls(sub)
    return found


def _sample(path: str) -> str:
    """A concrete path the route answers, for matching it as app-router does."""
    return re.sub(r"\{[^}]+\}", "x", path)


def _leaves() -> list[tuple[str, frozenset[str], object]]:
    """Every mounted route as app-router sees it: (path, methods, route).
    WebSocket routes carry no methods."""
    leaves = []
    for route in app.routes:
        contexts = (
            [
                (c.original_route, c.starlette_route or c)
                for c in route.effective_route_contexts()
            ]
            if type(route).__name__ == "_IncludedRouter"
            else [(route, route)]
        )
        for original, effective in contexts:
            methods = frozenset(getattr(effective, "methods", None) or ())
            leaves.append((effective.path, methods, original))
    return leaves


def _gets() -> list[tuple[str, object]]:
    return [
        (path, route)
        for path, methods, route in _leaves()
        if isinstance(route, APIRoute) and {"GET", "HEAD"} & methods
    ]


def test_every_realtime_family_names_a_mounted_route():
    paths = [_sample(path) for path, _methods, _route in _leaves()]
    stale = [
        p for p in REALTIME_GET_PATTERNS if not any(re.search(p, x) for x in paths)
    ]
    assert stale == []


def test_no_get_route_that_needs_the_realtime_process_is_left_to_the_pool():
    leaked = [
        f"GET {path}"
        for path, route in _gets()
        if _calls(route.dependant) & REALTIME_ONLY
        and served_by_pool("GET", _sample(path))
    ]
    assert leaked == []


def test_websockets_never_go_to_the_pool():
    sockets = [
        path
        for path, _methods, route in _leaves()
        if isinstance(route, APIWebSocketRoute)
    ]
    assert sockets
    assert not any(served_by_pool("GET", _sample(p), upgrade=True) for p in sockets)


def test_only_plain_reads_go_to_the_pool():
    assert served_by_pool("GET", "/topics/x/blocks")
    assert served_by_pool("GET", "/topics")
    assert not served_by_pool("POST", "/topics/x/messages")
    assert not served_by_pool("GET", "/rooms/live", upgrade=True)
    assert not served_by_pool("GET", "/topics/x/status")
