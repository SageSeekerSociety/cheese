"""A document's read-only share link (#3774): at most one per document, its
token the whole secret. Taking it back deletes it; sharing again makes a new
token."""

import secrets
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.models import Document, DocumentShare


class Shares:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def of(self, document_id: uuid.UUID) -> DocumentShare | None:
        return await self._session.get(DocumentShare, document_id)

    async def make(self, document_id: uuid.UUID, by: str) -> DocumentShare:
        """The document's link, made now when it has none."""
        row = await self.of(document_id)
        if row is None:
            row = DocumentShare(
                document_id=document_id, token=secrets.token_urlsafe(32), created_by=by
            )
            self._session.add(row)
            await self._session.flush()
        return row

    async def take_back(self, document_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(DocumentShare).where(DocumentShare.document_id == document_id)
        )

    async def document(self, token: str) -> Document | None:
        """The document this token shares, while it does."""
        return await self._session.scalar(
            select(Document)
            .join(DocumentShare, DocumentShare.document_id == Document.id)
            .where(DocumentShare.token == token)
        )
