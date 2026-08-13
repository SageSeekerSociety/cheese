from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.oauth.models import UserOAuthConnection


class OAuthConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Exposed so a caller that needs an INDEPENDENT transaction on the
        same database (see OAuthService._refresh_and_persist_token) can open
        a fresh session on this one's engine via ``session.bind``,
        rather than assuming a hardcoded module-level session factory —
        under the test harness the app's default session factory and a
        request's actual session can be bound to different databases."""
        return self._session

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

    async def get_for_update(self, connection_id: int) -> UserOAuthConnection | None:
        """Row-locked read (``SELECT ... FOR UPDATE``) — serializes concurrent
        token refreshers of the SAME connection so a second caller blocks
        until the first's refresh transaction commits, then re-reads the
        (now current) row instead of racing it with a stale refresh_token."""
        stmt = (
            select(UserOAuthConnection)
            .where(UserOAuthConnection.id == connection_id)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_tokens(
        self,
        connection_id: int,
        access_token: str | None,
        refresh_token: str | None,
        token_expires: datetime | None,
        raw_profile: dict | None = None,
    ) -> None:
        """``raw_profile=None`` leaves the stored profile alone.

        That default matters: most callers here are silent token refreshers
        that never talked to the provider's user endpoint, and overwriting the
        profile with what they don't have would erase it.
        """
        stmt = select(UserOAuthConnection).where(
            UserOAuthConnection.id == connection_id
        )
        result = await self._session.execute(stmt)
        entity = result.scalar_one_or_none()
        if entity:
            entity.access_token = access_token
            entity.refresh_token = refresh_token
            entity.token_expires = token_expires
            if raw_profile is not None:
                entity.raw_profile = raw_profile
            entity.updated_at = datetime.now(UTC)
            await self._session.flush()
