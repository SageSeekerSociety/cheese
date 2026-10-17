from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.domain.tag.services import TagService, _tag_to_dto

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _topic(**overrides):
    defaults = {
        "id": 1,
        "name": "Python",
        "created_by_id": 42,
        "created_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(repo=None) -> tuple[TagService, AsyncMock]:
    repo = repo or AsyncMock()
    svc = TagService(repo=repo)
    return svc, repo


# ---------------------------------------------------------------------------
# _tag_to_dto (pure helper)
# ---------------------------------------------------------------------------


class TestTopicToDto:
    def test_basic_conversion(self):
        topic = _topic(id=5, name="Rust", created_by_id=10)
        dto = _tag_to_dto(topic)
        assert dto["id"] == 5
        assert dto["name"] == "Rust"
        assert dto["createdById"] == 10
        assert dto["createdAt"] == NOW_MS

    def test_none_created_at(self):
        topic = _topic(created_at=None)
        dto = _tag_to_dto(topic)
        assert dto["createdAt"] == 0


# ---------------------------------------------------------------------------
# list_tags
# ---------------------------------------------------------------------------


class TestListTopics:
    @pytest.mark.anyio
    async def test_empty_keyword_returns_empty(self):
        svc, repo = _make_service()

        items, page = await svc.list_tags(keyword=None, page_start=None, page_size=10)

        assert items == []
        assert page["pageSize"] == 0
        assert page["pageStart"] == 0
        assert page["hasPrev"] is False
        assert page["hasMore"] is False

    @pytest.mark.anyio
    async def test_whitespace_keyword_returns_empty(self):
        svc, repo = _make_service()

        items, page = await svc.list_tags(keyword="   ", page_start=None, page_size=10)

        assert items == []
        assert page["pageSize"] == 0

    @pytest.mark.anyio
    async def test_empty_string_keyword_returns_empty(self):
        svc, repo = _make_service()

        items, page = await svc.list_tags(keyword="", page_start=None, page_size=10)

        assert items == []
        assert page["pageSize"] == 0

    @pytest.mark.anyio
    async def test_with_results_no_prev_no_more(self):
        svc, repo = _make_service()
        topics = [_topic(id=1, name="Go"), _topic(id=2, name="Golang")]
        repo.list_tags_cursor.return_value = (topics, None, False, None)

        items, page = await svc.list_tags(keyword="Go", page_start=None, page_size=10)

        repo.list_tags_cursor.assert_awaited_once_with(
            keyword="Go", page_start=None, page_size=10
        )
        assert len(items) == 2
        assert items[0]["name"] == "Go"
        assert items[1]["name"] == "Golang"
        assert page["pageSize"] == 2
        assert page["pageStart"] == 1
        assert page["hasPrev"] is False
        assert page["prevStart"] == 0
        assert page["hasMore"] is False
        assert page["nextStart"] == 0

    @pytest.mark.anyio
    async def test_with_results_has_prev_and_more(self):
        svc, repo = _make_service()
        topics = [_topic(id=5, name="Mid Topic")]
        repo.list_tags_cursor.return_value = (topics, 3, True, 7)

        items, page = await svc.list_tags(keyword="Mid", page_start=5, page_size=1)

        assert len(items) == 1
        assert page["pageSize"] == 1
        assert page["pageStart"] == 5
        assert page["hasPrev"] is True
        assert page["prevStart"] == 3
        assert page["hasMore"] is True
        assert page["nextStart"] == 7

    @pytest.mark.anyio
    async def test_empty_results_with_keyword(self):
        svc, repo = _make_service()
        repo.list_tags_cursor.return_value = ([], None, False, None)

        items, page = await svc.list_tags(keyword="xyz", page_start=None, page_size=10)

        assert items == []
        assert page["pageSize"] == 0
        assert page["pageStart"] == 0
        assert page["hasPrev"] is False
        assert page["hasMore"] is False


# ---------------------------------------------------------------------------
# get_tag
# ---------------------------------------------------------------------------


class TestGetTopic:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _topic(id=10, name="FastAPI")

        result = await svc.get_tag(10)

        repo.get_by_id.assert_awaited_once_with(10)
        assert result["id"] == 10
        assert result["name"] == "FastAPI"

    @pytest.mark.anyio
    async def test_not_found(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Topic not found"):
            await svc.get_tag(999)


# ---------------------------------------------------------------------------
# create_tag
# ---------------------------------------------------------------------------


class TestCreateTopic:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo = _make_service()
        repo.get_by_name.return_value = None
        repo.create.return_value = _topic(id=55)

        result = await svc.create_tag(name="NewTopic", created_by_id=7)

        repo.get_by_name.assert_awaited_once_with("NewTopic")
        repo.create.assert_awaited_once_with(name="NewTopic", created_by_id=7)
        assert result == {"id": 55}

    @pytest.mark.anyio
    async def test_conflict_already_exists(self):
        svc, repo = _make_service()
        repo.get_by_name.return_value = _topic(id=1, name="Existing")

        with pytest.raises(ConflictError, match="Topic already exists"):
            await svc.create_tag(name="Existing", created_by_id=7)

        repo.create.assert_not_awaited()
