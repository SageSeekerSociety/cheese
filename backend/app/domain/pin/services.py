"""Pinning: a channel's members keep a message or a file at the top of its
overview, where everyone who opens the channel sees it.

Pinning is in place — on the message or the file in the main line — and says so
in the main line, so a pin is something somebody did, not something that
appeared. Unpinning is quiet: it takes away what the overview shows and nothing
more.
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.block.about import EventAbout, landing
from app.domain.block.authorship import AuthorType
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.pin.models import Pin
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

#: What can be pinned: what someone said, and the files sent with it.
PINNABLE = (BlockKind.message, BlockKind.attachment, BlockKind.artifact)
#: The block's meta key an event line names the pinned block by, so a screen
#: can go to it.
PINNED_KEY = "pinned_block_id"


class Pins:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def of_room(self, room_id: uuid.UUID) -> list[tuple[Pin, Block]]:
        """The channel's pins with what they pin, the latest pinned first."""
        rows = await self._session.execute(
            select(Pin, Block)
            .join(Block, Block.id == Pin.block_id)
            .where(Pin.room_id == room_id)
            .order_by(Pin.pinned_at.desc(), Pin.id)
        )
        return [(pin, block) for pin, block in rows.all()]

    async def pin(self, room: Topic, block_id: uuid.UUID, *, by: str) -> Block | None:
        """``by`` pins a block of the channel's main line; the line that says so
        is returned for the caller to send out. Pinning what is already pinned
        changes nothing and says nothing (None)."""
        block = await self._pinnable(room, block_id, by=by)
        inserted = await self._session.scalar(
            insert(Pin)
            .values(id=uuid.uuid4(), room_id=room.id, block_id=block.id, pinned_by=by)
            .on_conflict_do_nothing(index_elements=[Pin.block_id])
            .returning(Pin.id)
        )
        if inserted is None:
            return None
        return await self._say_pinned(room, block, by=by)

    async def unpin(self, room: Topic, block_id: uuid.UUID, *, by: str) -> None:
        """``by`` takes a pin off. Quiet, and nothing when it was not pinned."""
        await self._may_pin(room, by)
        await self._session.execute(
            delete(Pin).where(Pin.room_id == room.id, Pin.block_id == block_id)
        )

    async def _may_pin(self, room: Topic, by: str) -> None:
        if room.is_private:
            raise ValidationError(say("pinChannelsOnly"))
        if room.status == TopicStatus.archived:
            raise ForbiddenError(say("roomArchivedUnarchiveFirst"))
        if not await TopicMemberService(self._session).may_speak(room, by):
            raise ForbiddenError(say("channelJoinToPin"))

    async def _pinnable(self, room: Topic, block_id: uuid.UUID, *, by: str) -> Block:
        await self._may_pin(room, by)
        block = await BlockRepository(self._session).get(block_id)
        if block is None or block.conversation_id != room.id:
            raise NotFoundError(say("pinMainLineOnly"))
        if block.kind not in PINNABLE:
            raise ValidationError(say("pinMessagesAndFiles"))
        return block

    async def _say_pinned(self, room: Topic, block: Block, *, by: str) -> Block:
        note = (
            say("pinnedMessage", actor=f"<@{by}>")
            if block.kind == BlockKind.message
            else say("pinnedFile", actor=f"<@{by}>", name=_file_name(block.content))
        )
        landed = landing(EventAbout.room, project_id=room.project_id, room_id=room.id)
        return await BlockRepository(self._session).add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author=by,
            author_type=AuthorType.platform,
            content=note,
            kind=BlockKind.event,
            meta={"platform": True, PINNED_KEY: str(block.id)},
        )


def _file_name(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]
