"""Unit tests for app.domain.dashboard.user_statistics.

``UserStatisticsRepository`` moved here from ``app.domain.user.repositories``
when the counts stopped being read from the layer that assembles the dto. The
queries and their assertions are unchanged; only the import path is.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.dashboard.user_statistics import UserStatisticsRepository


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    # An async context manager, as a real session's savepoint is.
    session.begin_nested = MagicMock(return_value=AsyncMock())
    return session


def _mock_scalar_one(val):
    m = MagicMock()
    m.scalar_one.return_value = val
    return m


class TestUserStatisticsRepository:
    @pytest.mark.anyio
    async def test_count_teams(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = UserStatisticsRepository(session)

        assert await repo.count_teams(1) == 3

    @pytest.mark.anyio
    async def test_count_task_memberships(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(5)
        repo = UserStatisticsRepository(session)

        assert await repo.count_task_memberships(1) == 5

    @pytest.mark.anyio
    async def test_count_knowledge_entries(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(10)
        repo = UserStatisticsRepository(session)

        assert await repo.count_knowledge_entries(1) == 10

    @pytest.mark.anyio
    async def test_count_submissions(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(7)
        repo = UserStatisticsRepository(session)

        assert await repo.count_submissions(1) == 7

    @pytest.mark.anyio
    async def test_count_questions(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(4)
        repo = UserStatisticsRepository(session)

        assert await repo.count_questions(1) == 4

    @pytest.mark.anyio
    async def test_count_answers(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(6)
        repo = UserStatisticsRepository(session)

        assert await repo.count_answers(1) == 6

    @pytest.mark.anyio
    async def test_aggregate(self):
        session = _mock_session()
        session.execute.side_effect = [
            _mock_scalar_one(3),  # teams
            _mock_scalar_one(5),  # tasks
            _mock_scalar_one(10),  # knowledge
            _mock_scalar_one(7),  # submissions
            _mock_scalar_one(4),  # questions
            _mock_scalar_one(6),  # answers
        ]
        repo = UserStatisticsRepository(session)

        result = await repo.aggregate(1)
        assert result == {
            "teamCount": 3,
            "taskParticipationCount": 5,
            "knowledgeCount": 10,
            "submissionCount": 7,
            "questionCount": 4,
            "answerCount": 6,
        }
