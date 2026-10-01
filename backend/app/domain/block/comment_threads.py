"""Transactional room-document comment threads over the existing block tree.

The room journal lock serializes canonical anchor capture and thread mutations.
The caller owns authorization and commit; no mutation emits document events.
"""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import ConflictError, NotFoundError
from app.domain.block.comment_models import DocCommentReply, DocCommentThread
from app.domain.block.doc_selection import raw_blocks
from app.domain.block.models import AuthorType, Block, BlockKind
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
            raise NotFoundError("文档原评论不存在")
        return row

    async def capture(
        self, comment: Block, *, current: bool = False
    ) -> DocCommentThread:
        row = await self.session.get(DocCommentThread, comment.id)
        if row is not None:
            return row
        node = await self.blocks.get(comment.reply_to) if comment.reply_to else None
        anchor = {
            "node_id": str(comment.reply_to) if comment.reply_to else None,
            "quote": comment.anchor_quote,
            "node_content": None,
            "document_id": None,
            "base_version": None,
            "start": None,
            "end": None,
            "offset_unit": "utf8-bytes",
        }
        if node is not None and node.kind == BlockKind.doc_node:
            anchor["node_content"] = node.content
            anchor["document_id"] = (
                str(node.struct_parent) if node.struct_parent else None
            )
            if current:
                doc = await self.blocks.doc_root(comment.topic_id)
                nodes = await self.blocks.list_doc_nodes(comment.topic_id)
                if doc is not None:
                    spans = raw_blocks(doc.content)
                    if len(nodes) == len(spans) and all(
                        item.content == span.normalized and item.struct_parent == doc.id
                        for item, span in zip(nodes, spans, strict=True)
                    ):
                        for item, span in zip(nodes, spans, strict=True):
                            if item.id == node.id:
                                anchor.update(
                                    document_id=str(doc.id),
                                    base_version=doc.doc_version,
                                    start=span.start,
                                    end=span.end,
                                )
                                break
        row = DocCommentThread(comment_id=comment.id, anchor=anchor)
        self.session.add(row)
        await self.session.flush()
        return row

    async def describe(self, comment: Block, *, summary: bool = False) -> dict:
        thread = await self.capture(comment)
        result = {
            "comment": block_out(comment),
            "revision": thread.revision,
            "state": thread.state,
            "anchor": thread.anchor,
        }
        if summary:
            result["reply_count"] = thread.reply_count
        else:
            replies = await self.session.execute(
                select(DocCommentReply.sequence, Block)
                .join(Block, Block.id == DocCommentReply.block_id)
                .where(DocCommentReply.comment_id == comment.id)
                .order_by(DocCommentReply.sequence)
            )
            result["replies"] = [
                {"sequence": sequence, "comment": block_out(block)}
                for sequence, block in replies
            ]
        return result

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
            raise ConflictError("评论线程已更新，请重新读取")
        if action == "reply":
            if thread.state != "open":
                raise ConflictError("评论已解决，请先重开")
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
                raise ConflictError("评论已经处于目标状态")
            thread.state = target
        thread.revision += 1
        await self.session.flush()
        return await self.describe(comment)
