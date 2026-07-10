"""Agent-binding data access (derives the is-agent distinction)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.models import AgentBinding, AgentBindingKind


class AgentBindingRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_for_user(self, user_id: uuid.UUID) -> AgentBinding | None:
        stmt = select(AgentBinding).where(AgentBinding.user_id == user_id)
        return await self._session.scalar(stmt)

    async def add(
        self, *, user_id: uuid.UUID, kind: str = AgentBindingKind.platform
    ) -> AgentBinding:
        binding = AgentBinding(user_id=user_id, kind=kind)
        self._session.add(binding)
        await self._session.flush()
        await self._session.refresh(binding)
        return binding

    async def agent_user_ids(self, user_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Which of ``user_ids`` are agents — one batch query, no per-id N+1."""
        ids = [u for u in set(user_ids) if u is not None]
        if not ids:
            return set()
        stmt = select(AgentBinding.user_id).where(AgentBinding.user_id.in_(ids))
        return set((await self._session.scalars(stmt)).all())
