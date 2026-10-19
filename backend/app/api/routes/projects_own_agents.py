"""Instantiate team model connections as project teammates (#3717)."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.device_hub import device_hub
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.model_catalog import model_choices
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.own import (
    add,
    listing,
    my_connections,
    owned_by_seat,
    remove,
)
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.device.wiring import sql_device_service
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.services import ProjectService
from app.domain.project_permissions import Permission
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.services import UsageService

router = APIRouter(prefix="/projects", tags=["projects"])


class OwnAgentAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=64)


class OwnAgentMachineOut(BaseModel):
    name: str
    online: bool


class OwnAgentConnectionOut(BaseModel):
    kind: str
    plan: str | None
    model: str | None
    on_account: bool
    allowed_models: list[str]


class OwnAgentOut(BaseModel):
    handle: str
    name: str | None
    owner_handle: str | None
    owner_name: str | None
    connection: OwnAgentConnectionOut | None
    machine: OwnAgentMachineOut | None
    model: str | None
    device_id: str | None


class MyConnectionOut(BaseModel):
    id: str
    kind: str
    plan: str | None = None
    model: str | None = None
    name: str | None = None
    service_host: str | None = None
    on_account: bool = True
    machine_name: str | None = None
    online: bool = True
    added: bool = False
    allowed_models: list[str]


class ExecutionDeviceOut(BaseModel):
    device_id: str
    name: str
    online: bool


class OwnAgentsOut(BaseModel):
    can_manage: bool
    agents: list[OwnAgentOut]
    my_connections: list[MyConnectionOut]
    devices: list[ExecutionDeviceOut]


@router.get("/{project_id}/own-agents", **typed_response(OwnAgentsOut))
async def get_own_agents(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    can_manage = True
    try:
        await resolver.authorize_project(
            actor, project_id=project_id, permission=Permission.configure_agents
        )
    except ForbiddenError:
        can_manage = False
    devices = await sql_device_service(db).list_devices_for_project(project_id)
    refusing = await sql_device_service(db).refusing_sandboxes(
        [d.device_id for d in devices]
    )
    choices = await my_connections(
        db, project_id, actor.user_id, project.settings, device_hub.is_online
    )
    access = await UsageService(db).model_access(project.team_id)
    choices.insert(
        0,
        {
            "id": "platform",
            "kind": "platform",
            "name": None,
            "allowed_models": [
                m["id"]
                for m in model_choices(project.settings)
                if access.shows(m) and access.allows(m)
            ],
        },
    )
    return ok(
        {
            "can_manage": can_manage,
            "agents": await listing(
                db, project_id, actor.user_id, device_hub.is_online, project.settings
            ),
            "my_connections": choices,
            "devices": [
                {
                    "device_id": d.device_id,
                    "name": d.name,
                    "online": device_hub.is_online(d.device_id),
                }
                for d in devices
                if d.device_id not in refusing
            ],
        }
    )


@router.post(
    "/{project_id}/own-agents",
    dependencies=[ROUTE_DECIDES],
    **typed_response(OwnAgentsOut),
)
async def add_own_agent(
    project_id: uuid.UUID, body: OwnAgentAdd, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(
        actor, project_id=project_id, permission=Permission.configure_agents
    )
    if actor.user_id is None:
        raise ForbiddenError(say("ownAgentAddPersonOnly"))
    project = await ProjectService(db).get_or_404(project_id)
    devices = sql_device_service(db)
    device = await devices.get_hosted_device(body.device_id)
    if device is None or not await devices.serves_project(body.device_id, project_id):
        raise NotFoundError(say("deviceNotInProject"))
    if body.device_id in await devices.refusing_sandboxes([body.device_id]):
        raise ForbiddenError(say("environmentSpecNotOffered"))
    if body.connection_id == "platform":
        await AgentInstanceService(db, models=model_choices).create(
            project_id=project_id,
            handle=f"agent-{uuid.uuid4().hex[:8]}",
            type_name=None,
            display_name=body.model,
            configuration=AgentConfiguration(
                model=body.model, execution_device_id=body.device_id
            ),
        )
    else:
        await add(
            db,
            project,
            actor.user_id,
            body.connection_id,
            CLAUDE_CODE,
            model=body.model,
            device_id=body.device_id,
        )
    await db.commit()
    return await get_own_agents(project_id, db, resolver)


@router.delete(
    "/{project_id}/own-agents/{handle}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(OwnAgentsOut),
)
async def remove_own_agent(
    project_id: uuid.UUID, handle: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(
        actor, project_id=project_id, permission=Permission.configure_agents
    )
    owned = await owned_by_seat(db, project_id, handle)
    if owned is None:
        raise NotFoundError(say("ownAgentNotFound"))
    await remove(db, owned.instance_id)
    await TopicMemberService(db).revoke_project_seats(
        project_id=project_id, member_handle=agent_instance_handle(owned.instance_id)
    )
    await db.commit()
    return await get_own_agents(project_id, db, resolver)
