"""Device-held team model connections and their latest check and usage."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.owner_provider import OWNER_CHANNEL
from app.domain.agent_instance.models import PersonalModelResource
from app.domain.agent_session.services import AgentSessionService
from app.domain.device import claude_login
from app.domain.device.login_kind import FIRST_LOGIN
from app.domain.device.models import (
    DeviceClaudeLoginRow,
    DeviceRow,
    ModelCredentialHost,
)


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

    def shown(login: DeviceClaudeLoginRow, storage_team: int | None) -> bool:
        """Whether the viewer may see the computer a login is on: a team
        device's name is the team's, a member's own computer only its own."""
        return storage_team == team_id or (
            login.owner_user_id is not None and login.owner_user_id == viewer_user_id
        )

    def account_shown(login: DeviceClaudeLoginRow) -> bool:
        """Whether the viewer may see which Claude account a login is: a team's
        own subscription to the team, a member's personal one only to them,
        wherever it is stored or shared."""
        if login.owner_user_id is None:
            return login.team_id == team_id
        return login.owner_user_id == viewer_user_id

    return [
        {
            **claude_login.view(login),
            "owner_user_id": login.owner_user_id,
            "home_team_id": login.team_id,
            "allowed_models": login.allowed_models,
            "device_id": login.transport_id,
            "device_name": (name or host_name or login.transport_id)
            if shown(login, storage_team)
            else login.transport_id,
            "account_email": login.account_email if account_shown(login) else None,
            "account_org": login.account_org if account_shown(login) else None,
            "online": hub.is_online(login.transport_id),
            "last_used_at": used[login.transport_id].isoformat()
            if login.transport_id in used
            else None,
        }
        for login, name, storage_team, host_name in rows
    ]
