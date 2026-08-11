import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.llm.models import AIConversation, AIMessage, AIUserQuota


class AIUserQuotaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, user_id: int, daily_total: float) -> AIUserQuota:
        stmt = select(AIUserQuota).where(
            AIUserQuota.user_id == user_id,
            AIUserQuota.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        entity = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if entity is None:
            entity = AIUserQuota(
                user_id=user_id,
                daily_seu_quota=daily_total,
                remaining_seu=daily_total,
                total_seu_consumed=0.0,
                last_reset_time=now,
                created_at=now,
                updated_at=now,
            )
            self._session.add(entity)
            await self._session.flush()
            return entity
        if entity.last_reset_time and now.date() > entity.last_reset_time.date():
            entity.remaining_seu = entity.daily_seu_quota or daily_total
            entity.total_seu_consumed = 0.0
            entity.last_reset_time = now
            entity.updated_at = now
            await self._session.flush()
        return entity

    async def consume(
        self, user_id: int, amount: float, daily_total: float
    ) -> tuple[float, datetime]:
        entity = await self.get_or_create(user_id, daily_total)
        remaining = entity.remaining_seu or 0.0
        if remaining < amount:
            raise ValueError("AI quota exhausted")
        new_remaining = remaining - amount
        entity.remaining_seu = new_remaining
        entity.total_seu_consumed = (entity.total_seu_consumed or 0.0) + amount
        entity.updated_at = datetime.now(UTC)
        await self._session.flush()
        reset_at = entity.last_reset_time or datetime.now(UTC)
        return new_remaining, reset_at

    async def get_quota(
        self, user_id: int, daily_total: float
    ) -> tuple[float, datetime]:
        entity = await self.get_or_create(user_id, daily_total)
        remaining = max(0.0, entity.remaining_seu or 0.0)
        reset_at = entity.last_reset_time or datetime.now(UTC)
        return remaining, reset_at


class AIConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        title: str | None = None,
        model_type: str = "standard",
        module_type: str = "GENERAL",
    ) -> AIConversation:
        now = datetime.now(UTC)
        conversation_uuid = str(uuid.uuid4())
        entity = AIConversation(
            owner_id=user_id,
            conversation_id=conversation_uuid,
            title=title,
            model_type=model_type,
            module_type=module_type,
            created_at=now,
            updated_at=now,
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def get_by_id(self, conversation_id: int) -> AIConversation | None:
        stmt = select(AIConversation).where(
            AIConversation.id == conversation_id,
            AIConversation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: int,
        *,
        page_start: int | None = None,
        page_size: int = 20,
    ) -> tuple[list[AIConversation], dict]:
        stmt = (
            select(AIConversation)
            .where(
                AIConversation.owner_id == user_id,
                AIConversation.deleted_at.is_(None),
            )
            .order_by(AIConversation.updated_at.desc())
        )

        if page_start is not None:
            stmt = stmt.where(AIConversation.id <= page_start)

        stmt = stmt.limit(page_size + 1)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > page_size
        if has_more:
            rows = rows[:page_size]

        page_info = {
            "pageStart": rows[0].id if rows else None,
            "pageSize": page_size,
            "hasMore": has_more,
            "nextStart": rows[-1].id if has_more and rows else None,
        }
        return rows, page_info

    async def delete(self, conversation_id: int) -> bool:
        entity = await self.get_by_id(conversation_id)
        if entity is None:
            return False
        entity.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def update_title(
        self, conversation_id: int, title: str
    ) -> AIConversation | None:
        entity = await self.get_by_id(conversation_id)
        if entity is None:
            return None
        entity.title = title
        entity.updated_at = datetime.now(UTC)
        await self._session.flush()
        return entity


class AIMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        conversation_id: int,
        role: str,
        content: str,
        model_type: str = "standard",
        tokens_used: int | None = None,
        seu_consumed: float | None = None,
    ) -> AIMessage:
        now = datetime.now(UTC)
        entity = AIMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            model_type=model_type,
            tokens_used=tokens_used,
            seu_consumed=seu_consumed,
            created_at=now,
            updated_at=now,
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def list_by_conversation(self, conversation_id: int) -> list[AIMessage]:
        stmt = (
            select(AIMessage)
            .where(
                AIMessage.conversation_id == conversation_id,
                AIMessage.deleted_at.is_(None),
            )
            .order_by(AIMessage.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_conversation(self, conversation_id: int) -> int:
        stmt = select(func.count(AIMessage.id)).where(
            AIMessage.conversation_id == conversation_id,
            AIMessage.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar() or 0
