"""How a sandbox's request on its platform socket reaches the route it names.

The rules, as a person would state them:

- the route sees the session's credential and none of what the sandbox sent
  that could say who is calling, the platform's other headers included;
- what the sandbox's CLI sends besides who it is reaches the route;
- the request goes through the app's middleware once, not again for the route.
"""

from types import SimpleNamespace

import pytest
from starlette.middleware.exceptions import ExceptionMiddleware

from app.api.routes.machine import _AsTheSession

pytestmark = pytest.mark.anyio


class _Recorder:
    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)


class _Counting:
    """A middleware layer that counts what passes it."""

    def __init__(self, app):
        self.app = app
        self.passed = 0

    async def __call__(self, scope, receive, send):
        self.passed += 1
        await self.app(scope, receive, send)


def _app():
    routes = _Recorder()
    outer = _Counting(ExceptionMiddleware(routes))
    return SimpleNamespace(middleware_stack=outer), outer, routes


async def test_the_route_sees_the_session_and_only_the_clis_own_headers():
    app, outer, routes = _app()
    request = SimpleNamespace(app=app)
    sent = [
        (b"content-type", b"application/json"),
        (b"x-cheese-token", b"the-sandboxs-own"),
        (b"x-cheese-screen", b"someone-else"),
        (b"x-cheese-session", b"another"),
        (b"x-api-key", b"k"),
        (b"x-user-id", b"7"),
        (b"authorization", b"Bearer x"),
        (b"cookie", b"c=1"),
        (b"x-cheese-turn", b"turn"),
        (b"X-Cheese-Pass", b"pass"),
    ]
    scope = {"type": "http", "path": "/machine/api/topics/t", "headers": sent}

    await _AsTheSession(request, "/topics/t", "the-session")(scope, None, None)

    (inner,) = routes.scopes
    assert inner["path"] == "/topics/t"
    assert dict(inner["headers"]) == {
        b"content-type": b"application/json",
        b"x-cheese-turn": b"turn",
        b"X-Cheese-Pass": b"pass",
        b"x-cheese-token": b"the-session",
    }
    assert outer.passed == 0
