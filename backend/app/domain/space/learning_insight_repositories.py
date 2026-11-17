"""学情分析的结果行（§ learning analysis, step 2）与它读的对话。

Four tables of this domain — the current judgments, the per-题目 cursors, the
filed questions and the nightly run counters — and the reads of conversation
the nightly analysis makes. Those reads take the same rule as 数据 › 学习
(``learning_service.member_words`` / ``readable_rooms``): a member's own
messages in a 题目's project, private chats and members-only channels left out,
芝士 and the project's mentors never counted as the member. They join the
``blocks``, ``topic``, ``project`` and ``task`` models directly, as
``learning_service`` does; no other domain's repository is imported
(``tests/unit/test_domain_import_guard.py``).

The 「space → task」 edge these joins take is one the tree already has (see
``learning_analysis_repositories``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import and_, func, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.authorship import participant_blocks
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.services import room_column
from app.domain.identity.handles import agent_handle_column
from app.domain.project.models import Project
from app.domain.space.learning_service import member_words, readable_rooms
from app.domain.space.models import (
    Space,
    SpaceKnowledgePoint,
    SpaceLearningCursor,
    SpaceLearningJudgment,
    SpaceLearningQuestion,
    SpaceLearningRun,
)
from app.domain.task.models import Task, TaskKnowledgePoint
from app.domain.topic.models import Topic
from app.domain.user.models import User


@dataclass(frozen=True)
class PendingPair:
    """One member with words in one 题目 the analysis has not read yet."""

    user_id: int
    handle: str
    task_id: int


@dataclass(frozen=True)
class Said:
    """One message as the analysis reads it."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    author: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class Excerpt:
    """One of the member's messages shown as evidence, with the 题目 it is in."""

    block_id: uuid.UUID
    task_id: int | None
    task_name: str
    content: str
    created_at: datetime


