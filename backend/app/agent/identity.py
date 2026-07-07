"""Agent identity as an *execution binding*, never a data column (知是 2.0).

A ``user_id`` is an agent iff it is the ``agent_user_id`` of a persisted agent
screen (``AgentScreenRow``) — i.e. it is (or was) driven by a device. The agent's
**owner** is the owner of the device running it. Live status (sid / device_id /
status / elapsed / tokens) comes from the hub's currently-online screens.

This module only *presents* the human-vs-agent distinction (avatars, forwarding
routing); business logic never branches on it.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.hub import DeviceHub
from app.agent.models import AgentScreenRow
from app.domain.device.models import DeviceRow
from app.domain.user.repositories import UserProfileRepository


@dataclass
class AgentIdentity:
    user_id: int
    sid: str | None = None
    device_id: str | None = None
    status: str | None = None
    elapsed: str | None = None
    tokens: str | None = None


async def resolve_agent_identities(
    session: AsyncSession, hub: DeviceHub, user_ids: list[int]
) -> dict[int, AgentIdentity]:
    """Which of ``user_ids`` are agents, with live status overlaid from the hub."""
    ids = [u for u in set(user_ids) if u]
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(AgentScreenRow.agent_user_id).where(AgentScreenRow.agent_user_id.in_(ids))
        )
    ).scalars()
    result: dict[int, AgentIdentity] = {uid: AgentIdentity(user_id=uid) for uid in set(rows)}
    for screen in hub.all_online_screens():
        ident = result.get(screen.agent_user_id)
        if ident is not None:
            ident.sid = screen.sid
            ident.device_id = screen.device_id
            ident.status = screen.vars.get("status")
            ident.elapsed = screen.vars.get("elapsed")
            ident.tokens = screen.vars.get("tokens")
    return result


async def agent_owner(session: AsyncSession, agent_user_id: int) -> int | None:
    """The human owner of the device running this agent user, or ``None`` if the
    user is not an agent."""
    row = (
        await session.execute(
            select(AgentScreenRow.device_id)
            .where(AgentScreenRow.agent_user_id == agent_user_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    device = await session.get(DeviceRow, row)
    return device.owner_user_id if device is not None else None


async def build_member_dicts(
    session: AsyncSession,
    hub: DeviceHub,
    user_ids: list[int],
    *,
    roles: dict[int, int] | None = None,
    read_watermarks: dict[int, int] | None = None,
) -> list[dict[str, object]]:
    """Resolve a list of ``user_id`` into ``Member`` / ``UserRef`` JSON dicts per the
    API contract: nickname/avatar_id from the user's profile, is_agent + live status
    from the execution binding."""
    profiles = await UserProfileRepository(session).get_profiles_by_user_ids(user_ids)
    agents = await resolve_agent_identities(session, hub, user_ids)
    out: list[dict[str, object]] = []
    for uid in user_ids:
        profile = profiles.get(uid)
        member: dict[str, object] = {
            "user_id": uid,
            "nickname": profile.nickname if profile is not None else f"user{uid}",
            "avatar_id": profile.avatar_id if profile is not None else None,
            "is_agent": uid in agents,
        }
        if roles is not None and uid in roles:
            member["role"] = roles[uid]
        if read_watermarks is not None:
            member["last_read_block_id"] = read_watermarks.get(uid, 0)
        ident = agents.get(uid)
        if ident is not None:
            member["agent_status"] = ident.status
            member["elapsed"] = ident.elapsed
            member["tokens"] = ident.tokens
            member["sid"] = ident.sid
            member["device_id"] = ident.device_id
        out.append(member)
    return out
