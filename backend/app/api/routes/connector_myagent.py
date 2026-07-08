"""「我的 Agent」页 — the owner-facing device + agent management under ``/connector``.

Project-agnostic: a human manages the devices they own and the agents running on
them. Agents are project-independent (project is a future wrapper) — creating one needs
no project; if the device happens to be assigned to one, the agent is linked to it.
There is no create-device endpoint: devices enroll via the device flow (``install.sh``
+ ``cheese link auto-connect``).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.hub import DeviceHub
from app.agent.identity import agent_owner, build_member_dicts
from app.agent.models import AgentScreenRow
from app.agent.orchestrator import AgentService
from app.common.auth import get_current_user_id
from app.core.errors import ForbiddenError, NotFoundError, PreconditionFailedError
from app.domain.device.service import DeviceService
from app.domain.user.repositories import UserProfileRepository


class CreateAgentBody(BaseModel):
    device_id: str
    nickname: str | None = None
    avatar_id: int | None = None
    # 「复制自」: when set, fork this existing agent's Claude conversation onto the new
    # agent (copy its transcript to device_id and resume it) instead of starting fresh.
    copy_from_agent_user_id: int | None = None
    # 「复制自」时可选的目标工作目录：不填则沿用源 agent 的 cwd。目录在目标机上不存在时
    # 后端会 `mkdir -p` 建出来，不会阻断创建。
    target_cwd: str | None = None


class UpdateAgentBody(BaseModel):
    nickname: str | None = None
    avatar_id: int | None = None


class RenameDeviceBody(BaseModel):
    name: str


class RecreateAgentBody(BaseModel):
    resume: bool = True  # default: continue the same Claude conversation
    force: bool = False  # replace a still-live screen (UI confirms first)


def build_myagent_router(
    agent_service: AgentService,
    device_service: DeviceService,
    hub: DeviceHub,
    session_factory: async_sessionmaker[AsyncSession],
) -> APIRouter:
    router = APIRouter(prefix="/connector/my", tags=["connector"])

    async def _agent_member(agent_user_id: int) -> dict[str, object]:
        async with session_factory() as session:
            return (await build_member_dicts(session, hub, [agent_user_id]))[0]

    async def _update_profile(
        agent_user_id: int, nickname: str | None, avatar_id: int | None
    ) -> None:
        if nickname is None and avatar_id is None:
            return
        async with session_factory() as session:
            repo = UserProfileRepository(session)
            profile = await repo.get_profile_by_user_id(agent_user_id)
            if profile is not None:
                await repo.update_profile(profile, nickname=nickname, avatar_id=avatar_id)
                await session.commit()

    # -- devices -----------------------------------------------------------

    @router.get("/devices")
    async def list_devices(user_id: int = Depends(get_current_user_id)) -> dict[str, object]:
        devices = await device_service.list_owned(user_id)
        out: list[dict[str, object]] = []
        for device in devices:
            agent_ids = agent_service.agent_user_ids_on_device(device.device_id)
            async with session_factory() as session:
                agents = await build_member_dicts(session, hub, agent_ids)
            out.append(
                {
                    "device_id": device.device_id,
                    "name": device.name,
                    "online": hub.is_online(device.device_id),
                    "agents": agents,
                }
            )
        return {"devices": out}

    @router.patch("/devices/{device_id}")
    async def rename_device(
        device_id: str, body: RenameDeviceBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        device = await device_service.rename_owned(device_id, body.name, actor_user_id=user_id)
        return {
            "device": {
                "device_id": device.device_id,
                "name": device.name,
                "online": hub.is_online(device.device_id),
                "agents": await _device_agents(device.device_id),
            }
        }

    async def _device_agents(device_id: str) -> list[dict[str, object]]:
        agent_ids = agent_service.agent_user_ids_on_device(device_id)
        async with session_factory() as session:
            return await build_member_dicts(session, hub, agent_ids)

    @router.delete("/devices/{device_id}")
    async def delete_device(
        device_id: str, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        await device_service.delete_owned(device_id, actor_user_id=user_id)
        return {"deleted": True}

    # -- agents ------------------------------------------------------------

    @router.post("/agents")
    async def create_agent(
        body: CreateAgentBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        device = await device_service.get_device(body.device_id)
        if device is None:
            raise NotFoundError("Unknown device")
        if device.owner_user_id != user_id:
            raise ForbiddenError("only the device owner may run agents on it")
        # Agents are project-independent (project is a future wrapper). If the device
        # happens to be assigned to a project, keep the agent linked to it; otherwise it
        # just runs, owned by the device owner.
        projects = await device_service.list_projects(body.device_id)
        project_id = projects[0] if projects else None
        if body.copy_from_agent_user_id is not None:
            # 「复制自」: fork an existing agent's conversation onto this new one. The caller
            # must own the source agent too.
            async with session_factory() as session:
                src_owner = await agent_owner(session, body.copy_from_agent_user_id)
            if src_owner is None:
                raise NotFoundError("source agent not found")
            if src_owner != user_id:
                raise ForbiddenError("you can only copy from your own agents")
            opened = await agent_service.clone_agent(
                source_agent_user_id=body.copy_from_agent_user_id,
                target_device_id=body.device_id,
                project_id=project_id,
                nickname=body.nickname,
                target_cwd=body.target_cwd,
            )
        else:
            opened = await agent_service.open_agent(
                device_id=body.device_id,
                project_id=project_id,
                nickname=body.nickname,
            )
        await _update_profile(opened.agent_user_id, body.nickname, body.avatar_id)
        return {"agent": await _agent_member(opened.agent_user_id)}

    @router.patch("/agents/{agent_user_id}")
    async def update_agent(
        agent_user_id: int, body: UpdateAgentBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        async with session_factory() as session:
            owner = await agent_owner(session, agent_user_id)
        if owner is None:
            raise NotFoundError("Unknown agent")
        if owner != user_id:
            raise ForbiddenError("only the agent's owner may edit it")
        await _update_profile(agent_user_id, body.nickname, body.avatar_id)
        return {"agent": await _agent_member(agent_user_id)}

    @router.post("/agents/{agent_user_id}/recreate")
    async def recreate_agent(
        agent_user_id: int,
        body: RecreateAgentBody,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        """Recreate one of the caller's own agents, reusing its user. ``resume`` (default
        True) continues its Claude conversation; ``force`` replaces a still-live screen —
        the service raises 412 on live-and-not-forced so the UI warns first. Picks the
        agent's prior device if online, else any online device the caller owns."""
        async with session_factory() as session:
            owner = await agent_owner(session, agent_user_id)
            prior = (
                (
                    await session.execute(
                        select(AgentScreenRow)
                        .where(AgentScreenRow.agent_user_id == agent_user_id)
                        .order_by(AgentScreenRow.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
        if owner is None:
            raise NotFoundError("Unknown agent")
        if owner != user_id:
            raise ForbiddenError("only the agent's owner may recreate it")
        # Choose a device the caller owns and that is online — prefer the agent's last one.
        online_owned = [d.device_id for d in await device_service.list_owned(user_id) if hub.is_online(d.device_id)]
        target = (
            prior.device_id
            if prior is not None and prior.device_id in online_owned
            else (online_owned[0] if online_owned else None)
        )
        if target is None:
            raise PreconditionFailedError("no online device of yours to recreate on")
        opened = await agent_service.recreate_agent(
            device_id=target,
            agent_user_id=agent_user_id,
            resume=body.resume,
            force=body.force,
            project_id=prior.project_id if prior is not None else None,
        )
        return {"agent": await _agent_member(opened.agent_user_id), "resumed": body.resume}

    @router.delete("/agents/{sid}")
    async def close_agent(
        sid: str, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        screen = hub.screen(sid)
        if screen is None:
            raise NotFoundError("Unknown agent screen")
        device = await device_service.get_device(screen.device_id)
        if device is None or device.owner_user_id != user_id:
            raise ForbiddenError("only the device owner may close its agents")
        await agent_service.close_agent(device_id=screen.device_id, sid=sid)
        return {"closed": True}

    return router
