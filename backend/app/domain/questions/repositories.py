"""questions 领域的数据访问。三个仓储都在这里，读的那一半在 `queries.py`。

`QuestionRepository` 是唯一跨两个文件的类：只 SELECT 的方法住在 `queries.py` 的
`QuestionQueries` 基类里（拆的理由和那条界限写在那份文件的 docstring），这里留下的是
会改数据的那一半 —— add / flush / delete / 改字段。继承让调用点一行都不用改：
services、路由和单测一直是拿一个 `QuestionRepository(session)` 调上面所有方法，分不清
也不用分清某个方法住在哪个文件。

`QuestionTopicRepository`（提问与标签的关系）和 `QuestionInvitationRepository`
（邀请）各自只碰自己的两张表，读和写都在一起，所以整个类留在这里。

两处和拆分前**不一样**，都不是 bug，但说出来比让人自己撞上强：

- 顺带 import 进来、只被搬走的那一半用到的名字（`re`、`or_`、`text`、`timedelta`、
  `VoteType`、`Discussion`、`DiscussableModelType`）跟着它们搬去了 `queries.py`；
  这份模块不再顺带回答它们。仓库里没有调用点从这条路径取这些名字。
  （`app/domain/feedback/repositories.py` 的 docstring 记着同一条取舍的另一面：
  那边 `_IN_BATCH` 有测试打补丁，所以连读它的 `batched` 一起留在了原地。）
- `_use_fts` 在这里只是再导出：补丁要打在读它的那份模块上。见下面的注释。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.questions.models import (
    Attitude,
    Question,
    QuestionFollowerRelation,
    QuestionInvitation,
    QuestionQueryLog,
    QuestionSearchLog,
    QuestionTagRelation,
)

# 这两个名字原来**定义在本模块**，拆分后定义在 `queries.py`（跟它们唯一的调用方
# `_keyword_filter` 住在一起）。这条 import 不是「顺手拿来用」，是把公开路径留在
# 原地：`tests/unit/test_questions_repository.py` 一直从
# `app.domain.questions.repositories` 取 `_use_fts`。所以 F401 在这里是故意的。
from app.domain.questions.queries import (  # noqa: F401
    _HAS_WORD_CHAR_RE,
    QuestionQueries,
    _use_fts,
)
from app.domain.tag.models import Tag


class QuestionRepository(QuestionQueries):
    """会改数据的那一半；只读的方法继承自 `QuestionQueries`。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_question(
        self,
        *,
        created_by_id: int,
        title: str,
        content: str,
        type_: int,
        group_id: int | None,
        bounty: int,
    ) -> Question:
        now = datetime.now(UTC)
        question = Question(
            created_by_id=created_by_id,
            title=title,
            content=content,
            type=type_,
            group_id=group_id,
            bounty=bounty,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(question)
        await self._session.flush()
        return question

    async def follow_question(self, *, question_id: int, user_id: int) -> bool:
        existing = await self._get_follow_relation(question_id, user_id)
        if existing is not None and existing.deleted_at is None:
            return False
        now = datetime.now(UTC)
        if existing is None:
            relation = QuestionFollowerRelation(
                question_id=question_id,
                follower_id=user_id,
                created_at=now,
                deleted_at=None,
            )
            self._session.add(relation)
        else:
            existing.deleted_at = None
            existing.created_at = now
        await self._session.flush()
        return True

    async def unfollow_question(self, *, question_id: int, user_id: int) -> bool:
        relation = await self._get_follow_relation(question_id, user_id)
        if relation is None or relation.deleted_at is not None:
            return False
        relation.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def accept_answer(
        self, *, question_id: int, answer_id: int
    ) -> Question | None:
        """Set accepted_answer_id for a question."""
        question = await self.get_by_id(question_id)
        if question is None:
            return None
        question.accepted_answer_id = answer_id
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question

    async def unaccept_answer(self, *, question_id: int) -> Question | None:
        """Clear accepted_answer_id for a question."""
        question = await self.get_by_id(question_id)
        if question is None:
            return None
        question.accepted_answer_id = None
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question

    async def log_query(
        self,
        *,
        question_id: int,
        viewer_id: int | None,
        ip: str,
        user_agent: str | None,
    ) -> None:
        log = QuestionQueryLog(
            question_id=question_id,
            viewer_id=viewer_id,
            ip=ip,
            user_agent=user_agent,
            created_at=datetime.now(UTC),
        )
        self._session.add(log)
        await self._session.flush()

    async def vote(self, *, question_id: int, user_id: int, vote_type: str) -> Attitude:
        existing = await self._get_vote(question_id, user_id)
        now = datetime.now(UTC)
        if existing is not None:
            existing.attitude = vote_type
            existing.updated_at = now
            await self._session.flush()
            return existing
        attitude = Attitude(
            attitudable_id=question_id,
            attitudable_type="QUESTION",
            user_id=user_id,
            attitude=vote_type,
            created_at=now,
            updated_at=now,
        )
        self._session.add(attitude)
        await self._session.flush()
        return attitude

    async def remove_vote(self, *, question_id: int, user_id: int) -> bool:
        existing = await self._get_vote(question_id, user_id)
        if existing is None:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

    async def log_search(
        self,
        *,
        keywords: str,
        first_question_id: int | None,
        page_size: int,
        result_count: int,
        duration_ms: float,
        searcher_id: int | None,
        ip: str,
        user_agent: str | None,
    ) -> None:
        log = QuestionSearchLog(
            keywords=keywords,
            first_question_id=first_question_id,
            page_size=page_size,
            result=str(result_count),
            duration=duration_ms,
            searcher_id=searcher_id,
            ip=ip,
            user_agent=user_agent,
            created_at=datetime.now(UTC),
        )
        self._session.add(log)
        await self._session.flush()

    async def update_question(
        self,
        question: Question,
        *,
        title: str | None = None,
        content: str | None = None,
        type_: int | None = None,
    ) -> Question:
        if title is not None:
            question.title = title
        if content is not None:
            question.content = content
        if type_ is not None:
            question.type = type_
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question

    async def soft_delete(self, question: Question) -> None:
        question.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def set_bounty(self, question: Question, bounty: int) -> Question:
        question.bounty = bounty
        question.updated_at = datetime.now(UTC)
        await self._session.flush()
        return question


class QuestionTopicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_topics(
        self, *, question_id: int, topic_ids: Sequence[int], user_id: int
    ) -> None:
        stmt = select(QuestionTagRelation).where(
            QuestionTagRelation.question_id == question_id,
            QuestionTagRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        existing = list(result.scalars().all())
        now = datetime.now(UTC)
        for row in existing:
            row.deleted_at = now
        for topic_id in topic_ids:
            self._session.add(
                QuestionTagRelation(
                    question_id=question_id,
                    tag_id=topic_id,
                    created_by_id=user_id,
                    created_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()

    async def list_topic_ids(self, question_ids: Sequence[int]) -> dict[int, list[int]]:
        if not question_ids:
            return {}
        stmt: Select[tuple[QuestionTagRelation]] = (
            select(QuestionTagRelation)
            .where(
                QuestionTagRelation.question_id.in_(list(question_ids)),
                QuestionTagRelation.deleted_at.is_(None),
            )
            .order_by(
                QuestionTagRelation.question_id.asc(), QuestionTagRelation.id.asc()
            )
        )
        result = await self._session.execute(stmt)
        mapping: dict[int, list[int]] = {}
        for row in result.scalars().all():
            mapping.setdefault(row.question_id, []).append(row.tag_id)
        return mapping

    async def validate_topic_ids(self, topic_ids: list[int]) -> set[int]:

        if not topic_ids:
            return set()
        stmt = select(Tag.id).where(
            Tag.id.in_(topic_ids),
            Tag.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def get_topics_for_question(self, question_id: int) -> list[dict]:

        stmt = (
            select(Tag)
            .join(QuestionTagRelation, QuestionTagRelation.tag_id == Tag.id)
            .where(
                QuestionTagRelation.question_id == question_id,
                QuestionTagRelation.deleted_at.is_(None),
                Tag.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        topics = result.scalars().all()
        return [{"id": t.id, "name": t.name} for t in topics]

    async def get_topics_for_questions(
        self, question_ids: Sequence[int]
    ) -> dict[int, list[dict]]:
        """Bulk variant of get_topics_for_question. Returns {question_id: [{id, name}]}."""

        if not question_ids:
            return {}
        stmt = (
            select(QuestionTagRelation.question_id, Tag.id, Tag.name)
            .join(Tag, Tag.id == QuestionTagRelation.tag_id)
            .where(
                QuestionTagRelation.question_id.in_(list(question_ids)),
                QuestionTagRelation.deleted_at.is_(None),
                Tag.deleted_at.is_(None),
            )
            .order_by(
                QuestionTagRelation.question_id.asc(),
                QuestionTagRelation.id.asc(),
            )
        )
        result = await self._session.execute(stmt)
        mapping: dict[int, list[dict]] = {}
        for question_id, topic_id, topic_name in result.all():
            mapping.setdefault(question_id, []).append(
                {"id": topic_id, "name": topic_name}
            )
        return mapping


class QuestionInvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_invitation(
        self, *, question_id: int, user_id: int
    ) -> QuestionInvitation:
        existing = await self._get_invitation(question_id, user_id)
        now = datetime.now(UTC)
        if existing is not None:
            existing.updated_at = now
            await self._session.flush()
            return existing
        invitation = QuestionInvitation(
            question_id=question_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(invitation)
        await self._session.flush()
        return invitation

    async def get_by_id(self, invitation_id: int) -> QuestionInvitation | None:
        stmt: Select[tuple[QuestionInvitation]] = select(QuestionInvitation).where(
            QuestionInvitation.id == invitation_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_invitations(
        self, *, question_id: int, limit: int, offset: int
    ) -> tuple[list[QuestionInvitation], int]:
        stmt = (
            select(QuestionInvitation)
            .where(
                QuestionInvitation.question_id == question_id,
            )
            .order_by(QuestionInvitation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(QuestionInvitation.id)).where(
            QuestionInvitation.question_id == question_id,
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def hard_delete(self, invitation: QuestionInvitation) -> None:
        await self._session.delete(invitation)
        await self._session.flush()

    async def _get_invitation(
        self, question_id: int, user_id: int
    ) -> QuestionInvitation | None:
        stmt: Select[tuple[QuestionInvitation]] = select(QuestionInvitation).where(
            QuestionInvitation.question_id == question_id,
            QuestionInvitation.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
