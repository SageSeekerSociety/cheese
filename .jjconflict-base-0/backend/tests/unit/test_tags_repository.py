"""Unit tests for app.domain.tag.repositories.TagRepository."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.tag.repositories import TagRepository, _use_fts

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _topic(**overrides):
    defaults = {
        "id": 1,
        "name": "Python",
        "created_by_id": 10,
        "created_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


def _mock_scalar(val):
    m = MagicMock()
    m.scalar_one_or_none.return_value = val
    return m


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUseFts:
    def test_short_returns_false(self):
        assert _use_fts("ab") is False

    def test_normal_returns_true(self):
        assert _use_fts("python") is True

    def test_emoji_returns_false(self):
        assert _use_fts("🎉🎉🎉") is False


class TestTopicRepository:
    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        t = _topic()
        session.execute.return_value = _mock_scalar(t)
        repo = TagRepository(session)

        result = await repo.get_by_id(1)
        assert result is t

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TagRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_get_by_name_found(self):
        session = _mock_session()
        t = _topic()
        session.execute.return_value = _mock_scalar(t)
        repo = TagRepository(session)

        result = await repo.get_by_name("Python")
        assert result is t

    @pytest.mark.anyio
    async def test_get_by_name_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TagRepository(session)

        assert await repo.get_by_name("Nonexistent") is None

    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = TagRepository(session)

        result = await repo.create(name="Rust", created_by_id=10)
        assert result.name == "Rust"
        assert result.created_by_id == 10
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_list_topics_cursor_no_results(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TagRepository(session)

        rows, prev_id, has_more, next_id = await repo.list_tags_cursor()
        assert rows == []
        assert prev_id is None
        assert has_more is False
        assert next_id is None

    @pytest.mark.anyio
    async def test_list_topics_cursor_with_results(self):
        session = _mock_session()
        t1 = _topic(id=1)
        t2 = _topic(id=2)
        session.execute.return_value = _mock_scalars([t1, t2])
        repo = TagRepository(session)

        rows, prev_id, has_more, next_id = await repo.list_tags_cursor(page_size=10)
        assert rows == [t1, t2]
        assert has_more is False
        assert next_id is None

    @pytest.mark.anyio
    async def test_list_topics_cursor_has_more(self):
        session = _mock_session()
        topics = [_topic(id=i) for i in range(1, 4)]  # 3 results
        session.execute.return_value = _mock_scalars(topics)
        repo = TagRepository(session)

        rows, prev_id, has_more, next_id = await repo.list_tags_cursor(page_size=2)
        assert len(rows) == 2
        assert has_more is True
        assert next_id == 3  # id of last row + 1

    @pytest.mark.anyio
    async def test_list_topics_cursor_with_page_start(self):
        session = _mock_session()
        t = _topic(id=5)
        session.execute.side_effect = [
            _mock_scalars([t]),  # main query
            _mock_scalars([4, 3]),  # prev_ids query
        ]
        repo = TagRepository(session)

        rows, prev_id, has_more, next_id = await repo.list_tags_cursor(
            page_start=5, page_size=10
        )
        assert rows == [t]
        assert prev_id == 3  # last of prev_ids

    @pytest.mark.anyio
    async def test_list_topics_cursor_with_keyword(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TagRepository(session)

        rows, prev_id, has_more, next_id = await repo.list_tags_cursor(
            keyword="python testing", page_size=10
        )
        assert rows == []

    @pytest.mark.anyio
    async def test_list_topics_cursor_with_keyword_and_page_start(self):
        session = _mock_session()
        t = _topic(id=5)
        session.execute.side_effect = [
            _mock_scalars([t]),
            _mock_scalars([]),  # no prev_ids
        ]
        repo = TagRepository(session)

        rows, prev_id, has_more, next_id = await repo.list_tags_cursor(
            keyword="py", page_start=5, page_size=10
        )
        assert rows == [t]
        assert prev_id is None
