"""Dependency injection wiring."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.core.config import settings
from app.core.db import async_session_factory, get_db
from app.domain.agent.chat import ChatService
from app.domain.agent.profiles import ProfileRegistry, build_registry
from app.domain.agent.runtime import InProcessBroker, TurnRunner
from app.domain.agent.service import AgentService
from app.domain.scheduler.service import SchedulerService

__all__ = [
    "get_db",
    "get_chat_service",
    "get_scheduler_service",
    "get_profile_registry",
    "get_broker",
    "get_turn_runner",
]


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
    return TurnRunner(get_broker())
