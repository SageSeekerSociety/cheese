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
    _nickname,
    connection_of,
    owned_in_project,
    owned_instance,
)
from app.domain.agent_instance.own.settings import allows
from app.domain.device.models import DeviceRow
from app.domain.identity.handles import agent_instance_handle
from app.domain.user.models import User


async def listing(
    db: AsyncSession,
    project_id: uuid.UUID,
    viewer_user_id: int | None,
    is_online,
    project_settings: dict[str, Any] | None,
) -> list[dict]:
    """The members' own agents a project has: whose each one is, who may call
    it, and the model connection it runs on (None once that is gone) and
    whether the project allows that connection. Everyone
    in the project reads that much; the machine holding the connection, and
    whether it is online, only its owner reads (``machine`` is None for
    everyone else)."""

    rows = []
    for instance, owner_user_id, callers in await owned_in_project(db, project_id):
        if not instance.is_active:
            continue
        owned = await owned_instance(db, instance.id)
        login = await connection_of(db, owned) if owned is not None else None
        owner = await db.get(User, owner_user_id)
        mine = viewer_user_id is not None and viewer_user_id == owner_user_id
        machine = None
        if mine and owned is not None and owned.device_id is not None:
            device = await db.get(DeviceRow, owned.device_id)
            if device is not None:
                machine = {
                    "name": device.name,
                    "online": bool(is_online(device.device_id)),
                }
        rows.append(
            {
                "handle": agent_instance_handle(instance.id),
                "name": instance.display_name,
                "callers": callers.value,
                "owner_handle": owner.username if owner else None,
                "owner_name": await _nickname(db, owner.username) if owner else None,
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
        _connection_key(device_id, account_id)
        for device_id, account_id in (
            await db.execute(
                select(OwnAgent.device_id, OwnAgent.account_connection_id)
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
