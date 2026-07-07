"""Data access for the block substrate (万物皆块 §2.1).

Chat messages are ``block`` rows with ``thread_id`` set. This repository is
data-access only — no business logic, no authorization.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind


class BlockRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_message(
        self,
        *,
        thread_id: int,
        project_id: int | None,
        author_id: int,
        text: str,
        reply_to_id: int | None = None,
    ) -> Block:
        block = Block(
            project_id=project_id,
            thread_id=thread_id,
            kind=BlockKind.MESSAGE,
            author_id=author_id,
            reply_to_id=reply_to_id,
            content=text,
            created_at=datetime.now(UTC),
        )
        self._session.add(block)
        await self._session.flush()
        return block

    async def messages_since(self, thread_id: int, after_id: int = 0) -> list[Block]:
        rows = (
            await self._session.execute(
                select(Block)
                .where(
                    Block.thread_id == thread_id,
                    Block.kind == BlockKind.MESSAGE,
                    Block.id > after_id,
                )
                .order_by(Block.id)
            )
        ).scalars()
        return list(rows)

    async def latest(self, thread_id: int) -> Block | None:
        return (
            await self._session.execute(
                select(Block)
                .where(Block.thread_id == thread_id, Block.kind == BlockKind.MESSAGE)
                .order_by(Block.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
