"""Remote MCP servers: project settings, the OAuth callback, and the session proxy.

Any project member may connect, disconnect or set a value — the connection is
the project's, and the same standing applies to everyone. No route here returns
a token or a secret value.
"""

import json
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.app_return import back_in_app, started_in_app
from app.api.auth import ActorResolver, ActorResolverDep, require_seated_agent
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.config import settings
from app.core.errors import (
    AppError,
    AuthenticationRequiredError,
    BaseError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
    message_key,
)
from app.core.sandbox_auth import scoped_token_claims
from app.core.sentences import exception_text, say
from app.domain.conversation import services as conversations
from app.domain.membership.roster import roster
from app.domain.project.models import Project
from app.domain.remote_mcp import oauth, service, upstream
from app.domain.topic.services import TopicService

router = APIRouter(tags=["remote-mcp"])


async def member(
    db: AsyncSession, project_id: uuid.UUID, resolver: ActorResolver
) -> str:
    """The caller's handle, when they are a member of the project."""
    if await db.get(Project, project_id) is None:
        raise NotFoundError("Project not found")
    actor = await resolver.resolve(project_id=project_id)
    resolver.reject_failed_credential(actor)
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required")
    if not any(m.handle == actor.handle for m in await roster(db, project_id)):
        raise ForbiddenError(say("mcpManageMembersOnly"))
    # The project roster lists every teammate the project has, seated or not.
    await resolver.refuse_unseated_agent(actor, project_id=project_id)
    return actor.handle


