"""Comment threads, on a document or on a room file.

The thread itself — who opened it, what it says, its replies, its state, and
the revision that guards concurrent writes — is the same either way. What
differs is where a thread points and how that place is kept.

On a document, the words a thread is about are marked in the shared document
itself (``commentAnchor``, carrying the opening comment's id), so the mark
travels with them through every later edit; the opening comment keeps the
quoted words for display.

On a room file there is no shared text to carry a mark: the file is bytes,
saved version by version. So the opening comment keeps the place in ``anchor``
— in the shapes a room message quotes (``block/message_input``) — together
with the version it was read at. A later version may move that place; nothing
here follows it, and a reader who finds it moved says so on the thread.

Every thread mutation is made under its subject's lock. The caller owns
authorization and commit.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.sentences import say
from app.domain.living_doc.models import (
    CommentOperation,
    DocumentComment,
    DocumentThread,
)
from app.domain.living_doc.services import DocumentJournal, payload_fingerprint

#: How much of a selection an opening comment keeps for display.
QUOTE_LIMIT = 500


@dataclass(frozen=True)
class OnDocument:
    """A thread on a document."""

    document_id: uuid.UUID


@dataclass(frozen=True)
class OnRoomFile:
    """A thread on a file of a room, at the address the preview uses."""

    room_id: uuid.UUID
    path: str

    @property
    def lock_key(self) -> str:
        """The advisory-lock key, in the shape ``save_room_file`` uses for the
        file itself — a different key, deliberately: a comment does not have to
        wait for a save of the file, and a save does not have to wait for a
        comment."""
        return f"room-file-comments:{self.room_id}:{self.path}"


Subject = OnDocument | OnRoomFile


def comment_out(comment: DocumentComment) -> dict:
    return {
        "id": str(comment.id),
        "author": comment.author,
        "content": comment.content,
        "anchor_quote": comment.anchor_quote,
        "anchor": comment.anchor,
        "created_at": comment.created_at.isoformat(),
    }


class CommentThreads:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _subject_columns(self, subject: Subject) -> dict:
        """Which subject an opening comment, or a reply, is written under."""
        if isinstance(subject, OnDocument):
            return {"document_id": subject.document_id}
        return {"room_id": subject.room_id, "room_path": subject.path}

    def _under(self, subject: Subject, stmt):
        if isinstance(subject, OnDocument):
            return stmt.where(DocumentComment.document_id == subject.document_id)
        return stmt.where(
            DocumentComment.room_id == subject.room_id,
            DocumentComment.room_path == subject.path,
        )

    def _openings(self, subject: Subject):
        return self._under(
            subject,
            select(DocumentComment).where(DocumentComment.thread_id.is_(None)),
        ).order_by(DocumentComment.created_at, DocumentComment.id)

    async def lock(self, subject: Subject) -> None:
        """Hold the subject's lock for the rest of this transaction.

        A document's threads share the document's own lock — the one its
        writers hold too. A room file has no such row, so its threads take an
        advisory lock on the file's address."""
        if isinstance(subject, OnDocument):
            await DocumentJournal(self.session).lock(subject.document_id)
            return
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": subject.lock_key},
        )

    async def roots(self, subject: Subject) -> list[DocumentComment]:
        """The comments that open this subject's threads, oldest first."""
        return list(await self.session.scalars(self._openings(subject)))

    async def root(self, subject: Subject, comment_id: uuid.UUID) -> DocumentComment:
        row = await self.session.scalar(
            self._openings(subject).where(DocumentComment.id == comment_id)
        )
        if row is None:
            raise NotFoundError(say("docCommentNotFound"))
        return row

    async def comment(self, comment_id: uuid.UUID) -> DocumentComment:
        """One comment, opening a thread or replying in one."""
        row = await self.session.get(DocumentComment, comment_id)
        if row is None:
            raise NotFoundError(say("docCommentNotFound"))
        return row

    async def open(
        self,
        subject: Subject,
        *,
        author: str,
        content: str,
        quote: str | None,
        anchor: dict | None = None,
    ) -> DocumentComment:
        """Start a thread on ``quote`` (the whole subject without one).

        ``anchor`` is where a file's thread points. A document's passes none:
        the words themselves carry the mark."""
        await self.lock(subject)
        comment = DocumentComment(
            **self._subject_columns(subject),
            author=author,
            content=content,
            anchor_quote=quote[:QUOTE_LIMIT] if quote else None,
            anchor=anchor,
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
        subject: Subject,
        *,
        comment_id: uuid.UUID,
        author: str,
        expected_revision: int,
        action: str,
        content: str | None = None,
    ) -> dict:
        await self.lock(subject)
        comment = await self.root(subject, comment_id)
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
                    **self._subject_columns(subject),
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


class FileCommentOperations:
    """A file thread's writes, remembered by operation id.

    ``DocumentJournal`` does this for a document's threads, on the document's
    own operation table and under its lock. A file has no document to hang
    either on, so its ledger is ``comment_operations`` and its lock is the
    thread lock ``CommentThreads.lock`` takes for the same subject: a retry
    after an unknown outcome replays its receipt instead of writing twice.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def claim(
        self,
        subject: OnRoomFile,
        *,
        actor: str,
        action: str,
        operation_id: uuid.UUID,
        payload: dict,
    ) -> CommentOperation:
        await CommentThreads(self.session).lock(subject)
        fingerprint = payload_fingerprint(payload)
        row = await self.session.scalar(
            select(CommentOperation).where(
                CommentOperation.room_id == subject.room_id,
                CommentOperation.room_path == subject.path,
                CommentOperation.actor == actor,
                CommentOperation.action == action,
                CommentOperation.operation_id == operation_id,
            )
        )
        if row is not None:
            if row.fingerprint != fingerprint:
                raise ConflictError(say("docOperationIdReused"))
            if row.receipt is None:
                raise ConflictError(say("docOperationNoReceipt"))
            return row
        row = CommentOperation(
            room_id=subject.room_id,
            room_path=subject.path,
            actor=actor,
            action=action,
            operation_id=operation_id,
            fingerprint=fingerprint,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def finish(self, operation: CommentOperation, receipt: dict) -> None:
        operation.receipt = receipt
