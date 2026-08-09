from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.oauth.models import UserOAuthConnection


class OAuthConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        provider_id: str,
        provider_user_id: str,
        raw_profile: dict | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_expires: datetime | None = None,
    ) -> UserOAuthConnection:
        now = datetime.now(UTC)
        entity = UserOAuthConnection(
            user_id=user_id,
            provider_id=provider_id,
            provider_user_id=provider_user_id,
            raw_profile=raw_profile,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires=token_expires,
            created_at=now,
            updated_at=now,
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def get_by_provider(
        self, provider_id: str, provider_user_id: str
    ) -> UserOAuthConnection | None:
        stmt = select(UserOAuthConnection).where(
            UserOAuthConnection.provider_id == provider_id,
            UserOAuthConnection.provider_user_id == provider_user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_and_provider(
        self, user_id: int, provider_id: str
    ) -> UserOAuthConnection | None:
        stmt = select(UserOAuthConnection).where(
            UserOAuthConnection.user_id == user_id,
            UserOAuthConnection.provider_id == provider_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: int) -> list[UserOAuthConnection]:
        stmt = (
            select(UserOAuthConnection)
            .where(UserOAuthConnection.user_id == user_id)
            .order_by(UserOAuthConnection.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_by_id(self, connection_id: int, user_id: int) -> bool:
        stmt = delete(UserOAuthConnection).where(
            UserOAuthConnection.id == connection_id,
            UserOAuthConnection.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        # rowcount exists on CursorResult returned by execute() for DML at runtime
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def update_tokens(
        self,
        connection_id: int,
        access_token: str | None,
        refresh_token: str | None,
        token_expires: datetime | None,
    ) -> None:
        stmt = select(UserOAuthConnection).where(
            UserOAuthConnection.id == connection_id
        )
        result = await self._session.execute(stmt)
        entity = result.scalar_one_or_none()
        if entity:
            entity.access_token = access_token
            entity.refresh_token = refresh_token
            entity.token_expires = token_expires
            entity.updated_at = datetime.now(UTC)
            await self._session.flush()
