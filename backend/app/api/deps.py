"""Dependency injection wiring."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.core.config import settings
from app.core.db import async_session_factory, get_db
from app.domain.agent.chat import ChatService
from app.domain.agent.service import AgentService
from app.domain.scheduler.service import SchedulerService

__all__ = ["get_db", "get_chat_service", "get_scheduler_service"]


@lru_cache
def get_chat_service() -> ChatService:
    agent = AgentService(model=settings.agent_model, env=settings.agent_env())
    return ChatService(
        session_factory=async_session_factory,
        agent=agent,
        base_system_prompt=settings.agent_system_prompt,
        workspace_root=settings.workspace_root,
        sandbox_enabled=settings.agent_sandbox_enabled,
    )


def get_scheduler_service(
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> SchedulerService:
    return SchedulerService(chat_service=chat)
