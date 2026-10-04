"""Comment threads on a document.

Which words a thread is about is marked in the shared document itself
(``commentAnchor``, carrying the opening comment's id); the opening comment
keeps the quoted words for display. Every thread mutation is made under the
document's lock. The caller owns authorization and commit.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.sentences import say
from app.domain.living_doc.models import Document, DocumentComment, DocumentThread
from app.domain.living_doc.services import DocumentJournal

#: How much of a selection an opening comment keeps for display.
QUOTE_LIMIT = 500


def comment_out(comment: DocumentComment) -> dict:
    return {
        "id": str(comment.id),
        "author": comment.author,
        "content": comment.content,
        "anchor_quote": comment.anchor_quote,
        "created_at": comment.created_at.isoformat(),
    }


class CommentThreads:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _openings(self, document_id: uuid.UUID):
        return (
            select(DocumentComment)
            .where(
                DocumentComment.document_id == document_id,
                DocumentComment.thread_id.is_(None),
            )
            .order_by(DocumentComment.created_at, DocumentComment.id)
        )

    async def roots(self, document_id: uuid.UUID) -> list[DocumentComment]:
        """The comments that open the document's threads, oldest first."""
        return list(await self.session.scalars(self._openings(document_id)))

    async def root(
        self, document_id: uuid.UUID, comment_id: uuid.UUID
    ) -> DocumentComment:
        row = await self.session.scalar(
            self._openings(document_id).where(DocumentComment.id == comment_id)
        )
        if row is None:
            raise NotFoundError(say("docCommentNotFound"))
        return row

    async def open(
        self, doc: Document, *, author: str, content: str, quote: str | None
    ) -> DocumentComment:
        """Start a thread on ``quote`` (the whole document without one)."""
        await DocumentJournal(self.session).lock(doc.id)
        comment = DocumentComment(
            document_id=doc.id,
            author=author,
            content=content,
            anchor_quote=quote[:QUOTE_LIMIT] if quote else None,
        )
        self.session.add(comment)
        await self.session.flush()
        self.session.add(DocumentThread(id=comment.id))
        await self.session.flush()
        return comment

    async def describe(self, comment: DocumentComment) -> dict:
        thread = await self.session.get(DocumentThread, comment.id)
        assert thread is not None
        replies = await self.session.scalars(
            select(DocumentComment)
            .where(DocumentComment.thread_id == comment.id)
            .order_by(DocumentComment.sequence)
        )
        return {
            "comment": comment_out(comment),
            "revision": thread.revision,
            "state": thread.state,
            "replies": [
                {"sequence": reply.sequence, "comment": comment_out(reply)}
                for reply in replies
            ],
        }

    async def mutate(
        self,
        doc: Document,
        *,
        comment_id: uuid.UUID,
        author: str,
        expected_revision: int,
        action: str,
        content: str | None = None,
    ) -> dict:
        await DocumentJournal(self.session).lock(doc.id)
        comment = await self.root(doc.id, comment_id)
        thread = await self.session.get(DocumentThread, comment.id)
        assert thread is not None
        await self.session.refresh(thread)
        if thread.revision != expected_revision:
            raise ConflictError(say("commentThreadUpdated"))
        if action == "reply":
            if thread.state != "open":
                raise ConflictError(say("commentResolvedReopenFirst"))
            assert content is not None
            thread.reply_count += 1
            self.session.add(
                DocumentComment(
                    document_id=doc.id,
                    thread_id=comment.id,
                    sequence=thread.reply_count,
                    author=author,
                    content=content,
                )
            )
        else:
            target = "resolved" if action == "resolve" else "open"
            if thread.state == target:
                raise ConflictError(say("commentAlreadyInState"))
            thread.state = target
        thread.revision += 1
        await self.session.flush()
        return await self.describe(comment)
