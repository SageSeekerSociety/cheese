"""Agent-binding data access (derives the is-agent distinction)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.identity.models import AgentBinding, AgentBindingKind

_BINDING_TABLES = frozenset({"agent_bindings"})


def _can_cache(session: object) -> bool:
    return isinstance(session, AsyncSession)


class AgentBindingRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_for_user(self, user_id: int) -> AgentBinding | None:
        stmt = select(AgentBinding).where(AgentBinding.user_id == user_id)
        binding = await self._session.scalar(stmt)
        if _can_cache(self._session):
            remember(
                self._session,
                ("is_agent_uid", user_id),
                binding is not None,
                tables=_BINDING_TABLES,
            )
        return binding

    async def add(
        self, *, user_id: int, kind: str = AgentBindingKind.platform
    ) -> AgentBinding:
        binding = AgentBinding(user_id=user_id, kind=kind)
        self._session.add(binding)
        await self._session.flush()
        await self._session.refresh(binding)
        return binding

    async def agent_user_ids(self, user_ids: list[int]) -> set[int]:
        """Which of ``user_ids`` are agents — one batch query, no per-id N+1."""
        ids = [u for u in dict.fromkeys(user_ids) if u is not None]
        if not ids:
            return set()
        if not _can_cache(self._session):
            stmt = select(AgentBinding.user_id).where(AgentBinding.user_id.in_(ids))
            return set((await self._session.scalars(stmt)).all())
        if self._session.autoflush:
            await self._session.flush()
        known: set[int] = set()
        missing: list[int] = []
        for uid in ids:
            found, cached = recall(self._session, ("is_agent_uid", uid))
            if found:
                if cached:
                    known.add(uid)
            else:
                missing.append(uid)
        if not missing:
            return known
        stmt = select(AgentBinding.user_id).where(AgentBinding.user_id.in_(missing))
        fetched = set((await self._session.scalars(stmt)).all())
        for uid in missing:
            remember(
                self._session,
                ("is_agent_uid", uid),
                uid in fetched,
                tables=_BINDING_TABLES,
            )
        return known | fetched
