"""Unit tests for app.domain.discussion.repositories.

Covers DiscussionRepository, ReactionTypeRepository, DiscussionReactionRepository,
and the _content_str_to_json helper.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.discussion.repositories import (
    DiscussionReactionRepository,
    DiscussionRepository,
    ReactionTypeRepository,
    _content_str_to_json,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _discussion(**overrides):
    defaults = {
        "id": 1,
        "model_type": "QUESTION",
        "model_id": 10,
        "sender_id": 50,
        "content": {"type": "doc", "content": []},
        "parent_id": None,
        "mentioned_user_ids": [],
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _reaction(**overrides):
    defaults = {
        "id": 1,
        "discussion_id": 1,
        "user_id": 50,
        "reaction_type_id": 1,
        "created_at": NOW,
        "updated_at": NOW,
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


def _mock_scalar_one(val):
    m = MagicMock()
    m.scalar_one.return_value = val
    return m


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


def _mock_rows(lst):
    m = MagicMock()
    m.all.return_value = lst
    return m


# ---------------------------------------------------------------------------
# _content_str_to_json
# ---------------------------------------------------------------------------


class TestContentStrToJson:
    def test_valid_json_dict(self):
        result = _content_str_to_json('{"type": "doc", "content": []}')
        assert result == {"type": "doc", "content": []}

    def test_invalid_json(self):
        result = _content_str_to_json("not json")
        assert result["type"] == "doc"
        assert result["content"][0]["content"][0]["text"] == "not json"

    def test_json_array_fallback(self):
        result = _content_str_to_json("[1, 2, 3]")
        assert result["type"] == "doc"

    def test_none_input(self):
        result = _content_str_to_json(None)
        assert result["type"] == "doc"

    def test_plain_text(self):
        result = _content_str_to_json("Hello world")
        assert result["type"] == "doc"
        assert result["content"][0]["content"][0]["text"] == "Hello world"


# ---------------------------------------------------------------------------
# DiscussionRepository
# ---------------------------------------------------------------------------


class TestDiscussionRepository:
    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = DiscussionRepository(session)

        result = await repo.create(
            model_type="QUESTION",
            model_id=10,
            sender_id=50,
            content='{"type":"doc","content":[]}',
            parent_id=None,
            mentioned_user_ids=[],
        )
        assert result.model_type == "QUESTION"
        assert result.sender_id == 50
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_create_with_mentions(self):
        session = _mock_session()
        repo = DiscussionRepository(session)

        result = await repo.create(
            model_type="QUESTION",
            model_id=10,
            sender_id=50,
            content="hello",
            parent_id=None,
            mentioned_user_ids=[60, 70],
        )
        # 1 discussion + 2 mentions = 3 add calls
        assert session.add.call_count == 3
        assert result.mentioned_user_ids == [60, 70]

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        d = _discussion()
        session.execute.side_effect = [
            _mock_scalar(d),  # get entity
            _mock_rows([]),  # load mentions
        ]
        repo = DiscussionRepository(session)

        result = await repo.get_by_id(1)
        assert result is d

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = DiscussionRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_count_children(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = DiscussionRepository(session)

        assert await repo.count_children(1) == 3

    @pytest.mark.anyio
    async def test_soft_delete_found(self):
        session = _mock_session()
        d = _discussion()
        session.execute.side_effect = [
            _mock_scalar(d),  # get_by_id
            _mock_rows([]),  # load mentions
        ]
        repo = DiscussionRepository(session)

        result = await repo.soft_delete(1)
        assert result is True
        assert d.deleted_at is not None

    @pytest.mark.anyio
    async def test_soft_delete_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = DiscussionRepository(session)

        result = await repo.soft_delete(999)
        assert result is False

    @pytest.mark.anyio
    async def test_update_content_found(self):
        session = _mock_session()
        d = _discussion()
        session.execute.side_effect = [
            _mock_scalar(d),
            _mock_rows([]),
        ]
        repo = DiscussionRepository(session)

        new_content = {"type": "doc", "content": [{"type": "paragraph"}]}
        result = await repo.update_content(1, new_content)
        assert result is d
        assert result.content == new_content

    @pytest.mark.anyio
    async def test_update_content_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = DiscussionRepository(session)

        result = await repo.update_content(999, {})
        assert result is None


# ---------------------------------------------------------------------------
# ReactionTypeRepository
# ---------------------------------------------------------------------------


class TestReactionTypeRepository:
    @pytest.mark.anyio
    async def test_list_active(self):
        session = _mock_session()
        rt = SimpleNamespace(id=1, code="LIKE", name="Like")
        session.execute.return_value = _mock_scalars([rt])
        repo = ReactionTypeRepository(session)

        result = await repo.list_active()
        assert result == [rt]

    @pytest.mark.anyio
    async def test_get_by_id(self):
        session = _mock_session()
        rt = SimpleNamespace(id=1, code="LIKE")
        session.execute.return_value = _mock_scalar(rt)
        repo = ReactionTypeRepository(session)

        result = await repo.get_by_id(1)
        assert result is rt

    @pytest.mark.anyio
    async def test_ensure_defaults_already_exists(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(2)
        repo = ReactionTypeRepository(session)

        await repo.ensure_defaults()
        # Only the count query should run, no INSERTs
        assert session.execute.call_count == 1

    @pytest.mark.anyio
    async def test_ensure_defaults_creates(self):
        session = _mock_session()
        # First call returns count=0, subsequent calls are the INSERT statements
        session.execute.side_effect = [_mock_scalar_one(0), None, None]
        repo = ReactionTypeRepository(session)

        await repo.ensure_defaults()
        # count query + 2 INSERT ON CONFLICT statements
        assert session.execute.call_count == 3


# ---------------------------------------------------------------------------
# DiscussionReactionRepository
# ---------------------------------------------------------------------------


class TestDiscussionReactionRepository:
    @pytest.mark.anyio
    async def test_get_reaction_found(self):
        session = _mock_session()
        r = _reaction()
        session.execute.return_value = _mock_scalar(r)
        repo = DiscussionReactionRepository(session)

        result = await repo.get_reaction(discussion_id=1, user_id=50, reaction_type_id=1)
        assert result is r

    @pytest.mark.anyio
    async def test_toggle_new_reaction(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = DiscussionReactionRepository(session)

        result = await repo.toggle(discussion_id=1, user_id=50, reaction_type_id=1)
        assert result is not None
        assert result.discussion_id == 1
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_toggle_existing_reaction_removes(self):
        session = _mock_session()
        existing = _reaction()
        session.execute.return_value = _mock_scalar(existing)
        repo = DiscussionReactionRepository(session)

        result = await repo.toggle(discussion_id=1, user_id=50, reaction_type_id=1)
        assert result is None
        assert existing.deleted_at is not None

    @pytest.mark.anyio
    async def test_remove_existing(self):
        session = _mock_session()
        existing = _reaction()
        session.execute.return_value = _mock_scalar(existing)
        repo = DiscussionReactionRepository(session)

        result = await repo.remove(discussion_id=1, user_id=50, reaction_type_id=1)
        assert result is True
        assert existing.deleted_at is not None

    @pytest.mark.anyio
    async def test_remove_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = DiscussionReactionRepository(session)

        result = await repo.remove(discussion_id=1, user_id=50, reaction_type_id=1)
        assert result is False

    @pytest.mark.anyio
    async def test_count_by_discussion(self):
        session = _mock_session()
        session.execute.return_value = _mock_rows([(1, 5), (2, 3)])
        repo = DiscussionReactionRepository(session)

        result = await repo.count_by_discussion(1)
        assert result == {1: 5, 2: 3}

    @pytest.mark.anyio
    async def test_has_user_reacted_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = DiscussionReactionRepository(session)

        assert await repo.has_user_reacted(discussion_id=1, user_id=50, reaction_type_id=1) is True

    @pytest.mark.anyio
    async def test_has_user_reacted_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = DiscussionReactionRepository(session)

        assert await repo.has_user_reacted(discussion_id=1, user_id=50, reaction_type_id=1) is False
