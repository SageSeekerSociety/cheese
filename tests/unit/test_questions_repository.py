"""Unit tests for app.domain.questions.repositories.

Covers QuestionRepository, QuestionTopicRepository, QuestionInvitationRepository
with mocked AsyncSession to exercise all repository methods.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.questions.repositories import (
    QuestionInvitationRepository,
    QuestionRepository,
    QuestionTopicRepository,
    _use_fts,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _question(**overrides):
    defaults = {
        "id": 10,
        "created_by_id": 50,
        "title": "How to test?",
        "content": "Please help me write tests.",
        "type": 1,
        "group_id": None,
        "bounty": 0,
        "accepted_answer_id": None,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _follow_relation(**overrides):
    defaults = {
        "id": 1,
        "question_id": 10,
        "follower_id": 50,
        "created_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _attitude(**overrides):
    defaults = {
        "id": 1,
        "attitudable_id": 10,
        "attitudable_type": "QUESTION",
        "user_id": 50,
        "attitude": "POSITIVE",
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _invitation(**overrides):
    defaults = {
        "id": 1,
        "question_id": 10,
        "user_id": 99,
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


def _mock_execute_scalar(return_val):
    """Create a mock result whose scalar_one_or_none returns the given value."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = return_val
    return mock_result


def _mock_execute_scalar_one(return_val):
    """Create a mock result whose scalar_one returns the given value."""
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = return_val
    return mock_result


def _mock_execute_scalars(return_list):
    """Create a mock result whose scalars().all() returns the given list."""
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = return_list
    mock_result.scalars.return_value = mock_scalars
    return mock_result


def _mock_execute_rows(return_list):
    """Create a mock result whose all() returns tuples/rows."""
    mock_result = MagicMock()
    mock_result.all.return_value = return_list
    return mock_result


# ---------------------------------------------------------------------------
# _use_fts helper
# ---------------------------------------------------------------------------


class TestUseFts:
    def test_short_token_returns_false(self):
        assert _use_fts("ab") is False

    def test_emoji_only_returns_false(self):
        assert _use_fts("🎉🎉🎉") is False

    def test_normal_word_returns_true(self):
        assert _use_fts("hello") is True

    def test_three_char_word_returns_true(self):
        assert _use_fts("abc") is True

    def test_empty_string_returns_false(self):
        assert _use_fts("") is False

    def test_two_char_word_returns_false(self):
        assert _use_fts("hi") is False


# ---------------------------------------------------------------------------
# QuestionRepository
# ---------------------------------------------------------------------------


