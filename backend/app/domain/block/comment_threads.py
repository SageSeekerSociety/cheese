"""Transactional room-document comment threads over the existing block tree.

Which words a thread is about is marked in the shared document itself
(``commentAnchor``); the root comment keeps the quoted words for display. The
room journal lock serializes thread mutations. The caller owns authorization
and commit.
"""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import ConflictError, NotFoundError
from app.domain.block.comment_models import DocCommentReply, DocCommentThread
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.notice_text import say
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.living_doc.services import DocumentJournal


def block_out(block: Block) -> dict:
    return BlockOut.model_validate(block).model_dump(mode="json")


class CommentThreads:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.blocks = BlockRepository(session)

    def roots_query(self, room_id: uuid.UUID):
        parent = aliased(Block)
        return (
            select(Block)
            .where(
                Block.topic_id == room_id,
                Block.task_id.is_(None),
                Block.kind == BlockKind.comment,
                ~exists().where(DocCommentReply.block_id == Block.id),
                ~exists().where(
                    parent.id == Block.reply_to, parent.kind == BlockKind.comment
                ),
            )
            .order_by(Block.created_at, Block.id)
        )

    async def roots(self, room_id: uuid.UUID) -> list[Block]:
        return list(await self.session.scalars(self.roots_query(room_id)))

    async def root(self, room_id: uuid.UUID, comment_id: uuid.UUID) -> Block:
        row = await self.session.scalar(
            self.roots_query(room_id).where(Block.id == comment_id)
        )
        if row is None:
            raise NotFoundError(say("docCommentNotFound"))
        return row

    async def capture(self, comment: Block) -> DocCommentThread:
        """The thread row of a root comment, made on first use."""
        row = await self.session.get(DocCommentThread, comment.id)
        if row is not None:
            return row
        row = DocCommentThread(comment_id=comment.id)
        self.session.add(row)
        await self.session.flush()
        return row

    async def describe(self, comment: Block) -> dict:
        thread = await self.capture(comment)
        replies = await self.session.execute(
            select(DocCommentReply.sequence, Block)
            .join(Block, Block.id == DocCommentReply.block_id)
            .where(DocCommentReply.comment_id == comment.id)
            .order_by(DocCommentReply.sequence)
        )
        return {
            "comment": block_out(comment),
            "revision": thread.revision,
            "state": thread.state,
            "replies": [
                {"sequence": sequence, "comment": block_out(block)}
                for sequence, block in replies
            ],
        }

    async def mutate(
        self,
        *,
        room_id: uuid.UUID,
        project_id: uuid.UUID,
        comment_id: uuid.UUID,
        author: str,
        expected_revision: int,
        action: str,
        content: str | None = None,
    ) -> dict:
        await DocumentJournal(self.session).lock(room_id)
        comment = await self.root(room_id, comment_id)
        thread = await self.capture(comment)
        await self.session.refresh(thread)
        if thread.revision != expected_revision:
            raise ConflictError(say("commentThreadUpdated"))
        if action == "reply":
            if thread.state != "open":
                raise ConflictError(say("commentResolvedReopenFirst"))
            assert content is not None
            reply = await self.blocks.add(
                project_id=project_id,
                topic_id=room_id,
                author=author,
                author_type=AuthorType.participant,
                content=content,
                kind=BlockKind.comment,
                reply_to=comment.id,
            )
            thread.reply_count += 1
            self.session.add(
                DocCommentReply(
                    block_id=reply.id,
                    comment_id=comment.id,
                    sequence=thread.reply_count,
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
