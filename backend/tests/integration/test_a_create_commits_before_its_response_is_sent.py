"""A create endpoint's row is committed before the client is told it exists.

The race this pins (merge queue, e2e run 36296605673)::

    POST /spaces                     201   ← the client is handed the id
    POST /admin/spaces/<id>/review   404   ← "Space not found"

Nothing had failed. ``app.core.db.get_db`` commits in the exit block of its
``yield``, and FastAPI 0.137.0 runs that block from ``request_stack`` *after*
``await response(scope, receive, send)`` (``fastapi/routing.py``) — so on main
the transaction is still open when the 201 leaves the route, and the very next
request reads a row that was never there. It looks like a missing rollback and
is not one: it is 先发响应、后提交. Committing where the writes end — 只留读在
后面 — removes the gap.

Why this file drives the ASGI app itself, and watches the *route's* frame:

* An ordinary ``TestClient`` test cannot see the ordering at all: it only
  returns once the whole ASGI call — dependency teardown included — is done.
* Watching the transport's ``http.response.start`` cannot see it either. Three
  of this app's own middlewares (``@app.middleware("http")``) are Starlette
  ``BaseHTTPMiddleware``, which runs the route in its own task and relays the
  response from the outer one — so at the transport the two moments race, and
  which one lands first is the scheduler's business, not the route's. (It was
  measured: on main, the relay may well come after the commit.)

So the request is driven through the real app — real ``get_db``, real
middleware, real database — while the two moments are read where the ordering
IS decided: inside the route. ``send`` at the route's own ASGI boundary is the
frame the client's 201 is made of, and the teardown that commits is what
follows it. Sessions are timestamped by wrapping ``app.core.db.
async_session_factory``; they are bound to this test's ``db_connection`` (the
one ``db_session`` holds its outer transaction on), so a request's writes roll
back with the test instead of landing in the shared database.
"""

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware

from app.common.auth import create_access_token
from app.core import db as core_db
from app.domain.space.models import Space
from tests.integration.conftest import registered

_COMMIT = "commit"
_RESPONSE = "response.start"


def _the_routes_side_of_the_relay(app):
    """The node whose ``.app`` the route runs in — the innermost of the
    platform's own ``@app.middleware("http")`` layers, which are Starlette
    ``BaseHTTPMiddleware``.

    Below it the response is still the route's own frame, sent in the route's
    own task and therefore ordered against the dependency teardown; above it,
    it is that middleware's relay, sent from a *second* task, and the two
    order themselves however the scheduler feels. So the frame this file reads
    has to be collected down here.
    """
    stack = app.middleware_stack
    if stack is None:
        # What this app's first request would have done anyway. Built here so
        # the middleware below can be reached without making a request first.
        stack = app.middleware_stack = app.build_middleware_stack()
    innermost = None
    node = stack
    while node is not None:
        if isinstance(node, BaseHTTPMiddleware):
            innermost = node
        node = getattr(node, "app", None)
    assert innermost is not None, "no BaseHTTPMiddleware to sit below"
    return innermost


class _WatchedRoute:
    """A route's ASGI app with the frame the client's response starts with
    recorded on the shared clock."""

    def __init__(self, app, events: list[str]) -> None:
        self._app = app
        self._events = events

    async def __call__(self, scope, receive, send) -> None:
        async def watched(message: dict) -> None:
            if message["type"] == "http.response.start":
                self._events.append(_RESPONSE)
            await send(message)

        await self._app(scope, receive, watched)


class _Asgi:
    """The real app driven one request at a time, from the portal's loop."""

    def __init__(self, app, portal, events: list[str]) -> None:
        self._app = app
        self._portal = portal
        self.events = events
        self.status: int | None = None
        self.body = b""

    def post(self, path: str, body: dict, *, token: str | None = None) -> None:
        # 每一次请求都从零开始记：上一个请求的收尾提交（``get_db`` 退出码里那一次）
        # 会落在下一个请求的响应之前，留着它就会替这一次请求把断言顶过去 —— 发题
        # 那条路由一行不改也照样绿（实测过：不清空时去掉 tasks 的提交，2 passed）。
        self.events.clear()
        headers = [(b"content-type", b"application/json")]
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        payload = json.dumps(body).encode()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
        }
        chunks: list[bytes] = []
        leftover = [payload]
        self.status = None

        async def receive() -> dict:
            # The body is handed over once; a second ask means the caller went
            # away (nothing here reads it twice, but a hang is a worse failure
            # than a disconnect).
            if not leftover:
                return {"type": "http.disconnect"}
            first, leftover[:] = leftover[0], []
            return {"type": "http.request", "body": first, "more_body": False}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                self.status = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        self._portal.call(self._app, scope, receive, send)
        self.body = b"".join(chunks)


