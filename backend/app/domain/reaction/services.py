"""Business logic & authorization for 消息表情回应 (message reactions).

One service behind the same trust boundary as threads: the actor is always injected,
and every op authorizes against the actor's real thread membership — a valid session
is necessary but never sufficient. react/unreact are idempotent (the repository's
unique constraint absorbs duplicates). Grouping produces Feishu-style chips keyed by
block id so the frontend can render reaction counts without extra round-trips.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.block.repositories import BlockRepository
from app.domain.reaction.repositories import ReactionRepository
from app.domain.thread.repositories import ThreadMembershipRepository


class ReactionService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _require_member(
        self, session: AsyncSession, thread_id: int, actor_id: int
    ) -> None:
        if not await ThreadMembershipRepository(session).is_member(thread_id, actor_id):
            raise ForbiddenError("must be a member of the thread")

    async def _require_message(
        self, session: AsyncSession, thread_id: int, block_id: int
    ) -> None:
        block = await BlockRepository(session).get_message_in_thread(thread_id, block_id)
        if block is None:
            raise NotFoundError("Unknown message in thread")

    async def react(
        self, actor_id: int, thread_id: int, block_id: int, emoji: str
    ) -> dict[str, object]:
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            await self._require_message(session, thread_id, block_id)
            await ReactionRepository(session).add(block_id, actor_id, emoji)
            await session.commit()
        return {"ok": True}

    async def unreact(
        self, actor_id: int, thread_id: int, block_id: int, emoji: str
    ) -> dict[str, object]:
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            await self._require_message(session, thread_id, block_id)
            await ReactionRepository(session).remove(block_id, actor_id, emoji)
            await session.commit()
        return {"ok": True}

    async def reactions_for_thread(
        self, actor_id: int, thread_id: int, block_ids: list[int]
    ) -> dict[str, object]:
        """Grouped reactions for the given blocks. Returns
        ``{"reactions": {"<blockId>": [{emoji, count, userIds, me}, ...]}}``.
        Only blocks the caller asked for appear; a block with no reactions is omitted.
        """
        async with self._sf() as session:
            await self._require_member(session, thread_id, actor_id)
            rows = await ReactionRepository(session).list_for_blocks(block_ids)

        # block_id -> emoji -> ordered list of user_ids (insertion order preserved).
        grouped: dict[int, dict[str, list[int]]] = {}
        for row in rows:
            grouped.setdefault(row.block_id, {}).setdefault(row.emoji, []).append(row.user_id)

        out: dict[str, list[dict[str, object]]] = {}
        for block_id, by_emoji in grouped.items():
            chips: list[dict[str, object]] = [
                {
                    "emoji": emoji,
                    "count": len(user_ids),
                    "userIds": user_ids,
                    "me": actor_id in user_ids,
                }
                for emoji, user_ids in by_emoji.items()
            ]
            out[str(block_id)] = chips
        return {"reactions": out}
