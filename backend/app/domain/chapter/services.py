"""Chapters: the messages that mark where a section of a conversation starts.

Anyone who reads a conversation reads its chapters. Anyone who may speak where
the section starts may mark one: in a channel's main line that is its members
(a task and a 支线 are spoken in by whoever may read the room, so anyone there
may organize them). Nothing is said in the conversation when a chapter is
added or taken away — it is a reading aid, not an event.
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.chapter.models import Chapter
from app.domain.membership.ports import ProjectTeam
from app.domain.room_task.place import Place
from app.domain.topic.models import TopicStatus, is_private_chat
from app.domain.topic_membership.services import TopicMemberService


class Chapters:
    def __init__(self, session: AsyncSession, *, team: ProjectTeam):
        self._session = session
        self._team = team

    async def of_conversation(
        self, conversation_id: uuid.UUID
    ) -> list[tuple[Chapter, Block]]:
        """A conversation's chapters with the messages they start, in reading
        order: the order the outline is numbered in."""
        rows = await self._session.execute(
            select(Chapter, Block)
            .join(Block, Block.id == Chapter.block_id)
            .where(Chapter.conversation_id == conversation_id)
            .order_by(Block.created_at.asc(), Block.seq.asc())
        )
        return [(chapter, block) for chapter, block in rows.all()]

    async def add(
        self, place: Place, block_id: uuid.UUID, *, by: str
    ) -> bool:
        """``by`` marks a message as a chapter. True when this added one, False
        when it already was one (and said nothing)."""
        await self._may_chapter(place, by)
        block = await self._message_of(place, block_id)
        inserted = await self._session.scalar(
            insert(Chapter)
            .values(
                id=uuid.uuid4(),
                conversation_id=place.conversation_id,
                block_id=block.id,
                pinned_by=by,
            )
            .on_conflict_do_nothing(index_elements=[Chapter.block_id])
            .returning(Chapter.id)
        )
        return inserted is not None

    async def remove(self, place: Place, block_id: uuid.UUID, *, by: str) -> None:
        """``by`` takes the chapter off. Nothing when it was not one."""
        await self._may_chapter(place, by)
        await self._session.execute(
            delete(Chapter).where(
                Chapter.conversation_id == place.conversation_id,
                Chapter.block_id == block_id,
            )
        )

    async def _may_chapter(self, place: Place, by: str) -> None:
        if is_private_chat(place.room):
            raise ValidationError(say("chapterNotInPrivateChat"))
        if place.room.status == TopicStatus.archived:
            raise ForbiddenError(say("roomArchivedUnarchiveFirst"))
        # A task and a 支线: everyone who may read the room may speak in them
        # and so may organize them (the route has already checked the read).
        if place.task is not None or place.thread is not None:
            return
        if not await TopicMemberService(self._session).may_speak(
            place.room, by, team=self._team
        ):
            raise ForbiddenError(say("channelJoinToChapter"))

    async def _message_of(self, place: Place, block_id: uuid.UUID) -> Block:
        block = await BlockRepository(self._session).get(block_id)
        if block is None or block.conversation_id != place.conversation_id:
            raise NotFoundError(say("chapterMessageNotHere"))
        if block.kind != BlockKind.message:
            raise ValidationError(say("chapterMessagesOnly"))
        return block