@pytest.fixture
def asgi(app, _portal, db_session, db_connection, monkeypatch) -> _Asgi:
    """The real ASGI app, with every session's ``commit()`` and the watched
    routes' response frames timestamped in one order.

    ``db_session`` is requested for its side effect: it opens the outer
    transaction every session here joins, and rolls it back at the end. No
    ``get_db`` override is installed — the one the other harnesses use would
    hide the ordering this file is about.
    """
    events: list[str] = []

    class _RecordingSession(AsyncSession):
        async def commit(self) -> None:
            events.append(_COMMIT)
            await super().commit()

    maker = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        class_=_RecordingSession,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(core_db, "async_session_factory", lambda: maker())

    holder = _the_routes_side_of_the_relay(app)
    monkeypatch.setattr(holder, "app", _WatchedRoute(holder.app, events))

    return _Asgi(app, _portal, events)


@pytest.fixture
def author(db_session: AsyncSession, _portal) -> tuple[int, str]:
    """A registered person and the token that names them, as a browser holds it."""
    handle = f"commit-order-{uuid.uuid4().hex[:10]}"
    user_id = _portal.call(registered, db_session, handle)
    return user_id, create_access_token(user_id, handle=handle)


def _assert_committed_before_responding(asgi: _Asgi) -> None:
    """The whole claim: every response was handed on after its own commit.

    一条用例里可能发了不止一个请求（发题的用例先要建一块板），而 ``events`` 记的
    是这条用例全程。所以**每一个** ``response.start`` 都得有自己那一份提交排在它
    前面 —— 只比「第一次提交 vs 第一次响应」的话，建板那一次的提交会替后面那个
    请求把断言顶过去，发题那条路由一字不改也照样绿（实测过）。
    """
    commits = 0
    responses = 0
    for frame in asgi.events:
        if frame == _COMMIT:
            commits += 1
            continue
        responses += 1
        assert commits >= responses, (
            f"a response was sent before its transaction was committed: {asgi.events}"
        )
    assert responses > 0, f"nothing was recorded; frames were {asgi.events}"


def _space_body(name: str) -> dict:
    return {
        "name": name,
        "intro": "A space whose row must be readable the moment it exists.",
        "description": "Committed before the response.",
        "avatarId": 1,
        "announcements": [],
        "taskTemplates": [],
    }


def test_a_space_is_committed_before_its_201_is_sent(asgi: _Asgi, author) -> None:
    _, token = author

    asgi.post(
        "/spaces", _space_body(f"Committed space ({uuid.uuid4().hex[:8]})"), token=token
    )

    assert asgi.status == 201, asgi.body
    _assert_committed_before_responding(asgi)


def test_a_task_is_committed_before_its_response_is_sent(
    asgi: _Asgi, db_session: AsyncSession, _portal, author
) -> None:
    _, token = author

    asgi.post(
        "/spaces",
        _space_body(f"Committed task board ({uuid.uuid4().hex[:8]})"),
        token=token,
    )
    assert asgi.status == 201, asgi.body
    space_id = json.loads(asgi.body)["data"]["space"]["id"]

    # 发题只发生在通过评审的板里；评审本身是另一个端点的题目，这里直接落库 ——
    # 测试的会话和请求的会话在同一条连接、同一个事务上，彼此看得见。
    async def _approve() -> None:
        space = await db_session.get(Space, space_id)
        assert space is not None, space_id
        space.review_status = "APPROVED"
        await db_session.flush()

    _portal.call(_approve)

    asgi.post(
        "/tasks",
        {
            "name": f"Committed task ({uuid.uuid4().hex[:8]})",
            "intro": "An intro.",
            "description": "A description.",
            "space": space_id,
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
        },
        token=token,
    )

    assert asgi.status == 200, asgi.body
    _assert_committed_before_responding(asgi)
