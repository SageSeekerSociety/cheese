"""A project's local MCP servers — a command each, run on the room's machine:
what a session's gateway asks before it starts one and says after, and the
owner of a member's computer answering whether one may run there.

The gateway's two requests carry the session's credential, like the remote
servers' proxy (`remote_mcp.py`). The answer is a person's, in their session.
"""

import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.auth import ActorResolverDep, session_in
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core import credentials
from app.core.errors import AuthenticationRequiredError, UnprocessableEntityError
from app.domain.agent_instance.services import type_of_seat
from app.domain.device import mcp_machines
from app.domain.remote_mcp import declared, local, service

router = APIRouter(tags=["local-mcp"])


class StartIn(BaseModel):
    #: The computer the session's server runs on, as its lease names it; None
    #: before it has one.
    device_id: str | None = Field(default=None, max_length=64)


@router.post(
    "/topics/{topic_id}/mcp/{name}/start",
    include_in_schema=False,
    dependencies=[ROUTE_DECIDES],
)
async def start(
    topic_id: uuid.UUID, name: str, body: StartIn, request: Request, db: DbSession
) -> dict:
    """What a local server starts with: the project secrets its default
    branch's definition names (``values``), and the commands the owner of the
    member's computer it runs on lets run there (``allowed``, None on the
    platform's machines)."""
    project_id, claims = await session_in(
        db, credentials.presented(request.headers), topic_id
    )
    found = await service.seat_declared(
        db, project_id, claims.get("a"), type_of_seat=type_of_seat
    )
    machine = (
        await mcp_machines.machine(db, project_id, body.device_id)
        if body.device_id
        else None
    )
    return ok(
        {
            "values": local.values(
                found.get_local(name), await service.secret_values(db, project_id)
            ),
            "allowed": await local.allowed(db, project_id, machine),
        }
    )


class RunIn(BaseModel):
    state: str = Field(max_length=16)
    log: list[str] = Field(default_factory=list, max_length=200)
    device_id: str | None = Field(default=None, max_length=64)
    #: A command waiting for its owner: its fingerprint, as the computer
    #: computed it, and the command as written.
    fingerprint: str | None = Field(default=None, max_length=64)
    command: str | None = Field(default=None, max_length=4000)


@router.post(
    "/topics/{topic_id}/mcp/{name}/run",
    include_in_schema=False,
    dependencies=[ROUTE_DECIDES],
)
async def run(
    topic_id: uuid.UUID, name: str, body: RunIn, request: Request, db: DbSession
) -> dict:
    """How a local server fared, as the session's gateway saw it."""
    project_id, claims = await session_in(
        db, credentials.presented(request.headers), topic_id
    )
    machine = (
        await mcp_machines.machine(db, project_id, body.device_id)
        if body.device_id
        else None
    )
    source, author = "repository", None
    if body.state == local.AWAITING:
        # Where the command is written and by whom, for the owner to read.
        found = await service.seat_declared(
            db, project_id, claims.get("a"), type_of_seat=type_of_seat
        )
        if name in found.added:
            source, author = "settings", await service.added_by(db, project_id, name)
        elif name in found.local_types:
            source = "type"
        else:
            author = await declared.last_author(db, project_id)
    try:
        state = await local.report(
            db,
            project_id,
            name,
            state=body.state,
            log=[line[:2000] for line in body.log],
            machine=machine,
            fingerprint=body.fingerprint,
            command=body.command,
            source=source,
            author=author,
        )
    except ValueError as exc:
        raise UnprocessableEntityError(str(exc)) from exc
    await db.commit()
    return ok({"state": state})


class DecideIn(BaseModel):
    allow: bool


class Decided(BaseModel):
    id: str
    allowed: bool


@router.post(
    "/mcp/approvals/{approval_id}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(Decided),
)
async def decide(
    approval_id: uuid.UUID, body: DecideIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The owner of a member's computer lets a command of a project's run on
    it, or not. Asked once per command; a changed command is asked again."""
    actor = await resolver.resolve()
    resolver.reject_failed_credential(actor)
    if not actor.authenticated:
        raise AuthenticationRequiredError()

    async def owns(device_id: str) -> bool:
        return device_id in await mcp_machines.owned(db, actor.handle)

    approval = await local.decide(
        db, approval_id, owner=actor.handle, owns=owns, allow=body.allow
    )
    await db.commit()
    return ok({"id": str(approval.id), "allowed": approval.allowed})