class TestQuestionRepository:
    @pytest.mark.anyio
    async def test_create_question(self):
        session = _mock_session()
        repo = QuestionRepository(session)

        result = await repo.create_question(
            created_by_id=50,
            title="Test Question",
            content="Content",
            type_=1,
            group_id=None,
            bounty=10,
        )
        assert result.title == "Test Question"
        assert result.content == "Content"
        assert result.bounty == 10
        assert result.created_by_id == 50
        assert result.type == 1
        assert result.deleted_at is None
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        q = _question()
        session.execute.return_value = _mock_execute_scalar(q)
        repo = QuestionRepository(session)

        result = await repo.get_by_id(10)
        assert result is q

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.get_by_id(999)
        assert result is None

    @pytest.mark.anyio
    async def test_search_no_keyword(self):
        session = _mock_session()
        q1 = _question(id=1)
        q2 = _question(id=2)
        session.execute.side_effect = [
            _mock_execute_scalars([q1, q2]),
            _mock_execute_scalar_one(2),
        ]
        repo = QuestionRepository(session)

        rows, total = await repo.search(
            keyword=None, limit=10, offset=0, sort_by="createdAt", sort_order="desc"
        )
        assert rows == [q1, q2]
        assert total == 2

    @pytest.mark.anyio
    async def test_search_with_short_keyword(self):
        session = _mock_session()
        q1 = _question(id=1)
        session.execute.side_effect = [
            _mock_execute_scalars([q1]),
            _mock_execute_scalar_one(1),
        ]
        repo = QuestionRepository(session)

        rows, total = await repo.search(
            keyword="hi", limit=10, offset=0, sort_by="updatedAt", sort_order="asc"
        )
        assert rows == [q1]
        assert total == 1

    @pytest.mark.anyio
    async def test_search_with_long_keyword(self):
        session = _mock_session()
        session.execute.side_effect = [
            _mock_execute_scalars([]),
            _mock_execute_scalar_one(0),
        ]
        repo = QuestionRepository(session)

        rows, total = await repo.search(
            keyword="testing", limit=10, offset=0, sort_by="createdAt", sort_order="desc"
        )
        assert rows == []
        assert total == 0

    @pytest.mark.anyio
    async def test_follow_question_new_follow(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.follow_question(question_id=10, user_id=50)
        assert result is True
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_follow_question_already_following(self):
        session = _mock_session()
        rel = _follow_relation(deleted_at=None)
        session.execute.return_value = _mock_execute_scalar(rel)
        repo = QuestionRepository(session)

        result = await repo.follow_question(question_id=10, user_id=50)
        assert result is False

    @pytest.mark.anyio
    async def test_follow_question_re_follow_deleted(self):
        session = _mock_session()
        rel = _follow_relation(deleted_at=NOW)
        session.execute.return_value = _mock_execute_scalar(rel)
        repo = QuestionRepository(session)

        result = await repo.follow_question(question_id=10, user_id=50)
        assert result is True
        assert rel.deleted_at is None

    @pytest.mark.anyio
    async def test_unfollow_question_success(self):
        session = _mock_session()
        rel = _follow_relation(deleted_at=None)
        session.execute.return_value = _mock_execute_scalar(rel)
        repo = QuestionRepository(session)

        result = await repo.unfollow_question(question_id=10, user_id=50)
        assert result is True
        assert rel.deleted_at is not None

    @pytest.mark.anyio
    async def test_unfollow_question_not_following(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.unfollow_question(question_id=10, user_id=50)
        assert result is False

    @pytest.mark.anyio
    async def test_unfollow_question_already_deleted(self):
        session = _mock_session()
        rel = _follow_relation(deleted_at=NOW)
        session.execute.return_value = _mock_execute_scalar(rel)
        repo = QuestionRepository(session)

        result = await repo.unfollow_question(question_id=10, user_id=50)
        assert result is False

    @pytest.mark.anyio
    async def test_list_followed(self):
        session = _mock_session()
        q1 = _question(id=1)
        session.execute.side_effect = [
            _mock_execute_scalars([q1]),
            _mock_execute_scalar_one(1),
        ]
        repo = QuestionRepository(session)

        rows, total = await repo.list_followed(user_id=50, limit=10, offset=0)
        assert rows == [q1]
        assert total == 1

    @pytest.mark.anyio
    async def test_count_followers(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar_one(5)
        repo = QuestionRepository(session)

        count = await repo.count_followers(10)
        assert count == 5

    @pytest.mark.anyio
    async def test_is_following_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(1)
        repo = QuestionRepository(session)

        result = await repo.is_following(10, 50)
        assert result is True

    @pytest.mark.anyio
    async def test_is_following_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.is_following(10, 50)
        assert result is False

    @pytest.mark.anyio
    async def test_count_comments(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar_one(3)
        repo = QuestionRepository(session)

        count = await repo.count_comments(10)
        assert count == 3

    @pytest.mark.anyio
    async def test_accept_answer_success(self):
        session = _mock_session()
        q = _question()
        session.execute.return_value = _mock_execute_scalar(q)
        repo = QuestionRepository(session)

        result = await repo.accept_answer(question_id=10, answer_id=5)
        assert result is q
        assert q.accepted_answer_id == 5

    @pytest.mark.anyio
    async def test_accept_answer_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.accept_answer(question_id=999, answer_id=5)
        assert result is None

    @pytest.mark.anyio
    async def test_unaccept_answer_success(self):
        session = _mock_session()
        q = _question(accepted_answer_id=5)
        session.execute.return_value = _mock_execute_scalar(q)
        repo = QuestionRepository(session)

        result = await repo.unaccept_answer(question_id=10)
        assert result is q
        assert q.accepted_answer_id is None

    @pytest.mark.anyio
    async def test_unaccept_answer_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.unaccept_answer(question_id=999)
        assert result is None

    @pytest.mark.anyio
    async def test_log_query(self):
        session = _mock_session()
        repo = QuestionRepository(session)

        await repo.log_query(question_id=10, viewer_id=50, ip="127.0.0.1", user_agent="Mozilla")
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_count_views(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar_one(42)
        repo = QuestionRepository(session)

        count = await repo.count_views(10)
        assert count == 42

    @pytest.mark.anyio
    async def test_vote_new(self):
        session = _mock_session()
        # First call: _get_vote returns None (no existing vote)
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.vote(question_id=10, user_id=50, vote_type="POSITIVE")
        assert result.attitude == "POSITIVE"
        assert result.attitudable_id == 10
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_vote_update_existing(self):
        session = _mock_session()
        existing = _attitude(attitude="POSITIVE")
        session.execute.return_value = _mock_execute_scalar(existing)
        repo = QuestionRepository(session)

        result = await repo.vote(question_id=10, user_id=50, vote_type="NEGATIVE")
        assert result.attitude == "NEGATIVE"

    @pytest.mark.anyio
    async def test_remove_vote_success(self):
        session = _mock_session()
        existing = _attitude()
        session.execute.return_value = _mock_execute_scalar(existing)
        repo = QuestionRepository(session)

        result = await repo.remove_vote(question_id=10, user_id=50)
        assert result is True
        session.delete.assert_awaited_once_with(existing)

    @pytest.mark.anyio
    async def test_remove_vote_no_vote(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.remove_vote(question_id=10, user_id=50)
        assert result is False

    @pytest.mark.anyio
    async def test_get_user_vote_found(self):
        session = _mock_session()
        existing = _attitude(attitude="NEGATIVE")
        session.execute.return_value = _mock_execute_scalar(existing)
        repo = QuestionRepository(session)

        result = await repo.get_user_vote(10, 50)
        assert result == "NEGATIVE"

    @pytest.mark.anyio
    async def test_get_user_vote_none(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionRepository(session)

        result = await repo.get_user_vote(10, 50)
        assert result is None

    @pytest.mark.anyio
    async def test_count_votes(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_rows([
            ("POSITIVE", 3),
            ("NEGATIVE", 1),
        ])
        repo = QuestionRepository(session)

        counts = await repo.count_votes(10)
        assert counts == {"POSITIVE": 3, "NEGATIVE": 1}

    @pytest.mark.anyio
    async def test_count_votes_empty(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_rows([])
        repo = QuestionRepository(session)

        counts = await repo.count_votes(10)
        assert counts == {"POSITIVE": 0, "NEGATIVE": 0}

    @pytest.mark.anyio
    async def test_log_search(self):
        session = _mock_session()
        repo = QuestionRepository(session)

        await repo.log_search(
            keywords="test",
            first_question_id=1,
            page_size=10,
            result_count=5,
            duration_ms=100.0,
            searcher_id=50,
            ip="127.0.0.1",
            user_agent="Mozilla",
        )
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_get_trending_questions(self):
        session = _mock_session()
        q1 = _question(id=1)
        session.execute.return_value = _mock_execute_scalars([q1])
        repo = QuestionRepository(session)

        result = await repo.get_trending_questions(limit=5, days=7)
        assert result == [q1]

    @pytest.mark.anyio
    async def test_get_stats(self):
        session = _mock_session()
        session.execute.side_effect = [
            _mock_execute_scalar_one(100),   # total questions
            _mock_execute_scalar_one(40),    # answered
            _mock_execute_scalar_one(500),   # views
        ]
        repo = QuestionRepository(session)

        stats = await repo.get_stats()
        assert stats["totalQuestions"] == 100
        assert stats["answeredQuestions"] == 40
        assert stats["unansweredQuestions"] == 60
        assert stats["totalViews"] == 500

    @pytest.mark.anyio
    async def test_get_popular_search_terms(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_rows([
            ("python", 50),
            ("testing", 30),
        ])
        repo = QuestionRepository(session)

        result = await repo.get_popular_search_terms(limit=10, days=7)
        assert result == [
            {"keyword": "python", "count": 50},
            {"keyword": "testing", "count": 30},
        ]

    @pytest.mark.anyio
    async def test_update_question_all_fields(self):
        session = _mock_session()
        q = _question()
        repo = QuestionRepository(session)

        result = await repo.update_question(q, title="New Title", content="New Content", type_=2)
        assert result.title == "New Title"
        assert result.content == "New Content"
        assert result.type == 2

    @pytest.mark.anyio
    async def test_update_question_partial(self):
        session = _mock_session()
        q = _question()
        repo = QuestionRepository(session)

        result = await repo.update_question(q, title="Only Title")
        assert result.title == "Only Title"
        assert result.content == "Please help me write tests."

    @pytest.mark.anyio
    async def test_soft_delete(self):
        session = _mock_session()
        q = _question()
        repo = QuestionRepository(session)

        await repo.soft_delete(q)
        assert q.deleted_at is not None
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_set_bounty(self):
        session = _mock_session()
        q = _question(bounty=0)
        repo = QuestionRepository(session)

        result = await repo.set_bounty(q, 100)
        assert result.bounty == 100

    @pytest.mark.anyio
    async def test_list_followers(self):
        session = _mock_session()
        session.execute.side_effect = [
            _mock_execute_rows([(50,), (60,)]),
            _mock_execute_scalar_one(2),
        ]
        repo = QuestionRepository(session)

        ids, total = await repo.list_followers(question_id=10, limit=10, offset=0)
        assert ids == [50, 60]
        assert total == 2

    @pytest.mark.anyio
    async def test_list_by_user(self):
        session = _mock_session()
        q1 = _question(id=1)
        session.execute.side_effect = [
            _mock_execute_scalars([q1]),
            _mock_execute_scalar_one(1),
        ]
        repo = QuestionRepository(session)

        rows, total = await repo.list_by_user(user_id=50, limit=10, offset=0)
        assert rows == [q1]
        assert total == 1


# ---------------------------------------------------------------------------
# QuestionTopicRepository
# ---------------------------------------------------------------------------


class TestQuestionTopicRepository:
    @pytest.mark.anyio
    async def test_replace_topics(self):
        session = _mock_session()
        old_rel = SimpleNamespace(deleted_at=None)
        session.execute.return_value = _mock_execute_scalars([old_rel])
        repo = QuestionTopicRepository(session)

        await repo.replace_topics(question_id=10, topic_ids=[1, 2, 3], user_id=50)
        # Old relation should be soft deleted
        assert old_rel.deleted_at is not None
        # 3 new relations added
        assert session.add.call_count == 3
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_list_topic_ids_empty(self):
        session = _mock_session()
        repo = QuestionTopicRepository(session)

        result = await repo.list_topic_ids([])
        assert result == {}

    @pytest.mark.anyio
    async def test_list_topic_ids(self):
        session = _mock_session()
        rel1 = SimpleNamespace(question_id=10, topic_id=1)
        rel2 = SimpleNamespace(question_id=10, topic_id=2)
        rel3 = SimpleNamespace(question_id=20, topic_id=3)
        session.execute.return_value = _mock_execute_scalars([rel1, rel2, rel3])
        repo = QuestionTopicRepository(session)

        result = await repo.list_topic_ids([10, 20])
        assert result == {10: [1, 2], 20: [3]}

    @pytest.mark.anyio
    async def test_validate_topic_ids_empty(self):
        session = _mock_session()
        repo = QuestionTopicRepository(session)

        result = await repo.validate_topic_ids([])
        assert result == set()

    @pytest.mark.anyio
    async def test_validate_topic_ids(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalars([1, 3])
        repo = QuestionTopicRepository(session)

        result = await repo.validate_topic_ids([1, 2, 3])
        assert result == {1, 3}

    @pytest.mark.anyio
    async def test_get_topics_for_question(self):
        session = _mock_session()
        t1 = SimpleNamespace(id=1, name="Python")
        t2 = SimpleNamespace(id=2, name="Testing")
        session.execute.return_value = _mock_execute_scalars([t1, t2])
        repo = QuestionTopicRepository(session)

        result = await repo.get_topics_for_question(10)
        assert result == [{"id": 1, "name": "Python"}, {"id": 2, "name": "Testing"}]

    @pytest.mark.anyio
    async def test_get_topics_for_questions_empty(self):
        session = _mock_session()
        repo = QuestionTopicRepository(session)

        result = await repo.get_topics_for_questions([])
        assert result == {}

    @pytest.mark.anyio
    async def test_get_topics_for_questions(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_rows([
            (10, 1, "Python"),
            (10, 2, "Testing"),
            (20, 3, "Django"),
        ])
        repo = QuestionTopicRepository(session)

        result = await repo.get_topics_for_questions([10, 20])
        assert result == {
            10: [{"id": 1, "name": "Python"}, {"id": 2, "name": "Testing"}],
            20: [{"id": 3, "name": "Django"}],
        }


# ---------------------------------------------------------------------------
# QuestionInvitationRepository
# ---------------------------------------------------------------------------


class TestQuestionInvitationRepository:
    @pytest.mark.anyio
    async def test_create_invitation_new(self):
        session = _mock_session()
        session.execute.return_value = _mock_execute_scalar(None)
        repo = QuestionInvitationRepository(session)

        result = await repo.create_invitation(question_id=10, user_id=99)
        assert result.question_id == 10
        assert result.user_id == 99
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_create_invitation_existing(self):
        session = _mock_session()
        existing = _invitation()
        session.execute.return_value = _mock_execute_scalar(existing)
        repo = QuestionInvitationRepository(session)

        result = await repo.create_invitation(question_id=10, user_id=99)
        assert result is existing
        assert result.updated_at is not None

    @pytest.mark.anyio
    async def test_get_by_id(self):
        session = _mock_session()
        inv = _invitation()
        session.execute.return_value = _mock_execute_scalar(inv)
        repo = QuestionInvitationRepository(session)

        result = await repo.get_by_id(1)
        assert result is inv

    @pytest.mark.anyio
    async def test_list_invitations(self):
        session = _mock_session()
        inv = _invitation()
        session.execute.side_effect = [
            _mock_execute_scalars([inv]),
            _mock_execute_scalar_one(1),
        ]
        repo = QuestionInvitationRepository(session)

        rows, total = await repo.list_invitations(question_id=10, limit=10, offset=0)
        assert rows == [inv]
        assert total == 1

    @pytest.mark.anyio
    async def test_hard_delete(self):
        session = _mock_session()
        inv = _invitation()
        repo = QuestionInvitationRepository(session)

        await repo.hard_delete(inv)
        session.delete.assert_awaited_once_with(inv)
