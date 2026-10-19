"""Device-held team model connections and their latest check and usage."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.owner_provider import OWNER_CHANNEL
from app.domain.agent_session.services import AgentSessionService
from app.domain.device import claude_login
from app.domain.device.login_kind import FIRST_LOGIN
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow, HostedDeviceRow
from app.domain.device.supply import Supply


async def of_team(db: AsyncSession, team_id: int, hub) -> list[dict]:
    """The team's device logins, the most recently confirmed first."""
    rows = (
        await db.execute(
            select(DeviceRow.device_id, DeviceRow.name, DeviceClaudeLoginRow)
            .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
            .join(
                DeviceClaudeLoginRow,
                DeviceClaudeLoginRow.device_id == DeviceRow.device_id,
            )
            .where(
                DeviceRow.team_id == team_id,
                DeviceRow.supply == Supply.self_hosted,
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
        OWNER_CHANNEL, [device_id for device_id, _, _ in rows]
    )
    return [
        {
            **claude_login.view(login),
            "allowed_models": login.allowed_models,
            "device_id": device_id,
            "device_name": name,
            "online": hub.is_online(device_id),
            "last_used_at": used[device_id].isoformat() if device_id in used else None,
        }
        for device_id, name, login in rows
    ]
