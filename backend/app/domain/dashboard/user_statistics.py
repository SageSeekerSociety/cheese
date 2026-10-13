"""One person's numbers, counted for the account dto.

Six counts, one table each in another domain: the teams they joined
(``TeamUserRelation``), the task memberships and submissions (``task``), the
knowledge entries, questions and answers they wrote. All four packages sit
above ``app.domain.user`` in the layer table, which is why this lives here and
not next to the dto it fills — ``UserAuthService.build_user_dto`` takes these
through the port ``app.domain.user.ports.UserStatistics`` names.

The architecture doc's rule for a query spanning packages is 「跨包的联表查询
放进拥有主表的那个包的 `queries.py`，或者第 7 层」 (``docs/manual/dev/
architecture.md``); these are single-table counts whose reading order the
lowest of them forbids, so they take the second half of the rule, beside the
other per-person aggregations (``DashboardService.member_summary``,
``user_profile``). Read-only; the tables stay the source of truth.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.answers.models import Answer
from app.domain.knowledge.models import Knowledge
from app.domain.questions.models import Question
from app.domain.task.models import TaskMembership, TaskSubmission
from app.domain.team.models import TeamUserRelation


class UserStatisticsRepository:
    """The six counts the account dto carries, for one person at a time."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_teams(self, user_id: int) -> int:
        stmt = select(func.count(TeamUserRelation.id)).where(
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_task_memberships(self, user_id: int) -> int:
        stmt = select(func.count(TaskMembership.id)).where(
            TaskMembership.member_id == user_id,
            TaskMembership.is_team.is_(False),
            TaskMembership.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_knowledge_entries(self, user_id: int) -> int:
        stmt = select(func.count(Knowledge.id)).where(
            Knowledge.created_by == user_id,
            Knowledge.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_submissions(self, user_id: int) -> int:
        stmt = select(func.count(TaskSubmission.id)).where(
            TaskSubmission.submitter_id == user_id,
            TaskSubmission.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_questions(self, user_id: int) -> int:
        stmt = select(func.count(Question.id)).where(
            Question.created_by_id == user_id,
            Question.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_answers(self, user_id: int) -> int:
        stmt = select(func.count(Answer.id)).where(
            Answer.created_by_id == user_id,
            Answer.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def aggregate(self, user_id: int) -> dict[str, int]:
        teams = await self.count_teams(user_id)
        tasks = await self.count_task_memberships(user_id)
        knowledge = await self.count_knowledge_entries(user_id)
        submissions = await self.count_submissions(user_id)
        questions = await self.count_questions(user_id)
        answers = await self.count_answers(user_id)
        return {
            "teamCount": teams,
            "taskParticipationCount": tasks,
            "knowledgeCount": knowledge,
            "submissionCount": submissions,
            "questionCount": questions,
            "answerCount": answers,
        }
