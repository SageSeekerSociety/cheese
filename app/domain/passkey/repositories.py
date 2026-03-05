from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.passkey.models import PasskeyCredential


class PasskeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        credential_id: str,
        public_key: bytes,
        counter: int = 0,
        device_type: str,
        backed_up: bool = False,
        transports: list[str] | None = None,
    ) -> PasskeyCredential:
        now = datetime.now(UTC)
        entity = PasskeyCredential(
            user_id=user_id,
            credential_id=credential_id,
            public_key=public_key,
            counter=counter,
            device_type=device_type,
            backed_up=backed_up,
            transports=",".join(transports) if transports else None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def get_by_credential_id(self, credential_id: str) -> PasskeyCredential | None:
        stmt = select(PasskeyCredential).where(PasskeyCredential.credential_id == credential_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: int) -> list[PasskeyCredential]:
        stmt = (
            select(PasskeyCredential)
            .where(PasskeyCredential.user_id == user_id)
            .order_by(PasskeyCredential.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_credential_ids_for_user(self, user_id: int) -> list[str]:
        stmt = select(PasskeyCredential.credential_id).where(PasskeyCredential.user_id == user_id)
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def update_counter(
        self,
        credential_id: str,
        counter: int,
    ) -> None:
        entity = await self.get_by_credential_id(credential_id)
        if entity:
            entity.counter = counter
            entity.updated_at = datetime.now(UTC)
            await self._session.flush()

    async def delete_by_id(self, credential_id: int, user_id: int) -> bool:
        stmt = delete(PasskeyCredential).where(
            PasskeyCredential.id == credential_id,
            PasskeyCredential.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount > 0

    async def delete_by_credential_id(self, credential_id: str, user_id: int) -> bool:
        stmt = delete(PasskeyCredential).where(
            PasskeyCredential.credential_id == credential_id,
            PasskeyCredential.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount > 0
