"""Who may call a write route, declared on the route itself.

Every POST / PUT / PATCH / DELETE route carries exactly one ``WriteAccess`` in
its dependencies. The instance is both the declaration (``seal`` finds it by
walking the route's dependency tree) and the check (FastAPI runs it):

- ``Can(permission)``: the caller may do ``permission`` to the object the
  path names (``app.auth.can``); the parameter it annotates receives that
  object. Checked before the handler runs; reads use it too, though only
  writes must carry a declaration. A team, a space, a problem or a question
  is acted on by a person (or their 芝士, as them); a project by whoever
  works in it — a person, their 芝士, or one of its AI teammates — and the
  parameter receives the project with who acts (``InProject``); asked at a
  conversation (``at="topic_id"``), by whoever may be in that channel, and the
  parameter receives the conversation's place with who acts (``InChannel``).
  What only an AI teammate does there — the write surface the ``cheese`` CLI
  alone calls — is a permission no person's role holds
  (``project_permissions.AGENT_ONLY``).
- ``ROUTE_DECIDES``: everyone reaches the handler, which authorizes the caller
  itself (``ActorResolver`` and friends). The routes written before ``Can``
  judged every caller still declare it and move onto ``Can`` a batch at a
  time; a route added now may not (``scripts/check_route_decides.py``).

A router may declare for all its routes
(``APIRouter(dependencies=[ROUTE_DECIDES])``); a route may not declare twice.

Two refusals stand behind a route that declares nothing. ``seal`` runs once the
routes are mounted and refuses to start the app. ``refuse_unsealed_writes`` is
an app-wide dependency that answers 403 to a write whose route ``seal`` did not
admit, so a route object created after ``seal``, or an app nobody sealed, is
closed rather than open. (A router already sealed and included again under
another prefix shares its route objects, and so stays admitted.)

A route that is renamed or moved carries its declaration along. The gate this
replaced was a table of path regexes kept apart from the routes; #370 flattened
a prefix, every regex stopped matching, and the surface only 芝士 may call
opened without a single failure.
"""

from __future__ import annotations

import enum
import inspect
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Path
from fastapi.dependencies.models import Dependant
from fastapi.requests import HTTPConnection
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, get_actor_resolver
from app.auth import can
from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.auth.project_roles import AGENT_PERMISSIONS, may
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.project.models import Project
from app.domain.project.services import ProjectService
from app.domain.project_permissions import Permission as ProjectPermission
from app.domain.room_task.place import Place, place_or_404

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, eq=False)
class WriteAccess:
    """One declaration: which callers reach the handler."""

    kind: Literal["route_decides", "can"]

    @property
    def name(self) -> str:
        return self.kind

    async def __call__(self, connection: HTTPConnection) -> None:
        """``route_decides``: nothing to check here; the handler does.

        Typed as the connection, not the request, so a router that declares
        for all its routes keeps its WebSocket routes working.
        """


@dataclass(frozen=True, eq=False)
class _Can(WriteAccess):
    kind: Literal["can"] = "can"
    permission: enum.StrEnum | None = None
    #: The path parameter that names the object; the policy's own unless a
    #: route spells it otherwise.
    at: str = ""

    def __post_init__(self) -> None:
        # The object's id is declared as a path parameter of this dependency,
        # under the name the path gives it, so the route's API description
        # keeps it and FastAPI validates it — a route that takes the object
        # rather than its id would otherwise drop it from both.
        object.__setattr__(
            self,
            "__signature__",
            inspect.Signature(
                [
                    inspect.Parameter(
                        "object_id",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[int, Path(alias=self.at)],
                    ),
                    inspect.Parameter(
                        "db",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[AsyncSession, Depends(get_db)],
                    ),
                    inspect.Parameter(
                        "person",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[AuthUserInfo, Depends(get_auth_user)],
                    ),
                ]
            ),
        )

    @property
    def name(self) -> str:
        return f"can {self.permission}"

    async def __call__(  # type: ignore[override]
        self, *, object_id: int, db: AsyncSession, person: AuthUserInfo
    ) -> Any:
        if person.user_id == 0:
            raise AuthenticationRequiredError(say("signInRequired"))
        assert self.permission is not None
        return await can.authorize(db, person.user_id, self.permission, object_id)


@dataclass(frozen=True)
class InProject:
    """A project a route acts in, and who acts there: a person, their 芝士 as
    them, or one of the project's AI teammates."""

    project: Project
    actor: Actor


@dataclass(frozen=True, eq=False)
class _CanInProject(WriteAccess):
    """``Can`` for a project's permission (``project_permissions``): whoever
    the request's credential names — a person, their 芝士, an AI teammate's
    room or project credential — judged by where they sit in the project
    (``ActorResolver.authorize_project``, ``project_roles.standing``)."""

    kind: Literal["can"] = "can"
    permission: ProjectPermission | None = None
    at: str = "project_id"

    def __post_init__(self) -> None:
        # The project's id is a path parameter of this dependency, under the
        # path's own name, as `_Can` declares its object's.
        object.__setattr__(
            self,
            "__signature__",
            inspect.Signature(
                [
                    inspect.Parameter(
                        "project_id",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[uuid.UUID, Path(alias=self.at)],
                    ),
                    inspect.Parameter(
                        "db",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[AsyncSession, Depends(get_db)],
                    ),
                    inspect.Parameter(
                        "resolver",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[
                            ActorResolver, Depends(get_actor_resolver)
                        ],
                    ),
                ]
            ),
        )

    @property
    def name(self) -> str:
        return f"can {self.permission}"

    async def __call__(  # type: ignore[override]
        self, *, project_id: uuid.UUID, db: AsyncSession, resolver: ActorResolver
    ) -> InProject:
        assert self.permission is not None
        actor = await resolver.resolve(project_id=project_id)
        await resolver.authorize_project(
            actor, project_id=project_id, permission=self.permission
        )
        return InProject(await ProjectService(db).get_or_404(project_id), actor)