class LearningConversationReads:
    """What the nightly analysis reads out of the conversations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enabled_spaces(self) -> list[Space]:
        """Every board with the analysis switched on, oldest first."""
        rows = await self._session.scalars(
            select(Space)
            .where(Space.learning_analysis_enabled.is_(True))
            .order_by(Space.id.asc())
        )
        return list(rows.all())

    async def pending_pairs(
        self, *, space_id: int, since: datetime | None
    ) -> list[PendingPair]:
        """(member, 题目) pairs with words past their cursor, in this board.

        One statement: the member's messages in the board's 题目 projects, said
        after ``since`` (the moment the analysis was switched on), joined to the
        person who wrote them and to that pair's cursor, kept only past it.
        """
        cursor = SpaceLearningCursor
        stmt = (
            select(User.id, Block.author, Project.external_task_id)
            .select_from(Block)
            .join(Topic, Topic.id == room_column(Block.conversation_id))
            .join(Project, Project.id == Block.project_id)
            .join(Task, Task.id == Project.external_task_id)
            .join(User, User.username == Block.author)
            .outerjoin(
                cursor,
                and_(
                    cursor.space_id == space_id,
                    cursor.user_id == User.id,
                    cursor.task_id == Project.external_task_id,
                ),
            )
            .where(
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
                *member_words(),
                *readable_rooms(),
                or_(
                    cursor.id.is_(None),
                    tuple_(Block.created_at, Block.id)
                    > tuple_(cursor.last_block_at, cursor.last_block_id),
                ),
            )
            .group_by(User.id, Block.author, Project.external_task_id)
            # The pair whose oldest unread word is oldest goes first: a capped
            # night leaves the newest talk, not the same people, for tomorrow.
            .order_by(func.min(Block.created_at).asc())
        )
        if since is not None:
            stmt = stmt.where(Block.created_at >= since)
        rows = (await self._session.execute(stmt)).all()
        return [
            PendingPair(user_id=int(uid), handle=str(handle), task_id=int(task_id))
            for uid, handle, task_id in rows
        ]

    async def member_messages(
        self,
        *,
        task_id: int,
        handle: str,
        since: datetime | None,
        after: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[Said]:
        """The member's next ``limit`` messages in this 题目, oldest first."""
        stmt = (
            select(Block)
            .select_from(Block)
            .join(Topic, Topic.id == room_column(Block.conversation_id))
            .join(Project, Project.id == Block.project_id)
            .where(
                Project.external_task_id == task_id,
                Block.author == handle,
                *member_words(),
                *readable_rooms(),
            )
            .order_by(Block.created_at.asc(), Block.id.asc())
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(Block.created_at >= since)
        if after is not None:
            at, block_id = after
            stmt = stmt.where(
                tuple_(Block.created_at, Block.id)
                > tuple_(literal(at), literal(block_id))
            )
        rows = (await self._session.scalars(stmt)).all()
        return [_said(block) for block in rows]

    async def agent_replies(
        self, *, conversation_ids: Sequence[uuid.UUID], start: datetime, limit: int
    ) -> list[Said]:
        """芝士's messages in these conversations from ``start`` on, oldest first.

        Read only as context for the member's own words (the analysis keeps a
        reply only where it follows one of them), never as evidence.
        """
        if not conversation_ids:
            return []
        rows = (
            await self._session.scalars(
                select(Block)
                .where(
                    Block.conversation_id.in_(list(conversation_ids)),
                    Block.kind == BlockKind.message,
                    participant_blocks(),
                    agent_handle_column(Block.author),
                    Block.created_at >= start,
                )
                .order_by(Block.created_at.asc(), Block.id.asc())
                .limit(limit)
            )
        ).all()
        return [_said(block) for block in rows]

    async def task_points(self, task_id: int) -> list[SpaceKnowledgePoint]:
        """The live 知识点 this 题目 files under, in list order."""
        rows = await self._session.scalars(
            select(SpaceKnowledgePoint)
            .join(
                TaskKnowledgePoint,
                TaskKnowledgePoint.knowledge_point_id == SpaceKnowledgePoint.id,
            )
            .where(
                TaskKnowledgePoint.task_id == task_id,
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
            .order_by(
                SpaceKnowledgePoint.display_order.asc(), SpaceKnowledgePoint.id.asc()
            )
        )
        return list(rows.all())

    async def task_names(self, task_ids: Sequence[int]) -> dict[int, str]:
        if not task_ids:
            return {}
        rows = await self._session.execute(
            select(Task.id, Task.name).where(Task.id.in_(list(task_ids)))
        )
        return {int(tid): str(name) for tid, name in rows.all()}

    async def excerpts(
        self, *, handle: str, block_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, Excerpt]:
        """The member's own messages among ``block_ids``, with their 题目.

        Written by ``handle`` and still there: a block that was removed, or one
        that is somehow not theirs, is not shown — evidence on the member's page
        is only ever their own words.
        """
        if not block_ids:
            return {}
        rows = await self._session.execute(
            select(Block, Task.id, Task.name)
            .join(Project, Project.id == Block.project_id)
            .outerjoin(Task, Task.id == Project.external_task_id)
            .where(Block.id.in_(list(block_ids)), Block.author == handle)
        )
        out: dict[uuid.UUID, Excerpt] = {}
        for block, task_id, task_name in rows.all():
            out[block.id] = Excerpt(
                block_id=block.id,
                task_id=int(task_id) if task_id is not None else None,
                task_name=str(task_name or ""),
                content=block.content or "",
                created_at=block.created_at,
            )
        return out


def _said(block: Block) -> Said:
    return Said(
        id=block.id,
        conversation_id=block.conversation_id,
        author=block.author,
        content=block.content or "",
        created_at=block.created_at,
    )


class SpaceLearningJudgmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_member(
        self, *, space_id: int, user_id: int
    ) -> list[SpaceLearningJudgment]:
        """This member's current judgments on the board's live 知识点."""
        rows = await self._session.scalars(
            select(SpaceLearningJudgment)
            .join(
                SpaceKnowledgePoint,
                SpaceKnowledgePoint.id == SpaceLearningJudgment.knowledge_point_id,
            )
            .where(
                SpaceLearningJudgment.space_id == space_id,
                SpaceLearningJudgment.user_id == user_id,
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
            # Rows are rewritten by upserts the identity map does not see.
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    async def get(
        self, *, space_id: int, user_id: int, judgment_id: int
    ) -> SpaceLearningJudgment | None:
        return (
            await self._session.execute(
                select(SpaceLearningJudgment).where(
                    SpaceLearningJudgment.id == judgment_id,
                    SpaceLearningJudgment.space_id == space_id,
                    SpaceLearningJudgment.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    async def write(
        self,
        *,
        space_id: int,
        user_id: int,
        knowledge_point_id: int,
        level: str,
        summary: str,
        evidence: list[uuid.UUID],
        now: datetime,
        misconception_id: int | None = None,
    ) -> None:
        """The point's judgment becomes this one; a dispute on it is cleared,
        because this IS the re-check the dispute asked for."""
        stmt = pg_insert(SpaceLearningJudgment).values(
            space_id=space_id,
            user_id=user_id,
            knowledge_point_id=knowledge_point_id,
            level=level,
            summary=summary,
            evidence_block_ids=evidence,
            misconception_id=misconception_id,
            disputed_at=None,
            analyzed_at=now,
            created_at=now,
            updated_at=now,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "user_id", "knowledge_point_id"],
                set_={
                    "level": stmt.excluded.level,
                    "summary": stmt.excluded.summary,
                    "evidence_block_ids": stmt.excluded.evidence_block_ids,
                    "misconception_id": stmt.excluded.misconception_id,
                    "disputed_at": None,
                    "analyzed_at": stmt.excluded.analyzed_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    async def dispute(self, judgment: SpaceLearningJudgment, *, now: datetime) -> None:
        """Mark it 「不准」. The first moment is kept on a second click."""
        if judgment.disputed_at is None:
            judgment.disputed_at = now
            judgment.updated_at = now
            self._session.add(judgment)
        await self._session.flush()


class SpaceLearningCursorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, *, space_id: int, user_id: int, task_id: int
    ) -> SpaceLearningCursor | None:
        return (
            await self._session.execute(
                select(SpaceLearningCursor)
                .where(
                    SpaceLearningCursor.space_id == space_id,
                    SpaceLearningCursor.user_id == user_id,
                    SpaceLearningCursor.task_id == task_id,
                )
                # Moved by an upsert the identity map does not see.
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def list_for_member(
        self, *, space_id: int, user_id: int
    ) -> list[SpaceLearningCursor]:
        rows = await self._session.scalars(
            select(SpaceLearningCursor)
            .where(
                SpaceLearningCursor.space_id == space_id,
                SpaceLearningCursor.user_id == user_id,
            )
            .order_by(SpaceLearningCursor.task_id.asc())
        )
        return list(rows.all())

    async def advance(
        self,
        *,
        space_id: int,
        user_id: int,
        task_id: int,
        last: Said,
        read: int,
        now: datetime,
    ) -> None:
        """Move the pair's cursor to ``last`` and count ``read`` more messages."""
        stmt = pg_insert(SpaceLearningCursor).values(
            space_id=space_id,
            user_id=user_id,
            task_id=task_id,
            last_block_at=last.created_at,
            last_block_id=last.id,
            messages_read=read,
            created_at=now,
            updated_at=now,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "user_id", "task_id"],
                set_={
                    "last_block_at": stmt.excluded.last_block_at,
                    "last_block_id": stmt.excluded.last_block_id,
                    "messages_read": SpaceLearningCursor.messages_read + read,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )


class SpaceLearningQuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def file(
        self,
        *,
        space_id: int,
        user_id: int,
        task_id: int,
        block_id: uuid.UUID,
        knowledge_point_id: int | None,
        kind: str,
        gist: str,
        asked_at: datetime,
        now: datetime,
    ) -> None:
        """File one question; a message filed before is filed again."""
        stmt = pg_insert(SpaceLearningQuestion).values(
            space_id=space_id,
            user_id=user_id,
            task_id=task_id,
            block_id=block_id,
            knowledge_point_id=knowledge_point_id,
            kind=kind,
            gist=gist,
            asked_at=asked_at,
            created_at=now,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["block_id"],
                set_={
                    "knowledge_point_id": stmt.excluded.knowledge_point_id,
                    "kind": stmt.excluded.kind,
                    "gist": stmt.excluded.gist,
                },
            )
        )

    async def list_for_member(
        self, *, space_id: int, user_id: int
    ) -> list[SpaceLearningQuestion]:
        """The member's filed questions in this board, newest first."""
        rows = await self._session.scalars(
            select(SpaceLearningQuestion)
            .where(
                SpaceLearningQuestion.space_id == space_id,
                SpaceLearningQuestion.user_id == user_id,
            )
            .order_by(SpaceLearningQuestion.asked_at.desc())
        )
        return list(rows.all())


class SpaceLearningRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def of_day(self, *, space_id: int, day: date) -> SpaceLearningRun:
        """The board's row for ``day``, written the first time it is asked for."""
        now = datetime.now(UTC)
        await self._session.execute(
            pg_insert(SpaceLearningRun)
            .values(
                space_id=space_id,
                run_date=day,
                calls=0,
                capped=False,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["space_id", "run_date"])
        )
        row = (
            await self._session.execute(
                select(SpaceLearningRun)
                .where(
                    SpaceLearningRun.space_id == space_id,
                    SpaceLearningRun.run_date == day,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return row

    async def count_call(self, run: SpaceLearningRun) -> None:
        run.calls += 1
        run.updated_at = datetime.now(UTC)
        self._session.add(run)
        await self._session.flush()

    async def finish(self, run: SpaceLearningRun, *, capped: bool) -> None:
        now = datetime.now(UTC)
        run.capped = capped
        run.finished_at = now
        run.updated_at = now
        self._session.add(run)
        await self._session.flush()
