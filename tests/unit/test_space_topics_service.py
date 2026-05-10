"""Unit tests for app.domain.space.topics_service.SpaceTopicsService."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.space.topics_service import SpaceTopicsService, _topic_to_dto

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _topic(**overrides):
    defaults = {"id": 1, "name": "Python"}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_session():
    session = AsyncMock()
    return session


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTopicToDto:
    def test_converts_topic_to_dict(self):
        topic = _topic(id=5, name="Django")
        result = _topic_to_dto(topic)
        assert result == {"id": 5, "name": "Django"}


class TestSpaceTopicsService:
    @pytest.mark.anyio
    async def test_get_hot_topics(self):
        session = _mock_session()
        t1 = _topic(id=1, name="Python")
        t2 = _topic(id=2, name="Java")
        session.execute.return_value = _mock_scalars([t1, t2])
        svc = SpaceTopicsService(session)

        result = await svc.get_hot_topics(space_id=1, limit=10)
        assert result == [
            {"id": 1, "name": "Python"},
            {"id": 2, "name": "Java"},
        ]

    @pytest.mark.anyio
    async def test_get_hot_topics_empty(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        svc = SpaceTopicsService(session)

        result = await svc.get_hot_topics(space_id=1, limit=10)
        assert result == []

    @pytest.mark.anyio
    async def test_search_topics_empty_keyword(self):
        session = _mock_session()
        svc = SpaceTopicsService(session)

        result = await svc.search_topics(space_id=1, keyword="", limit=10)
        assert result == []

    @pytest.mark.anyio
    async def test_search_topics_whitespace_keyword(self):
        session = _mock_session()
        svc = SpaceTopicsService(session)

        result = await svc.search_topics(space_id=1, keyword="   ", limit=10)
        assert result == []

    @pytest.mark.anyio
    async def test_search_topics_with_results(self):
        session = _mock_session()
        t = _topic(id=1, name="Python")
        session.execute.return_value = _mock_scalars([t])
        svc = SpaceTopicsService(session)

        result = await svc.search_topics(space_id=1, keyword="Pyth", limit=10)
        assert result == [{"id": 1, "name": "Python"}]

    @pytest.mark.anyio
    async def test_search_topics_no_results(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        svc = SpaceTopicsService(session)

        result = await svc.search_topics(space_id=1, keyword="Nonexistent", limit=10)
        assert result == []
