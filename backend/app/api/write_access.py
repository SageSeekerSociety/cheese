"""Who may call a write route, declared on the route itself.

Every POST / PUT / PATCH / DELETE route carries exactly one ``WriteAccess`` in
its dependencies. The instance is both the declaration (``seal`` finds it by
walking the route's dependency tree) and the check (FastAPI runs it):

- ``CHEESE_ONLY_IN_ROOM`` / ``CHEESE_ONLY_IN_PROJECT``: only a 芝士 credential
  for the room (``topic_id``) or project (``project_id``) in the path. A
  person's browser session is refused. Used for the write surface the ``cheese``
  CLI alone calls.
- ``ROUTE_DECIDES``: everyone reaches the handler, which authorizes the caller
  itself (``ActorResolver`` and friends). Most routes are this.

A router may declare for all its routes
(``APIRouter(dependencies=[ROUTE_DECIDES])``); a route may not declare twice.

Two refusals stand behind a route that declares nothing. ``seal`` runs once the
routes are mounted and refuses to start the app, unless the route is in the
frozen list of undeclared routes that predate this module
(``write_access_baseline``, which only shrinks). ``refuse_unsealed_writes`` is
an app-wide dependency that answers 403 to a write whose route ``seal`` did not
admit, so a route mounted after ``seal``, or an app nobody sealed, is closed
rather than open.

A route that is renamed or moved carries its declaration along, and a frozen
route that is renamed leaves the list and has to be declared. The gate this
replaced was a table of path regexes kept apart from the routes; #370 flattened
a prefix, every regex stopped matching, and the 芝士-only surface opened without
a single failure.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.requests import HTTPConnection
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.core.db import get_db
from app.core.errors import BaseError, ForbiddenError, UnauthorizedError
from app.core.sandbox_auth import (
    is_global_sandbox_token,
    is_valid_cheese_token,
    looks_like_project_agent_credential,
    scoped_token_claims,
)
from app.domain.agent_credential.services import ProjectAgentCredentialService

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, eq=False)
class WriteAccess:
    """One declaration: which callers reach the handler."""

    kind: Literal["cheese_only", "route_decides"]
    #: For ``cheese_only``: the path parameter ``<scope>_id`` names the room or
    #: project the credential must be for.
    scope: Literal["topic", "project", None] = None

    @property
    def name(self) -> str:
        where = {"topic": "_in_room", "project": "_in_project", None: ""}
        return self.kind + where[self.scope]

    async def __call__(
        self, request: Request, db: Annotated[AsyncSession, Depends(get_db)]
    ) -> None:
        if self.kind == "route_decides":
            return
        raw = request.path_params.get(f"{self.scope}_id")
        project_id = str(raw) if self.scope == "project" and raw else None
        topic_id = str(raw) if self.scope == "topic" and raw else None
        token = request.headers.get("x-cheese-token") or ""
        opened = is_valid_cheese_token(token, project_id=project_id, topic_id=topic_id)
        # A project agent credential can address topics in its project, so it
        # can't be matched against the URL by string compare the way a per-turn
        # token is — a topic path names its project only through the topic.
        if not is_global_sandbox_token(token) and (
            opened or looks_like_project_agent_credential(token)
        ):
            opened = await _credential_opens(
                db,
                token,
                project_id=project_id,
                topic_id=topic_id,
                screen_token=request.headers.get("x-cheese-screen") or "",
            )
        if not opened:
            raise UnauthorizedError("invalid sandbox token")


async def _credential_opens(
    db: AsyncSession,
    token: str,
    *,
    project_id: str | None,
    topic_id: str | None,
    screen_token: str,
) -> bool:
    """Whether the credential's participant may act in this room or project:
    the project match and the revocation check, which a signature alone does
    not answer. Reads only."""
    try:
        target = await ProjectAgentCredentialService(db).project_of_request(
            project_id=project_id, topic_id=topic_id
        )
        if target is None:
            return False
        claims = scoped_token_claims(token)
        origin = claims.get("t") if claims else None
        topic = uuid.UUID(topic_id or origin) if topic_id or origin else None
        resolver = ActorResolver(
            session=db, bearer=None, cheese_token=token, screen_token=screen_token
        )
        actor = await resolver.resolve(project_id=target, topic_id=topic)
        if not actor.authenticated:
            return False
        if topic is not None:
            await resolver.authorize_topic(actor, project_id=target, topic_id=topic)
        else:
            await resolver.authorize_project(actor, project_id=target)
        return True
    except (BaseError, ValueError):
        return False


CHEESE_ONLY_IN_ROOM = Depends(WriteAccess("cheese_only", "topic"))
CHEESE_ONLY_IN_PROJECT = Depends(WriteAccess("cheese_only", "project"))
ROUTE_DECIDES = Depends(WriteAccess("route_decides"))


@dataclass(frozen=True)
class WriteRoute:
    method: str
    path: str
    endpoint: str
    declared: tuple[str, ...]
    #: The route object Starlette puts in ``scope["route"]`` for this request.
    original: APIRoute


def _declarations(dependant: Dependant | None) -> list[WriteAccess]:
    found: dict[int, WriteAccess] = {}
    stack = [dependant] if dependant is not None else []
    while stack:
        node = stack.pop()
        if isinstance(node.call, WriteAccess):
            found.setdefault(id(node.call), node.call)
        stack.extend(node.dependencies)
    return list(found.values())


def write_routes(app: FastAPI) -> Iterator[WriteRoute]:
    """Every (method, path) of the app that writes, with what it declares.

    Walks the effective routes: since FastAPI 0.137 an included router stays
    one ``_IncludedRouter`` in ``app.routes``, and only its contexts carry the
    prefixed path and a dependency tree with the router's dependencies merged in.
    """
    for entry in app.routes:
        if hasattr(entry, "effective_route_contexts"):
            routes = [
                (ctx, ctx.original_route)
                for ctx in entry.effective_route_contexts()
                if isinstance(ctx.original_route, APIRoute)
            ]
        elif isinstance(entry, APIRoute):
            routes = [(entry, entry)]
        else:
            continue
        for route, original in routes:
            declared = tuple(sorted(d.name for d in _declarations(route.dependant)))
            endpoint = route.endpoint
            name = f"{endpoint.__module__}.{endpoint.__qualname__}"
            for method in sorted((route.methods or set()) & WRITE_METHODS):
                yield WriteRoute(method, route.path, name, declared, original)


def violations(app: FastAPI, frozen: frozenset[tuple[str, str]]) -> list[str]:
    """Write routes that declare nothing (and are not frozen) or declare twice."""
    found: list[str] = []
    for route in write_routes(app):
        if len(route.declared) > 1:
            found.append(
                f"{route.method} {route.path} ({route.endpoint}) declares "
                f"{' and '.join(route.declared)}; keep one"
            )
        elif not route.declared and (route.method, route.path) not in frozen:
            found.append(
                f"{route.method} {route.path} ({route.endpoint}) declares no write "
                "access; add CHEESE_ONLY_IN_ROOM, CHEESE_ONLY_IN_PROJECT or "
                "ROUTE_DECIDES (app/api/write_access.py) to its dependencies"
            )
    return found


#: Routes ``seal`` admitted, by identity of the object in ``scope["route"]``.
_SEALED: set[int] = set()


def seal(app: FastAPI) -> None:
    """Refuse to start with a write route nobody decided the callers of, and
    admit the rest for ``refuse_unsealed_writes``."""
    from app.api.write_access_baseline import UNDECLARED

    found = violations(app, UNDECLARED)
    if found:
        raise RuntimeError(
            "write routes without a write-access declaration:\n  " + "\n  ".join(found)
        )
    _SEALED.update(id(route.original) for route in write_routes(app))


async def refuse_unsealed_writes(connection: HTTPConnection) -> None:
    """App-wide: a write reaches its route only if ``seal`` admitted the route.

    Typed as the connection, not the request: an app-wide dependency runs for
    the WebSocket routes too, which carry no method and are never refused here.
    """
    method = connection.scope.get("method")
    if method in WRITE_METHODS and id(connection.scope.get("route")) not in _SEALED:
        raise ForbiddenError("write route without a write-access declaration")
