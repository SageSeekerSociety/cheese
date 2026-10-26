"""What a session's sandbox may ask of the platform.

A sandbox holds no credential. Its request reaches these routes only through
the process that owns the device links (``app.api.machine_relay``), which names
the device and the state directory the request arrived on and vouches for both
with the device connection secret; the caller is whichever session holds that
state now (``app.api.machine_caller``). A model call it makes is served by
the model route (``llm_proxy``, ``/machine/llm``); anything else it asks of the
platform's API goes to ``/machine/api/...`` and is served by the route it
names, under the session's own credential made for that request.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.exceptions import ExceptionMiddleware
from starlette.types import Receive, Scope, Send

from app.api.machine_caller import Caller
from app.api.write_access import ROUTE_DECIDES
from app.core.db import get_db, release_read_session
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.machine.caller import credential_of

router = APIRouter(prefix="/machine", tags=["machine"], include_in_schema=False)

#: The platform's own headers a sandbox's request keeps: what the machine's
#: CLI sends besides who it is (`sandbox/cheese`). Every other ``x-`` header,
#: and the standard ones that say who is calling, are dropped: a header such
#: as ``x-cheese-screen`` would otherwise change whom the route serves.
_KEPT = frozenset(
    {
        b"x-cheese-turn",
        b"x-cheese-pass",
        b"x-cheese-head",
        b"x-content-sha256",
    }
)
_DROPPED = frozenset({b"authorization", b"cookie", b"proxy-authorization"})


def _kept(name: bytes) -> bool:
    name = name.lower()
    if name in _DROPPED:
        return False
    return not name.startswith(b"x-") or name in _KEPT


def _routes(app):
    """The app below its middleware: where the request has already been
    through it once (limits, metrics, hosts), but still with the exception
    handlers that turn a refusal into its answer."""
    layer = getattr(app, "middleware_stack", None)
    while layer is not None and not isinstance(layer, ExceptionMiddleware):
        layer = getattr(layer, "app", None)
    if layer is None:
        # Running it from the top would put the request through every
        # middleware twice; a stack that hides its routes is a change to make
        # here, not one to serve around.
        raise RuntimeError(
            "The app's routes are not below its middleware"  # i18n-exempt: a platform bug
        )
    return layer


#: The first segments of the platform's API a sandbox does not reach here: the
#: model routes, which it asks on ``/machine/llm`` under a key of its own, and
#: these routes themselves.
_NOT_HERE = frozenset({"llm", "machine"})


@router.get("/whoami")
async def whoami(caller: Caller) -> dict:
    """Whose this sandbox is, as the platform sees it from the socket alone."""
    return {
        "session": caller.session_id,
        "project": caller.project_id,
        "conversation": caller.conversation_id,
        "agent": caller.agent_handle,
    }


class _AsTheSession(Response):
    """The request, handed to the route it names with the session's
    credential in place of whatever said who was calling; that route's answer
    is streamed back as it is."""

    def __init__(self, request: Request, path: str, credential: str) -> None:
        super().__init__()
        self._request = request
        self._path = path
        self._credential = credential

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = [(name, value) for name, value in scope["headers"] if _kept(name)]
        headers.append((b"x-cheese-token", self._credential.encode()))
        inner = {
            **scope,
            "path": self._path,
            "raw_path": self._path.encode(),
            "root_path": "",
            "headers": headers,
            # Its own copy: what the route asked keeps on its request stays
            # off this one's.
            "state": dict(scope.get("state") or {}),
        }
        await _routes(self._request.app)(inner, receive, send)


@router.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    dependencies=[ROUTE_DECIDES],
)
async def platform(
    path: str,
    request: Request,
    caller: Caller,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """The platform's API, asked by a sandbox on its platform socket: served by
    the route ``path`` names, as the session's own credential would be (its
    lease, and a 支线's keeping no work, included), made here for this one
    request and never handed to the sandbox."""
    if path.split("/", 1)[0] in _NOT_HERE:
        raise NotFoundError()
    credential = await credential_of(db, caller)
    if credential is None:
        raise ForbiddenError()
    # The route asked may hold the request for minutes.
    await release_read_session(db)
    return _AsTheSession(request, "/" + path, credential)
