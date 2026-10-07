"""Who may call a write route, declared on the route itself.

Every POST / PUT / PATCH / DELETE route says which callers the platform lets
through to it, by carrying exactly one of these in its ``dependencies``:

- ``CHEESE_ONLY_IN_ROOM`` / ``CHEESE_ONLY_IN_PROJECT``: only a 芝士 credential
  for the room (``topic_id``) or project (``project_id``) in the path. A
  person's browser session is refused. Used for the write surface the ``cheese``
  CLI alone calls.
- ``ROUTE_DECIDES``: everyone reaches the handler, which authorizes the caller
  itself (``ActorResolver`` and friends). Most routes are this.

A router may carry the declaration for all its routes
(``APIRouter(dependencies=[ROUTE_DECIDES])``); a route may not carry two.

``seal`` runs once the routes are mounted and refuses to start an app that has
a write route with no declaration, unless the route is in the frozen list of
undeclared routes that predate this module (``write_access_baseline``). That
list only shrinks. A route that is renamed or moved leaves the list and has to
be declared, so a rename can no longer open a route the way #370 did, when the
gate was a table of path regexes kept apart from the routes and every one of
them stopped matching at once.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.params import Depends as DependsParam
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.core.db import get_db
from app.core.errors import BaseError, UnauthorizedError
from app.core.sandbox_auth import (
    is_global_sandbox_token,
    is_valid_cheese_token,
    looks_like_project_agent_credential,
    scoped_token_claims,
)
from app.domain.agent_credential.services import ProjectAgentCredentialService

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class _CheeseOnly:
    """The gate in front of a 芝士-only route: a credential for the room or
    project the path names, or the request goes no further."""

    scope: Literal["topic", "project"]

    async def __call__(
        self, request: Request, db: Annotated[AsyncSession, Depends(get_db)]
    ) -> None:
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


async def _route_decides() -> None:
    """Declaration only: the handler authorizes its caller."""


CHEESE_ONLY_IN_ROOM = Depends(_CheeseOnly("topic"))
CHEESE_ONLY_IN_PROJECT = Depends(_CheeseOnly("project"))
ROUTE_DECIDES = Depends(_route_decides)

_DECLARATIONS = {
    CHEESE_ONLY_IN_ROOM.dependency: "cheese_only_in_room",
    CHEESE_ONLY_IN_PROJECT.dependency: "cheese_only_in_project",
    ROUTE_DECIDES.dependency: "route_decides",
}


@dataclass(frozen=True)
class WriteRoute:
    method: str
    path: str
    endpoint: str
    declared: tuple[str, ...]


def write_routes(app: FastAPI) -> Iterator[WriteRoute]:
    """Every (method, path) of the app that writes, with what it declares.

    Walks the effective routes: since FastAPI 0.137 an included router stays
    one ``_IncludedRouter`` in ``app.routes``, and only its contexts carry the
    prefixed path and the router's dependencies merged with the route's.
    """
    for entry in app.routes:
        if hasattr(entry, "effective_route_contexts"):
            routes = [
                ctx
                for ctx in entry.effective_route_contexts()
                if isinstance(ctx.original_route, APIRoute)
            ]
        elif isinstance(entry, APIRoute):
            routes = [entry]
        else:
            continue
        for route in routes:
            declared = tuple(
                _DECLARATIONS[dep.dependency]
                for dep in route.dependencies
                if isinstance(dep, DependsParam) and dep.dependency in _DECLARATIONS
            )
            endpoint = route.endpoint
            name = f"{endpoint.__module__}.{endpoint.__qualname__}"
            for method in sorted((route.methods or set()) & WRITE_METHODS):
                yield WriteRoute(method, route.path, name, declared)


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


def seal(app: FastAPI) -> None:
    """Refuse to start with a write route nobody decided the callers of."""
    from app.api.write_access_baseline import UNDECLARED

    found = violations(app, UNDECLARED)
    if found:
        raise RuntimeError(
            "write routes without a write-access declaration:\n  " + "\n  ".join(found)
        )
