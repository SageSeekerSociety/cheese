"""Dependency injection wiring."""

import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory, get_db
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import build_compute_pool
from app.domain.agent.device_hub import device_hub
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.profiles import ProfileRegistry, build_registry
from app.domain.agent.runtime import TurnRunner, get_broker
from app.domain.agent.service import AgentService
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.scheduler.service import SchedulerService

__all__ = [
    "get_db",
    "get_chat_service",
    "get_scheduler_service",
    "get_profile_registry",
    "get_broker",
    "get_turn_runner",
    "project_device_online",
    "team_device_online",
]


async def project_device_online(db: AsyncSession, project_id: uuid.UUID) -> bool:
    """Whether this project has an enrolled machine connected right now — the honest
    availability of the self-hosted compute pool for THIS project's context (compute
    belongs to the project/team, not globally, execution-architecture v4). Used by the
    compute-profile routes to scope the 自托管设备 pool to a project's own machines."""
    service = DeviceService(SqlDeviceRepository(db))
    return await service.project_has_online_device(project_id, device_hub.is_online)


async def team_device_online(db: AsyncSession, team_id: int) -> bool:
    """Whether one of the team's registered compute nodes is connected now."""
    service = DeviceService(SqlDeviceRepository(db))
    devices = await service.list_devices_for_team(team_id)
    return any(device_hub.is_online(device.device_id) for device in devices)


@lru_cache
def get_profile_registry() -> ProfileRegistry:
    return build_registry(settings)


@lru_cache
def get_chat_service() -> ChatService:
    agent = AgentService(model=settings.agent_model, env=settings.agent_env())
    # Gateway admin client (docs/llm-gateway.md L1/L2): only when the pool routes
    # through the self-hosted gateway AND admin creds are configured.
    gateway = None
    if settings.llm_gateway_admin_base and settings.llm_gateway_admin_key:
        gateway = LlmGateway(
            settings.llm_gateway_admin_base, settings.llm_gateway_admin_key
        )
    return ChatService(
        session_factory=async_session_factory,
        agent=agent,
        base_system_prompt=settings.agent_system_prompt,
        workspace_root=settings.workspace_root,
        sandbox_enabled=settings.agent_sandbox_enabled,
        profiles=get_profile_registry(),
        compute=build_compute_pool(agent),
        gateway=gateway,
    )


def get_scheduler_service(
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> SchedulerService:
    return SchedulerService(chat_service=chat)


@lru_cache
def get_turn_runner() -> TurnRunner:
    # #388 缺陷一: let the cold-start fuse know when a topic's device screen is
    # running on a credential the backend already stamped as expired, so a doomed
    # turn fast-fails with the true reason instead of burning the full fuse. Reads
    # the device hub's live screen state (in-memory, cheap); a topic on the local
    # tmux/SDK path has no screen there → None → the fuse is unchanged.
    from app.domain.agent.device_provider import topic_credential_expiry

    return TurnRunner(
        get_broker(),
        turn_timeout_s=settings.agent_turn_timeout_s,
        first_output_timeout_s=settings.agent_first_output_timeout_s,
        credential_expiry_of=topic_credential_expiry,
    )
