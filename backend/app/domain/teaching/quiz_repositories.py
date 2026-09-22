"""小测的读写。判分与可见性不在这里 —— 那是 service 的事，这里只有查询。"""

from datetime import UTC, datetime

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.teaching.quiz_models import (
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
)


class QuizRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_in_space(self, *, space_id: int, quiz_id: int) -> Quiz | None:
        stmt: Select[tuple[Quiz]] = select(Quiz).where(
            Quiz.id == quiz_id,
            Quiz.space_id == space_id,
            Quiz.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_for_unit(self, *, unit_id: int) -> Quiz | None:
        """一个单元最多一次小测 —— 所以这是 ``scalar_one_or_none``，不是列表。"""
        stmt: Select[tuple[Quiz]] = select(Quiz).where(
            Quiz.unit_id == unit_id, Quiz.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def quiz_ids_by_unit(self, *, unit_ids: list[int]) -> dict[int, int]:
        """「哪些周有小测」—— 一次问清，供单元列表给每一行标出来。"""
        if not unit_ids:
            return {}
        stmt = select(Quiz.unit_id, Quiz.id).where(
            Quiz.unit_id.in_(unit_ids), Quiz.deleted_at.is_(None)
        )
        return {
            unit_id: quiz_id
            for unit_id, quiz_id in (await self._session.execute(stmt)).all()
        }

    async def create(
        self,
        *,
        space_id: int,
        unit_id: int,
        title: str,
        due_at: datetime | None,
        created_by: int,
    ) -> Quiz:
        now = datetime.now(UTC)
        quiz = Quiz(
            space_id=space_id,
            unit_id=unit_id,
            title=title,
            due_at=due_at,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(quiz)
        await self._session.flush()
        return quiz

    async def update(self, *, quiz: Quiz, **fields: object) -> Quiz:
        for name, value in fields.items():
            setattr(quiz, name, value)
        quiz.updated_at = datetime.now(UTC)
        await self._session.flush()
        return quiz

    async def soft_delete(self, *, quiz: Quiz) -> None:
        quiz.deleted_at = datetime.now(UTC)
        await self._session.flush()


class QuizQuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_quiz(self, *, quiz_id: int) -> list[QuizQuestion]:
        stmt: Select[tuple[QuizQuestion]] = (
            select(QuizQuestion)
            .where(
                QuizQuestion.quiz_id == quiz_id,
                QuizQuestion.deleted_at.is_(None),
            )
            .order_by(QuizQuestion.position, QuizQuestion.id)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_for_quizzes(
        self, *, quiz_ids: list[int]
    ) -> dict[int, list[QuizQuestion]]:
        if not quiz_ids:
            return {}
        stmt: Select[tuple[QuizQuestion]] = (
            select(QuizQuestion)
            .where(
                QuizQuestion.quiz_id.in_(quiz_ids),
                QuizQuestion.deleted_at.is_(None),
            )
            .order_by(QuizQuestion.position, QuizQuestion.id)
        )
        out: dict[int, list[QuizQuestion]] = {quiz_id: [] for quiz_id in quiz_ids}
        for question in (await self._session.execute(stmt)).scalars().all():
            out.setdefault(question.quiz_id, []).append(question)
        return out

    async def get_in_quiz(
        self, *, quiz_id: int, question_id: int
    ) -> QuizQuestion | None:
        stmt: Select[tuple[QuizQuestion]] = select(QuizQuestion).where(
            QuizQuestion.id == question_id,
            QuizQuestion.quiz_id == quiz_id,
            QuizQuestion.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def next_position(self, *, quiz_id: int) -> int:
        stmt = select(QuizQuestion.position).where(
            QuizQuestion.quiz_id == quiz_id, QuizQuestion.deleted_at.is_(None)
        )
        positions = [row[0] for row in (await self._session.execute(stmt)).all()]
        return (max(positions) + 1) if positions else 1

    async def create(
        self,
        *,
        quiz_id: int,
        position: int,
        kind: str,
        prompt: str,
        options: list,
        answer: list,
        points: int,
    ) -> QuizQuestion:
        now = datetime.now(UTC)
        question = QuizQuestion(
            quiz_id=quiz_id,
            position=position,
            kind=kind,
            prompt=prompt,
            options=options,
            answer=answer,
            points=points,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(question)
        await self._session.flush()
        return question

    async def update(self, *, question: QuizQuestion, **fields: object) -> QuizQuestion:
        for name, value in fields.items():
            setattr(question, name, value)
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question

    async def soft_delete(self, *, question: QuizQuestion) -> None:
        question.deleted_at = datetime.now(UTC)
        await self._session.flush()


class QuizAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, *, attempt_id: int) -> QuizAttempt | None:
        stmt: Select[tuple[QuizAttempt]] = select(QuizAttempt).where(
            QuizAttempt.id == attempt_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_for_user(self, *, quiz_id: int, user_id: int) -> QuizAttempt | None:
        stmt: Select[tuple[QuizAttempt]] = select(QuizAttempt).where(
            QuizAttempt.quiz_id == quiz_id, QuizAttempt.user_id == user_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_quiz(self, *, quiz_id: int) -> list[QuizAttempt]:
        stmt: Select[tuple[QuizAttempt]] = (
            select(QuizAttempt)
            .where(QuizAttempt.quiz_id == quiz_id)
            .order_by(QuizAttempt.submitted_at, QuizAttempt.id)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(
        self, *, quiz_id: int, space_id: int, user_id: int, graded_at: datetime | None
    ) -> QuizAttempt:
        now = datetime.now(UTC)
        attempt = QuizAttempt(
            quiz_id=quiz_id,
            space_id=space_id,
            user_id=user_id,
            submitted_at=now,
            graded_at=graded_at,
            created_at=now,
            updated_at=now,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def update(self, *, attempt: QuizAttempt, **fields: object) -> QuizAttempt:
        for name, value in fields.items():
            setattr(attempt, name, value)
        attempt.updated_at = datetime.now(UTC)
        await self._session.flush()
        return attempt


class QuizAnswerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_attempt(self, *, attempt_id: int) -> list[QuizAnswer]:
        stmt: Select[tuple[QuizAnswer]] = (
            select(QuizAnswer)
            .where(QuizAnswer.attempt_id == attempt_id)
            .order_by(QuizAnswer.id)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_for_attempts(
        self, *, attempt_ids: list[int]
    ) -> dict[int, list[QuizAnswer]]:
        """整班一份作答的读法：一次取回，别按人循环。"""
        if not attempt_ids:
            return {}
        stmt: Select[tuple[QuizAnswer]] = (
            select(QuizAnswer)
            .where(QuizAnswer.attempt_id.in_(attempt_ids))
            .order_by(QuizAnswer.id)
        )
        out: dict[int, list[QuizAnswer]] = {aid: [] for aid in attempt_ids}
        for answer in (await self._session.execute(stmt)).scalars().all():
            out.setdefault(answer.attempt_id, []).append(answer)
        return out

    async def list_ungraded_for_quiz(
        self, *, quiz_id: int
    ) -> list[tuple[QuizAnswer, QuizQuestion, QuizAttempt]]:
        """复核队列：这次小测里**等着人判**的那些答案，连题与作答一起取回。

        判据是「答案没判过分」而**不是**「题型是简答」—— 题型决定谁会被自动判，
        这里问的是谁还没判，两个问题分开问，将来加了新的主观题型也不用改这里。
        """
        stmt = (
            select(QuizAnswer, QuizQuestion, QuizAttempt)
            .join(QuizQuestion, QuizQuestion.id == QuizAnswer.question_id)
            .join(QuizAttempt, QuizAttempt.id == QuizAnswer.attempt_id)
            .where(
                QuizAttempt.quiz_id == quiz_id,
                QuizAnswer.awarded_points.is_(None),
                QuizQuestion.deleted_at.is_(None),
            )
            .order_by(QuizAttempt.submitted_at, QuizAnswer.id)
        )
        return [
            (answer, question, attempt)
            for answer, question, attempt in (await self._session.execute(stmt)).all()
        ]

    async def get_for_attempt_and_question(
        self, *, attempt_id: int, question_id: int
    ) -> QuizAnswer | None:
        stmt: Select[tuple[QuizAnswer]] = select(QuizAnswer).where(
            QuizAnswer.attempt_id == attempt_id,
            QuizAnswer.question_id == question_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, *, answer_id: int) -> QuizAnswer | None:
        stmt: Select[tuple[QuizAnswer]] = select(QuizAnswer).where(
            QuizAnswer.id == answer_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def clear_for_attempt(self, *, attempt_id: int) -> None:
        """重交 = 换一份答案：先清空这个人这次的答案行，再按新卷子写。

        只清这一次作答的答案（不是删作答本身）—— 一份作答一个人，**不追加版本**，
        见 ``QuizAttempt`` 的说明。
        """
        await self._session.execute(
            delete(QuizAnswer).where(QuizAnswer.attempt_id == attempt_id)
        )
        await self._session.flush()

    async def create(
        self,
        *,
        attempt_id: int,
        question_id: int,
        response: list,
        awarded_points: int | None,
    ) -> QuizAnswer:
        now = datetime.now(UTC)
        answer = QuizAnswer(
            attempt_id=attempt_id,
            question_id=question_id,
            response=response,
            awarded_points=awarded_points,
            comment="",
            graded_by=None,
            graded_at=now if awarded_points is not None else None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(answer)
        await self._session.flush()
        return answer

    async def update(self, *, answer: QuizAnswer, **fields: object) -> QuizAnswer:
        for name, value in fields.items():
            setattr(answer, name, value)
        answer.updated_at = datetime.now(UTC)
        await self._session.flush()
        return answer
