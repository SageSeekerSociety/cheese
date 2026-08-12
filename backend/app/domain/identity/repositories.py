"""Agent-binding + agent-token data access (derives the is-agent distinction)."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.models import AgentBinding, AgentBindingKind, AgentToken


class AgentBindingRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_for_user(self, user_id: int) -> AgentBinding | None:
        stmt = select(AgentBinding).where(AgentBinding.user_id == user_id)
        return await self._session.scalar(stmt)

    async def get_for_owner(self, owner_user_id: int) -> AgentBinding | None:
        """The binding of the agent this human runs, if they have issued one."""
        stmt = select(AgentBinding).where(AgentBinding.owner_user_id == owner_user_id)
        return await self._session.scalar(stmt)

    async def add(
        self,
        *,
        user_id: int,
        kind: str = AgentBindingKind.platform,
        owner_user_id: int | None = None,
    ) -> AgentBinding:
        binding = AgentBinding(user_id=user_id, kind=kind, owner_user_id=owner_user_id)
        self._session.add(binding)
        await self._session.flush()
        await self._session.refresh(binding)
        return binding

    async def agent_user_ids(self, user_ids: list[int]) -> set[int]:
        """Which of ``user_ids`` are agents — one batch query, no per-id N+1."""
        ids = [u for u in set(user_ids) if u is not None]
        if not ids:
            return set()
        stmt = select(AgentBinding.user_id).where(AgentBinding.user_id.in_(ids))
        return set((await self._session.scalars(stmt)).all())

    async def platform_agent_user_ids(self, user_ids: list[int]) -> set[int]:
        """Which of ``user_ids`` are the *platform's* agents (芝士), i.e. bindings
        that belong to no human.

        Separate from :meth:`agent_user_ids` because the two questions diverged
        once members could run their own agents: "may I skip notifying this
        member, they read the timeline anyway" and "who speaks for the platform
        in this room" are both true only of a platform agent.
        """
        ids = [u for u in set(user_ids) if u is not None]
        if not ids:
            return set()
        stmt = select(AgentBinding.user_id).where(
            AgentBinding.user_id.in_(ids), AgentBinding.owner_user_id.is_(None)
        )
        return set((await self._session.scalars(stmt)).all())


class AgentTokenRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        owner_user_id: int,
        agent_user_id: int,
        name: str,
        token_hash: str,
        token_prefix: str,
        expires_at: datetime,
    ) -> AgentToken:
        token = AgentToken(
            owner_user_id=owner_user_id,
            agent_user_id=agent_user_id,
            name=name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            expires_at=expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        await self._session.refresh(token)
        return token

    async def get_by_hash(self, token_hash: str) -> AgentToken | None:
        stmt = select(AgentToken).where(AgentToken.token_hash == token_hash)
        return await self._session.scalar(stmt)

    async def get_for_owner(
        self, token_id: uuid.UUID, owner_user_id: int
    ) -> AgentToken | None:
        """Scoped read: a token is only ever addressable by the user who owns it,
        so a wrong id and someone else's id are the same 404."""
        stmt = select(AgentToken).where(
            AgentToken.id == token_id, AgentToken.owner_user_id == owner_user_id
        )
        return await self._session.scalar(stmt)

    async def list_for_owner(self, owner_user_id: int) -> list[AgentToken]:
        stmt = (
            select(AgentToken)
            .where(AgentToken.owner_user_id == owner_user_id)
            .order_by(AgentToken.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())