@router.get("/projects/{project_id}/mcp/servers")
async def list_servers(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await member(db, project_id, resolver)
    return ok(await service.settings_view(db, project_id))


@router.post("/projects/{project_id}/mcp/servers/{name}/connect")
async def connect(
    project_id: uuid.UUID,
    name: str,
    request: Request,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    handle = await member(db, project_id, resolver)
    url = await service.begin_connect(
        db, project_id, name, handle, in_app=started_in_app(request)
    )
    return ok({"authorization_url": url})


@router.delete("/projects/{project_id}/mcp/servers/{name}/connection")
async def disconnect(
    project_id: uuid.UUID, name: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await member(db, project_id, resolver)
    await service.disconnect(db, project_id, name)
    return ok(None)


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=8192)


@router.put("/projects/{project_id}/mcp/secrets/{name}")
async def set_secret(
    project_id: uuid.UUID,
    name: str,
    body: SecretIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    handle = await member(db, project_id, resolver)
    await service.set_secret(db, project_id, name, body.value, handle)
    return ok(None)


@router.delete("/projects/{project_id}/mcp/secrets/{name}")
async def clear_secret(
    project_id: uuid.UUID, name: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await member(db, project_id, resolver)
    await service.clear_secret(db, project_id, name)
    return ok(None)


@router.get("/mcp/oauth/client.json")
async def client_metadata() -> dict:
    """The platform's Client ID Metadata Document. Served bare, not in the API
    envelope: the authorization server reads it as the client's registration."""
    return oauth.client_metadata()


def _failure(exc: AppError | BaseError) -> dict[str, str]:
    """Why connecting failed, as the query the settings page reads.

    The page words it from its catalog in its reader's language, so the URL
    carries the sentence's key (``mcp_error``) and its parameters as JSON
    (``mcp_error_params``), never the Chinese sentence. A refusal said in plain
    words has no key and goes out as ``failed``, which the page words as a
    failure without a reason."""
    said = message_key(exception_text(exc))
    if said is None:
        return {"mcp_error": "failed"}
    query = {"mcp_error": said["key"]}
    if said["params"]:
        query["mcp_error_params"] = json.dumps(said["params"], ensure_ascii=False)
    return query


@router.get("/mcp/oauth/callback")
async def callback(
    db: DbSession,
    state: str = "",
    code: str = "",
    error: str = "",
    iss: str | None = None,
) -> RedirectResponse:
    base = settings.frontend_url.rstrip("/")
    try:
        flow = service.read_state(state)
    except ValidationError as exc:
        return RedirectResponse(f"{base}/?{urlencode(_failure(exc))}", 302)
    back = f"{base}/projects/{flow['project']}/settings"
    query = {"mcp": flow["server"]}
    try:
        if error or not code:
            raise ValidationError(
                say("mcpAuthIncomplete", error=error or say("mcpNoAuthCode"))
            )
        await service.finish_connect(db, flow, code=code, issuer=iss)
    except (AppError, BaseError) as exc:
        query |= _failure(exc)
    else:
        query["mcp_result"] = "connected"
    landing = RedirectResponse(f"{back}?{urlencode(query)}#mcp", 302)
    return back_in_app(landing) if flow.get("app") else landing


class ProxyIn(BaseModel):
    method: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = {}


@router.post("/topics/{topic_id}/mcp/{name}", include_in_schema=False)
async def proxy(
    topic_id: uuid.UUID, name: str, body: ProxyIn, request: Request, db: DbSession
) -> dict:
    """A session's request to one of its project's remote servers. Authenticated
    by the room's credential; the credential for the server is attached here."""
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    if not claims or claims.get("t") != str(topic_id):
        raise AuthenticationRequiredError("A credential for this room is required")
    # The conversation the credential works: a room, or one of its tasks.
    project_id = await conversations.project_of(db, topic_id)
    if project_id is None:
        raise NotFoundError("Topic not found")
    if claims.get("p") != str(project_id):
        raise ForbiddenError("This room belongs to another project")
    await require_seated_agent(
        db,
        request.headers.get("x-cheese-token", ""),
        project_id=project_id,
        topic_id=topic_id,
    )
    try:
        result = await service.call(
            db,
            project_id=project_id,
            topic_id=topic_id,
            agent_handle=claims.get("a"),
            name=name,
            method=body.method,
            params=body.params,
        )
    except (AppError, BaseError) as exc:
        # Said to the agent as the tool's answer: what it can do next is the
        # same whether the server is unconnected or unknown.
        return ok({"error": str(exc)})
    except (upstream.UpstreamError, httpx.HTTPError) as exc:
        return ok({"error": f"{name} 这次没有答复：{exc}"})
    return ok({"result": result})


@router.get("/topics/{topic_id}/mcp/servers")
async def room_servers(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The room's read-only view: whose authorization its sessions act with."""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        project_id=place.project_id, topic_id=place.conversation_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError("Login required")
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    return ok({"servers": await service.room_view(db, place.project_id)})


#: The MCP resources methods a person's request may reach. Reads only: this path
#: exists to fetch a View's HTML, not to drive the server on anybody's behalf.
_RESOURCE_METHODS = frozenset({"resources/list", "resources/read"})


class ResourceIn(BaseModel):
    method: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = {}


@router.post(
    "/topics/{topic_id}/mcp/{name}/resources", dependencies=[ROUTE_DECIDES]
)
async def read_resource(
    topic_id: uuid.UUID,
    name: str,
    body: ResourceIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """A room MEMBER's request to one of the project's remote servers for a View.

    The MCP Apps read path (``ui://`` resources): a previewed third party's HTML
    is fetched under the person who is watching it. It deliberately does NOT use
    the room's agent credential the session proxy above (``proxy``) authenticates
    with — that credential is a session's, and reusing it here would let a
    previewed document act with an agent's standing. The server is one the
    project declares; the project's stored connection authorizes it here.
    """
    if body.method not in _RESOURCE_METHODS:
        raise UnprocessableEntityError(say("mcpResourcesMethodUnsupported"))
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        project_id=place.project_id, topic_id=place.conversation_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("signInFirst"))
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    try:
        # `agent_handle=None` reads the project's own declared servers (no seat's
        # type), which is exactly what a person watching a View may reach.
        result = await service.call(
            db,
            project_id=place.project_id,
            topic_id=place.conversation_id,
            agent_handle=None,
            name=name,
            method=body.method,
            params=body.params,
        )
    except BaseError as exc:
        raise UnprocessableEntityError(str(exc)) from None
    except (upstream.UpstreamError, httpx.HTTPError) as exc:
        raise UnprocessableEntityError(
            say("mcpServerUnreachable", error=f"{name}: {exc}")
        ) from None
    return ok({"result": result})
