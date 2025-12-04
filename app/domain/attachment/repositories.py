from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.attachment.models import Attachment


class AttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, attachment_id: int) -> Attachment | None:
        stmt = select(Attachment).where(Attachment.id == attachment_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(self, ids: list[int]) -> list[Attachment]:
        if not ids:
            return []
        stmt = select(Attachment).where(Attachment.id.in_(ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        attachment_type: str,
        url: str,
        meta: dict[str, Any] | None = None,
    ) -> Attachment:
        attachment = Attachment(
            type=attachment_type,
            url=url,
            meta=meta or {},
        )
        self._session.add(attachment)
        await self._session.flush()
        return attachment

    async def delete(self, attachment_id: int) -> bool:
        attachment = await self.get_by_id(attachment_id)
        if attachment is None:
            return False
        await self._session.delete(attachment)
        await self._session.flush()
        return True
