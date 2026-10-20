"""Project teammates and available team connections."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_instance.own.connections import _connection_key, logins
from app.domain.agent_instance.own.ownership import (
    connections_of,
    owned_rows_in_project,
)
from app.domain.project.models import Project
from app.domain.identity.handles import agent_instance_handle
from app.domain.user.models import User
from app.domain.user.services import faces_by_handle


async def listing(
    db: AsyncSession,
    project_id: uuid.UUID,
    viewer_user_id: int | None,
    is_online,
    project_settings: dict[str, Any] | None,
) -> list[dict]:
    """Project readers see its teammates, team connections and credential devices."""

    active = [
        (instance, owned)
        for instance, owned in await owned_rows_in_project(db, project_id)
        if instance.is_active
    ]
    if not active:
        return []
    connections = await connections_of(db, [owned for _, owned in active])
    # Unlike the user repository's live-user reads, this preserves the handle
    # of a soft-deleted owner, as the former primary-key lookup did.
    owners = {
        user.id: user
        for user in await db.scalars(
            select(User).where(
                User.id.in_({owned.owner_user_id for _, owned in active})
            )
        )
    }
    faces = await faces_by_handle(db, [owner.username for owner in owners.values()])
    project = await db.get(Project, project_id)
    rows = []
    for instance, owned in active:
        login = connections[instance.id]
        owner = (
            owners.get(owned.owner_user_id)
            if login is None or login.owner_user_id is not None
            else None
        )
        machine = None
        if login is not None and owned.device_id is not None and project is not None:
            machine = {
                "name": login.storage_name(project.team_id, viewer_user_id),
                "online": bool(is_online(owned.device_id)),
            }
        rows.append(
            {
                "handle": agent_instance_handle(instance.id),
                "name": instance.display_name,
                "model": (instance.configuration or {}).get("model"),
                "device_id": (instance.configuration or {}).get("execution_device_id"),
                "owner_handle": owner.username if owner else None,
                "owner_name": (
                    faces.get(owner.username, (None, None))[0] or owner.username
                )
                if owner
                else None,
                "connection": {
                    "kind": login.kind.value,
                    "plan": login.plan,
                    "model": login.model,
                    "on_account": login.on_account,
                    "allowed_models": list(login.allowed_models),
                }
                if login is not None
                else None,
                "machine": machine,
            }
        )
    return rows


async def my_connections(
    db: AsyncSession,
    project_id: uuid.UUID,
    viewer_user_id: int | None,
    project_settings: dict[str, Any] | None,
    is_online,
) -> list[dict]:
    """Team connections available for this project, including existing uses."""
    here = {
        _connection_key(login_id, account_id)
        for login_id, account_id in (
            await db.execute(
                select(OwnAgent.login_id, OwnAgent.account_connection_id)
                .join(AgentInstance, AgentInstance.id == OwnAgent.instance_id)
                .where(
                    AgentInstance.project_id == project_id,
                    AgentInstance.is_active.is_(True),
                )
            )
        ).all()
    }
    project = await db.get(Project, project_id)
    if project is None:
        return []
    return [
        {
            "id": login.id,
            "kind": login.kind.value,
            "plan": login.plan,
            "model": login.model,
            "name": login.name,
            "service_host": login.service_host,
            "on_account": login.on_account,
            "machine_name": login.storage_name(project.team_id, viewer_user_id),
            "owner_user_id": login.owner_user_id,
            "online": login.on_account or bool(is_online(login.device_id)),
            "added": login.id in here,
            "allowed_models": list(login.allowed_models),
        }
        for login in await logins(db, project.team_id)
        if login.owner_user_id is None or login.owner_user_id == viewer_user_id
    ]
