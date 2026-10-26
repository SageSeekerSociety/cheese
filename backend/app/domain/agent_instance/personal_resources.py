"""A member places a reference to their credential in another team."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent_instance.models import AccountConnection, PersonalModelResource
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.team import permissions as team_seats
from app.domain.team.permissions import Permission as TeamPermission
from app.domain.team.services import team_service


async def managed_login(db, team_id: int, user_id: int, connection_id: str):
    from app.domain.agent_instance.own.connections import logins_by_teams

    row = await credential(db, connection_id)
    if not isinstance(row, DeviceClaudeLoginRow):
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    visible = await logins_by_teams(db, {team_id})
    if (
        not any(c.id == connection_id for c in visible[team_id])
        and row.team_id != team_id
    ):
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    await manage(db, user_id, row)
    return row.transport_id


async def manage(db, user_id: int, row):
    if row.owner_user_id is not None:
        if row.owner_user_id != user_id:
            raise ForbiddenError(say("ownAgentConnectionNotFound"))
    elif not await team_seats.may(
        db, row.team_id, user_id, TeamPermission.manage_machines
    ):
        raise ForbiddenError(say("deviceTeamAdminOnly"))


async def credential(db, connection_id: str):
    row = await db.get(DeviceClaudeLoginRow, connection_id)
    if row is None:
        try:
            row = await db.get(AccountConnection, uuid.UUID(connection_id))
        except ValueError:
            pass
    if row is None:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    return row


def reference(row):
    return (
        {"login_id": row.id}
        if isinstance(row, DeviceClaudeLoginRow)
        else {"account_connection_id": row.id}
    )


async def put(db, team_id: int, user_id: int, connection_id: str):
    row = await credential(db, connection_id)
    if row.owner_user_id != user_id:
        raise ForbiddenError(say("ownAgentConnectionNotFound"))
    teams = team_service(db)
    if not await teams.is_team_member(
        team_id, user_id
    ) or not await teams.is_team_member(row.team_id, user_id):
        raise ForbiddenError(say("notTeamMember"))
    if row.team_id != team_id:
        await db.execute(
            insert(PersonalModelResource)
            .values(id=uuid.uuid4(), team_id=team_id, **reference(row))
            .on_conflict_do_nothing()
        )


async def remove(db, team_id: int, user_id: int, connection_id: str):
    row = await credential(db, connection_id)
    if row.owner_user_id is None:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    if row.owner_user_id != user_id and not await team_seats.may(
        db, team_id, user_id, TeamPermission.manage_machines
    ):
        raise ForbiddenError(say("deviceTeamAdminOnly"))
    field, value = next(iter(reference(row).items()))
    ref = await db.scalar(
        select(PersonalModelResource).where(
            PersonalModelResource.team_id == team_id,
            getattr(PersonalModelResource, field) == value,
        )
    )
    if ref is None:
        raise NotFoundError(say("ownAgentConnectionNotFound"))
    await db.delete(ref)
