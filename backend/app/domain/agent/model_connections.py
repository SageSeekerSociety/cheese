"""Device-held team model connections and their latest check and usage."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.owner_provider import OWNER_CHANNEL
from app.domain.agent_session.services import AgentSessionService
from app.domain.device import claude_login
from app.domain.device.login_kind import FIRST_LOGIN
from app.domain.device.models import (
    DeviceClaudeLoginRow,
    DeviceRow,
    ModelCredentialHost,
)
from app.domain.agent_instance.models import PersonalModelResource


async def of_team(
    db: AsyncSession, team_id: int, hub, viewer_user_id: int | None = None
) -> list[dict]:
    """The team's device logins, the most recently confirmed first."""
    rows = (
        await db.execute(
            select(
                DeviceClaudeLoginRow,
                DeviceRow.name,
                DeviceRow.team_id,
                ModelCredentialHost.name,
            )
            .outerjoin(DeviceRow, DeviceRow.device_id == DeviceClaudeLoginRow.device_id)
            .outerjoin(
                ModelCredentialHost,
                ModelCredentialHost.id == DeviceClaudeLoginRow.host_id,
            )
            .where(
                or_(
                    DeviceClaudeLoginRow.team_id == team_id,
                    DeviceClaudeLoginRow.id.in_(
                        select(PersonalModelResource.login_id).where(
                            PersonalModelResource.team_id == team_id
                        )
                    ),
                ),
                or_(
                    DeviceClaudeLoginRow.logged_in.is_(True),
                    DeviceClaudeLoginRow.revoked_at.is_not(None),
                ),
            )
            # One machine's logins are confirmed in one answer: the first
            # login first, then the same order on every read.
            .order_by(
                DeviceClaudeLoginRow.checked_at.desc(),
                DeviceClaudeLoginRow.local_id != FIRST_LOGIN,
                DeviceClaudeLoginRow.local_id,
            )
        )
    ).all()
    used = await AgentSessionService(db).last_used_on(
        OWNER_CHANNEL, [row.transport_id for row, *_ in rows]
    )
    return [
        {
            **claude_login.view(login),
            "owner_user_id": login.owner_user_id,
            "home_team_id": login.team_id,
            "allowed_models": login.allowed_models,
            "device_id": login.transport_id,
            "device_name": (name or host_name or login.transport_id)
            if storage_team == team_id
            or (
                login.owner_user_id is not None
                and login.owner_user_id == viewer_user_id
            )
            else login.transport_id,
            "online": hub.is_online(login.transport_id),
            "last_used_at": used[login.transport_id].isoformat()
            if login.transport_id in used
            else None,
        }
        for login, name, storage_team, host_name in rows
    ]
