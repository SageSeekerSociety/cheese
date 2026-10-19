"""What the API lists: a project's own agents, and a reader's own connections.

Both are reads over what the rest of this package already answers — everyone in
the project sees whose each agent is and what it runs on; the machine it is on,
and whether that machine is online, only its owner does.
"""

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
from app.domain.agent_instance.own.settings import allows
from app.domain.device.mcp_machines import names as device_names
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
    """The members' own agents a project has: whose each one is, and the
    model connection it runs on (None once that is gone) and
    whether the project allows that connection. Everyone
    in the project reads that much; the machine holding the connection, and
    whether it is online, only its owner reads (``machine`` is None for
    everyone else)."""

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
    machines = await device_names(
        db,
        [
            owned.device_id
            for _, owned in active
            if viewer_user_id == owned.owner_user_id and owned.device_id is not None
        ],
    )
    rows = []
    for instance, owned in active:
        login = connections[instance.id]
        owner = owners.get(owned.owner_user_id)
        mine = viewer_user_id is not None and viewer_user_id == owned.owner_user_id
        machine = None
        if mine and owned.device_id is not None and owned.device_id in machines:
            machine = {
                "name": machines[owned.device_id],
                "online": bool(is_online(owned.device_id)),
            }
        rows.append(
            {
                "handle": agent_instance_handle(instance.id),
                "name": instance.display_name,
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
                    "allowed": allows(login, project_settings),
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
    viewer_user_id: int,
    project_settings: dict[str, Any] | None,
    is_online,
) -> list[dict]:
    """The reader's own connections, for them to add to this project: each
    with whether it is already here and whether the project allows its kind."""
    here = {
        _connection_key(login_id, account_id)
        for login_id, account_id in (
            await db.execute(
                select(OwnAgent.login_id, OwnAgent.account_connection_id)
                .join(AgentInstance, AgentInstance.id == OwnAgent.instance_id)
                .where(
                    AgentInstance.project_id == project_id,
                    AgentInstance.is_active.is_(True),
                    OwnAgent.owner_user_id == viewer_user_id,
                )
            )
        ).all()
    }
    return [
        {
            "id": login.id,
            "kind": login.kind.value,
            "plan": login.plan,
            "model": login.model,
            "name": login.name,
            "service_host": login.service_host,
            "on_account": login.on_account,
            "machine_name": login.device_name,
            # One the account holds runs on the platform's session host.
            "online": login.on_account or bool(is_online(login.device_id)),
            "added": login.id in here,
            "allowed": allows(login, project_settings),
        }
        for login in await logins(db, viewer_user_id)
    ]
