"""巩固练习那几张表的读写（§ learning analysis, step 4，``practice_models``）。

一个人自己的行按 ``(space_id, user_id)`` 读，路由从不收「看谁」；老师那一侧只读整体
（做过巩固题的人里几人升档、最近一次推送几人）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import (
    Space,
    SpaceKnowledgePoint,
    SpaceLearningJudgment,
    SpaceLearningMisconception,
    SpaceLearningRun,
)
from app.domain.space.practice_models import (
    SpaceLearningExplanation,
    SpaceLearningMemberDay,
    SpaceLearningPractice,
    SpaceLearningPush,
    SpaceLearningReview,
)


@dataclass(frozen=True)
class PracticeOutcome:
    """一个人在一个知识点上做过的一组题的前后档位（老师那一侧数升档用）。"""

    user_id: int
    level_before: str | None
    level_after: str | None
    completed_at: datetime


class LearningPracticeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- 一组题 -----------------------------------------------------------

    async def get(
        self, *, space_id: int, user_id: int, practice_id: int
    ) -> SpaceLearningPractice | None:
        """这个人自己的这一组；别人的、或不在这块板的，一律当作没有。"""
        return (
            await self._session.execute(
                select(SpaceLearningPractice)
                .where(
                    SpaceLearningPractice.id == practice_id,
                    SpaceLearningPractice.space_id == space_id,
                    SpaceLearningPractice.user_id == user_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def open_set(
        self, *, space_id: int, user_id: int, point_id: int
    ) -> SpaceLearningPractice | None:
        return (
            await self._session.execute(
                select(SpaceLearningPractice)
                .where(
                    SpaceLearningPractice.space_id == space_id,
                    SpaceLearningPractice.user_id == user_id,
                    SpaceLearningPractice.knowledge_point_id == point_id,
                    SpaceLearningPractice.completed_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def open_sets(
        self, *, space_id: int, user_id: int
    ) -> list[SpaceLearningPractice]:
        """这个人没做完的几组（排着队的、出好了没做完的），新的在前。"""
        rows = await self._session.scalars(
            select(SpaceLearningPractice)
            .join(
                SpaceKnowledgePoint,
                SpaceKnowledgePoint.id == SpaceLearningPractice.knowledge_point_id,
            )
            .where(
                SpaceLearningPractice.space_id == space_id,
                SpaceLearningPractice.user_id == user_id,
                SpaceLearningPractice.completed_at.is_(None),
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
            .order_by(SpaceLearningPractice.created_at.desc())
        )
        return list(rows.all())

    async def start(
        self,
        *,
        space_id: int,
        user_id: int,
        point_id: int,
        source: str,
        now: datetime,
        push_id: int | None = None,
    ) -> SpaceLearningPractice:
        row = SpaceLearningPractice(
            space_id=space_id,
            user_id=user_id,
            knowledge_point_id=point_id,
            source=source,
            push_id=push_id,
            questions=[],
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def drop(self, row: SpaceLearningPractice) -> None:
        """「换一组」：没做完的那一组不要了（做完的从不删）。"""
        await self._session.execute(
            delete(SpaceLearningPractice).where(
                SpaceLearningPractice.id == row.id,
                SpaceLearningPractice.completed_at.is_(None),
            )
        )
        await self._session.flush()

    async def queue_for(
        self, *, space_id: int, point_id: int, user_ids: list[int], push_id: int
    ) -> int:
        """给这些人各排一组（已经有一组没做完的跳过），返回新排上的人数。"""
        if not user_ids:
            return 0
        now = datetime.now(UTC)
        stmt = (
            pg_insert(SpaceLearningPractice)
            .values(
                [
                    {
                        "space_id": space_id,
                        "user_id": uid,
                        "knowledge_point_id": point_id,
                        "source": "pushed",
                        "push_id": push_id,
                        "questions": [],
                        "created_at": now,
                    }
                    for uid in user_ids
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["space_id", "user_id", "knowledge_point_id"],
                index_where=SpaceLearningPractice.completed_at.is_(None),
            )
            .returning(SpaceLearningPractice.id)
        )
        return len((await self._session.execute(stmt)).all())

    async def queued(self, *, limit: int) -> list[SpaceLearningPractice]:
        """开着学情分析的板上还排着队（老师推送、还没出题）的组，先排的先出。"""
        rows = await self._session.scalars(
            select(SpaceLearningPractice)
            # 关了学情分析的板上排着的不算：它们等板重新打开，不占这一批的名额。
            .join(Space, Space.id == SpaceLearningPractice.space_id)
            .where(
                SpaceLearningPractice.generated_at.is_(None),
                SpaceLearningPractice.completed_at.is_(None),
                Space.learning_analysis_enabled.is_(True),
            )
            .order_by(SpaceLearningPractice.created_at.asc())
            .limit(limit)
        )
        return list(rows.all())

    async def outcomes(self, space_id: int, point_id: int) -> list[PracticeOutcome]:
        """这个知识点上做完的每一组的前后档位，先做的在前。"""
        rows = await self._session.execute(
            select(
                SpaceLearningPractice.user_id,
                SpaceLearningPractice.level_before,
                SpaceLearningPractice.level_after,
                SpaceLearningPractice.completed_at,
            )
            .where(
                SpaceLearningPractice.space_id == space_id,
                SpaceLearningPractice.knowledge_point_id == point_id,
                SpaceLearningPractice.completed_at.is_not(None),
            )
            .order_by(SpaceLearningPractice.completed_at.asc())
        )
        return [
            PracticeOutcome(
                user_id=int(uid),
                level_before=before,
                level_after=after,
                completed_at=at,
            )
            for uid, before, after, at in rows.all()
        ]

    async def recent_stems(
        self, *, space_id: int, user_id: int, point_id: int, sets: int = 2
    ) -> list[str]:
        """这个人在这个知识点上最近做完的几组的题干（出新题时让模型别重复）。"""
        rows = await self._session.scalars(
            select(SpaceLearningPractice.questions)
            .where(
                SpaceLearningPractice.space_id == space_id,
                SpaceLearningPractice.user_id == user_id,
                SpaceLearningPractice.knowledge_point_id == point_id,
                SpaceLearningPractice.completed_at.is_not(None),
            )
            .order_by(SpaceLearningPractice.completed_at.desc())
            .limit(sets)
        )
        return [
            str(item.get("stem"))
            for questions in rows.all()
            for item in (questions or [])
            if isinstance(item, dict) and item.get("stem")
        ]

    # --- 判断（第 2 步的表，做完一组时改写） -----------------------------

    async def judgment(
        self, *, space_id: int, user_id: int, point_id: int
    ) -> SpaceLearningJudgment | None:
        return (
            await self._session.execute(
                select(SpaceLearningJudgment)
                .join(
                    SpaceKnowledgePoint,
                    SpaceKnowledgePoint.id == SpaceLearningJudgment.knowledge_point_id,
                )
                .where(
                    SpaceLearningJudgment.space_id == space_id,
                    SpaceLearningJudgment.user_id == user_id,
                    SpaceLearningJudgment.knowledge_point_id == point_id,
                    SpaceKnowledgePoint.deleted_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def misconception_label(self, misconception_id: int | None) -> str | None:
        if misconception_id is None:
            return None
        return await self._session.scalar(
            select(SpaceLearningMisconception.label).where(
                SpaceLearningMisconception.id == misconception_id
            )
        )

    async def stuck_members(self, *, space_id: int, point_id: int) -> list[int]:
        """当前卡在这个知识点上的人：不稳或薄弱，且没被本人标「不准」。"""
        rows = await self._session.scalars(
            select(SpaceLearningJudgment.user_id).where(
                SpaceLearningJudgment.space_id == space_id,
                SpaceLearningJudgment.knowledge_point_id == point_id,
                SpaceLearningJudgment.level.in_(("shaky", "weak")),
                SpaceLearningJudgment.disputed_at.is_(None),
            )
        )
        return sorted({int(uid) for uid in rows.all()})

    # --- 推送 -------------------------------------------------------------

    async def record_push(
        self, *, space_id: int, point_id: int, pushed_by: int, now: datetime
    ) -> SpaceLearningPush:
        row = SpaceLearningPush(
            space_id=space_id,
            knowledge_point_id=point_id,
            pushed_by=pushed_by,
            recipients=0,
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def last_push(self, space_id: int, point_id: int) -> SpaceLearningPush | None:
        return (
            await self._session.execute(
                select(SpaceLearningPush)
                .where(
                    SpaceLearningPush.space_id == space_id,
                    SpaceLearningPush.knowledge_point_id == point_id,
                )
                .order_by(
                    SpaceLearningPush.created_at.desc(), SpaceLearningPush.id.desc()
                )
                .limit(1)
            )
        ).scalar_one_or_none()

    # --- 复习排期 ---------------------------------------------------------

    async def review(
        self, *, space_id: int, user_id: int, point_id: int
    ) -> SpaceLearningReview | None:
        return (
            await self._session.execute(
                select(SpaceLearningReview)
                .where(
                    SpaceLearningReview.space_id == space_id,
                    SpaceLearningReview.user_id == user_id,
                    SpaceLearningReview.knowledge_point_id == point_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def reviews(
        self, *, space_id: int, user_id: int
    ) -> list[SpaceLearningReview]:
        """这个人还在排期里的知识点（巩固住了的不算），先到期的在前。"""
        rows = await self._session.scalars(
            select(SpaceLearningReview)
            .join(
                SpaceKnowledgePoint,
                SpaceKnowledgePoint.id == SpaceLearningReview.knowledge_point_id,
            )
            .where(
                SpaceLearningReview.space_id == space_id,
                SpaceLearningReview.user_id == user_id,
                SpaceLearningReview.due_on.is_not(None),
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
            .order_by(SpaceLearningReview.due_on.asc(), SpaceLearningReview.id.asc())
        )
        return list(rows.all())

    async def schedule(
        self,
        *,
        space_id: int,
        user_id: int,
        point_id: int,
        streak: int,
        due_on: date | None,
        now: datetime,
    ) -> None:
        stmt = pg_insert(SpaceLearningReview).values(
            space_id=space_id,
            user_id=user_id,
            knowledge_point_id=point_id,
            streak=streak,
            due_on=due_on,
            last_practiced_at=now,
            created_at=now,
            updated_at=now,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "user_id", "knowledge_point_id"],
                set_={
                    "streak": stmt.excluded.streak,
                    "due_on": stmt.excluded.due_on,
                    "last_practiced_at": stmt.excluded.last_practiced_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    # --- 讲解缓存 ---------------------------------------------------------

    async def explanation(
        self, *, space_id: int, user_id: int, point_id: int
    ) -> SpaceLearningExplanation | None:
        return (
            await self._session.execute(
                select(SpaceLearningExplanation)
                .where(
                    SpaceLearningExplanation.space_id == space_id,
                    SpaceLearningExplanation.user_id == user_id,
                    SpaceLearningExplanation.knowledge_point_id == point_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def write_explanation(
        self,
        *,
        space_id: int,
        user_id: int,
        point_id: int,
        body: str,
        basis_at: datetime,
        now: datetime,
    ) -> None:
        stmt = pg_insert(SpaceLearningExplanation).values(
            space_id=space_id,
            user_id=user_id,
            knowledge_point_id=point_id,
            body=body,
            basis_at=basis_at,
            generated_at=now,
            created_at=now,
            updated_at=now,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "user_id", "knowledge_point_id"],
                set_={
                    "body": stmt.excluded.body,
                    "basis_at": stmt.excluded.basis_at,
                    "generated_at": stmt.excluded.generated_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    # --- 每人每天的调用 ---------------------------------------------------

    async def calls_today(self, *, space_id: int, user_id: int, day: date) -> int:
        return int(
            await self._session.scalar(
                select(func.coalesce(func.max(SpaceLearningMemberDay.calls), 0)).where(
                    SpaceLearningMemberDay.space_id == space_id,
                    SpaceLearningMemberDay.user_id == user_id,
                    SpaceLearningMemberDay.day == day,
                )
            )
            or 0
        )

    async def count_call(self, *, space_id: int, user_id: int, day: date) -> None:
        now = datetime.now(UTC)
        stmt = pg_insert(SpaceLearningMemberDay).values(
            space_id=space_id,
            user_id=user_id,
            day=day,
            calls=1,
            created_at=now,
            updated_at=now,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["space_id", "user_id", "day"],
                set_={
                    "calls": SpaceLearningMemberDay.calls + 1,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    async def refund_call(self, *, space_id: int, user_id: int, day: date) -> None:
        """给这一次调用退一格每人每天的额度。

        一次他要的调用没出成东西（模型没答、答案读不出来）时退回去：不让学生为一次
        白花的调用扣掉当天的一格。地板是零，退不成负数。整块板的 ``count_board_call``
        不退 —— 那块刹车算的是真花出去的调用，调用确实发生了。
        """
        await self._session.execute(
            update(SpaceLearningMemberDay)
            .where(
                SpaceLearningMemberDay.space_id == space_id,
                SpaceLearningMemberDay.user_id == user_id,
                SpaceLearningMemberDay.day == day,
                SpaceLearningMemberDay.calls > 0,
            )
            .values(
                calls=SpaceLearningMemberDay.calls - 1, updated_at=datetime.now(UTC)
            )
        )

    async def count_board_call(self, run_id: int) -> None:
        """One more call against the board's day (``SpaceLearningRun.calls``),
        as one statement: the nightly pass may be counting on the same row."""
        await self._session.execute(
            update(SpaceLearningRun)
            .where(SpaceLearningRun.id == run_id)
            .values(calls=SpaceLearningRun.calls + 1, updated_at=datetime.now(UTC))
        )

    async def set_recipients(self, push: SpaceLearningPush, recipients: int) -> None:
        await self._session.execute(
            update(SpaceLearningPush)
            .where(SpaceLearningPush.id == push.id)
            .values(recipients=recipients)
        )
