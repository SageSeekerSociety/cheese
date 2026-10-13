"""A person's model connections (#2991): every login their own Claude Code has
for the platform, across the machines they enrolled, for 「设置 → 模型连接」.

A connection is what a machine last said about its login (`owner_login`): a
Claude subscription and its plan, an Anthropic API key, or another model
service with its model and host — never a key, which stays on the machine.
Each comes with the machine it is kept on, whether that machine is online, and
when a session of the owner's Claude Code last ran there.

Only its owner reads this. A machine that is not logged in has no connection,
unless its owner removed one while it was away and it has not logged out yet.
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.owner_provider import OWNER_CHANNEL
from app.domain.agent_session.services import AgentSessionService
from app.domain.device import claude_login
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow, HostedDeviceRow
from app.domain.device.supply import Supply


async def of_owner(db: AsyncSession, owner_user_id: int, hub) -> list[dict]:
    """The owner's connections, the most recently confirmed first."""
    rows = (
        await db.execute(
            select(DeviceRow.device_id, DeviceRow.name, DeviceClaudeLoginRow)
            .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
            .join(
                DeviceClaudeLoginRow,
                DeviceClaudeLoginRow.device_id == DeviceRow.device_id,
            )
            .where(
                DeviceRow.owner_user_id == owner_user_id,
                DeviceRow.supply == Supply.self_hosted,
                or_(
                    DeviceClaudeLoginRow.logged_in.is_(True),
                    DeviceClaudeLoginRow.revoked_at.is_not(None),
                ),
            )
            .order_by(DeviceClaudeLoginRow.checked_at.desc())
        )
    ).all()
    used = await AgentSessionService(db).last_used_on(
        OWNER_CHANNEL, [device_id for device_id, _, _ in rows]
    )
    return [
        {
            **claude_login.view(login),
            "device_id": device_id,
            "device_name": name,
            "online": hub.is_online(device_id),
            "last_used_at": used[device_id].isoformat() if device_id in used else None,
        }
        for device_id, name, login in rows
    ]