@dataclass(frozen=True)
class InChannel:
    """A conversation a route acts in — a channel, or one of its tasks or 支线
    (``Place``) — and who acts there."""

    place: Place
    actor: Actor


@dataclass(frozen=True, eq=False)
class _CanInChannel(WriteAccess):
    """``Can`` for a project's permission asked in one conversation: whoever
    may be in its channel (``ActorResolver.authorize_topic``: a private
    channel is not there for anyone outside it), and may do the permission
    where they sit in the project — an AI teammate by what its role grants
    (``project_roles.AGENT_PERMISSIONS``), as everyone else by theirs."""

    kind: Literal["can"] = "can"
    permission: ProjectPermission | None = None
    at: str = "topic_id"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "__signature__",
            inspect.Signature(
                [
                    inspect.Parameter(
                        "topic_id",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[uuid.UUID, Path(alias=self.at)],
                    ),
                    inspect.Parameter(
                        "db",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[AsyncSession, Depends(get_db)],
                    ),
                    inspect.Parameter(
                        "resolver",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[
                            ActorResolver, Depends(get_actor_resolver)
                        ],
                    ),
                ]
            ),
        )

    @property
    def name(self) -> str:
        return f"can {self.permission}"

    async def __call__(  # type: ignore[override]
        self, *, topic_id: uuid.UUID, db: AsyncSession, resolver: ActorResolver
    ) -> InChannel:
        assert self.permission is not None
        place = await place_or_404(db, topic_id)
        actor = await resolver.resolve(
            topic_id=place.conversation_id, project_id=place.project_id
        )
        await resolver.authorize_topic(
            actor,
            project_id=place.project_id,
            topic_id=place.room_id,
            room=place.room,
        )
        if not await self._may(db, place, actor):
            raise ForbiddenError(say("projectPermissionDenied"))
        return InChannel(place, actor)

    async def _may(self, db: AsyncSession, place: Place, actor: Actor) -> bool:
        assert self.permission is not None
        # Nobody signed in gets past the room only on the development override,
        # which sits nowhere and is asked nothing more.
        if not actor.authenticated:
            return True
        # An AI teammate the room just let in sits in it by its seat there, not
        # by a row on the project's roster, which a project's own teammates do
        # not have: what it may do is what its role grants.
        if actor.via == "cheese":
            return self.permission in AGENT_PERMISSIONS
        return await may(db, place.project_id, actor.handle, self.permission)


def Can(permission: enum.StrEnum, *, at: str | None = None) -> Any:
    """What a route needs, on the object its path names: the parameter it
    annotates receives that object, loaded and authorized
    (``team: Annotated[Team, Can(TeamPermission.edit_settings)]``), or for a
    project the project and who acts in it
    (``at: Annotated[InProject, Can(ProjectPermission.configure_agents)]``).
    It is the route's write-access declaration too. ``at`` names the path
    parameter only where a route spells it otherwise than the object's policy
    does."""
    if isinstance(permission, ProjectPermission):
        if at is not None and at != "project_id":
            return Depends(_CanInChannel(permission=permission, at=at))
        return Depends(_CanInProject(permission=permission, at="project_id"))
    param = at or can.policy_of(permission).path_param
    return Depends(_Can(permission=permission, at=param))


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
        contexts = getattr(entry, "effective_route_contexts", None)
        if contexts is not None:
            routes = [
                (ctx, ctx.original_route)
                for ctx in contexts()
                if isinstance(ctx.original_route, APIRoute)
            ]
        elif isinstance(entry, APIRoute):
            routes = [(entry, entry)]
        else:
            continue
        for route, original in routes:
            dependant = getattr(route, "dependant", None)
            declared = tuple(sorted(d.name for d in _declarations(dependant)))
            endpoint = route.endpoint
            name = f"{endpoint.__module__}.{endpoint.__qualname__}"
            for method in sorted((route.methods or set()) & WRITE_METHODS):
                yield WriteRoute(method, route.path, name, declared, original)


def violations(app: FastAPI) -> list[str]:
    """Write routes that declare nothing or declare twice."""
    found: list[str] = []
    for route in write_routes(app):
        if len(route.declared) > 1:
            found.append(
                f"{route.method} {route.path} ({route.endpoint}) declares "
                f"{' and '.join(route.declared)}; keep one"
            )
        elif not route.declared:
            found.append(
                f"{route.method} {route.path} ({route.endpoint}) declares no write "
                "access; add Can(...) or ROUTE_DECIDES (app/api/write_access.py) "
                "to its dependencies"
            )
    return found


#: Routes ``seal`` admitted, by identity of the object in ``scope["route"]``.
#: The value holds the route so its id cannot be reused by a later object.
_SEALED: dict[int, APIRoute] = {}


def seal(app: FastAPI) -> None:
    """Refuse to start with a write route nobody decided the callers of, and
    admit the rest for ``refuse_unsealed_writes``."""
    found = violations(app)
    if found:
        raise RuntimeError(
            "write routes without a write-access declaration:\n  " + "\n  ".join(found)
        )
    _SEALED.update((id(r.original), r.original) for r in write_routes(app))


async def refuse_unsealed_writes(connection: HTTPConnection) -> None:
    """App-wide: a write reaches its route only if ``seal`` admitted the route.

    Typed as the connection, not the request: an app-wide dependency runs for
    the WebSocket routes too, which carry no method and are never refused here.
    """
    method = connection.scope.get("method")
    if method in WRITE_METHODS and id(connection.scope.get("route")) not in _SEALED:
        raise ForbiddenError("write route without a write-access declaration")
