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
from app.domain.agent.profiles import ProfileRegistry, build_registry
from app.domain.agent.runtime import InProcessBroker, TurnRunner
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
]


async def project_device_online(db: AsyncSession, project_id: uuid.UUID) -> bool:
    """Whether this project has an enrolled machine connected right now — the honest
    availability of the self-hosted compute pool for THIS project's context (compute
    belongs to the project/team, not globally, execution-architecture v4). Used by the
    compute-profile routes to scope the 自托管设备 pool to a project's own machines."""
    service = DeviceService(SqlDeviceRepository(db))
    return await service.project_has_online_device(project_id, device_hub.is_online)


@lru_cache
def get_profile_registry() -> ProfileRegistry:
    return build_registry(settings)


@lru_cache
def get_chat_service() -> ChatService:
    agent = AgentService(model=settings.agent_model, env=settings.agent_env())
    return ChatService(
        session_factory=async_session_factory,
        agent=agent,
        base_system_prompt=settings.agent_system_prompt,
        workspace_root=settings.workspace_root,
        sandbox_enabled=settings.agent_sandbox_enabled,
        profiles=get_profile_registry(),
        compute=build_compute_pool(agent),
    )


def get_scheduler_service(
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> SchedulerService:
    return SchedulerService(chat_service=chat)


@lru_cache
def get_broker() -> InProcessBroker:
    return InProcessBroker()


@lru_cache
def get_turn_runner() -> TurnRunner:
    return TurnRunner(get_broker(), turn_timeout_s=settings.agent_turn_timeout_s)
