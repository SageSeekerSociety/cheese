from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.answers.services import AnswersService, _answer_to_dto, _profile_to_dto

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _answer(**overrides):
    defaults = {
        "id": 1,
        "question_id": 10,
        "created_by_id": 99,
        "content": "My answer text",
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _profile(**overrides):
    defaults = {
        "user_id": 99,
        "nickname": "alice",
        "avatar_id": 5,
        "intro": "hi there",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _question(**overrides):
    defaults = {
        "id": 10,
        "created_by_id": 50,
        "title": "How?",
        "content": "Details",
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


def _make_service(
    repo=None, question_repo=None, profile_repo=None
) -> tuple[AnswersService, AsyncMock, AsyncMock, AsyncMock]:
    repo = repo or AsyncMock()
    question_repo = question_repo or AsyncMock()
    profile_repo = profile_repo or AsyncMock()
    svc = AnswersService(repo=repo, question_repo=question_repo, profile_repo=profile_repo)
    return svc, repo, question_repo, profile_repo


# ---------------------------------------------------------------------------
# _answer_to_dto / _profile_to_dto (pure helpers)
# ---------------------------------------------------------------------------


class TestAnswerToDto:
    def test_basic_conversion(self):
        answer = _answer(id=7, question_id=3, content="body")
        author = {"id": 99, "nickname": "alice"}
        dto = _answer_to_dto(answer, author=author)
        assert dto["id"] == 7
        assert dto["question_id"] == 3
        assert dto["content"] == "body"
        assert dto["created_by"] == 99
        assert dto["author"] == author
        assert dto["created_at"] == NOW_MS
        assert dto["updated_at"] == NOW_MS

    def test_none_timestamps(self):
        answer = _answer(created_at=None, updated_at=None)
        dto = _answer_to_dto(answer)
        assert dto["created_at"] == 0
        assert dto["updated_at"] == 0

    def test_none_author(self):
        dto = _answer_to_dto(_answer(), author=None)
        assert dto["author"] is None


class TestProfileToDto:
    def test_non_none_profile(self):
        p = _profile(user_id=3, nickname="bob", avatar_id=7, intro="hey")
        dto = _profile_to_dto(p)
        assert dto == {"id": 3, "nickname": "bob", "avatarId": 7, "intro": "hey"}

    def test_none_profile(self):
        assert _profile_to_dto(None) is None


# ---------------------------------------------------------------------------
# create_answer
# ---------------------------------------------------------------------------


class TestCreateAnswer:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, q_repo, p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()
        repo.has_user_answered_question.return_value = False
        created = _answer(id=42)
        repo.create_answer.return_value = created
        p_repo.get_profile_by_user_id.return_value = _profile()

        result = await svc.create_answer(question_id=10, user_id=99, content="Answer!")

        repo.create_answer.assert_awaited_once_with(
            question_id=10, created_by_id=99, content="Answer!"
        )
        assert result["id"] == 42
        assert result["author"]["nickname"] == "alice"

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, _repo, q_repo, _p_repo = _make_service()
        q_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.create_answer(question_id=999, user_id=1, content="text")

    @pytest.mark.anyio
    async def test_empty_content_rejected(self):
        svc, _repo, q_repo, _p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()

        with pytest.raises(BadRequestError, match="content cannot be empty"):
            await svc.create_answer(question_id=10, user_id=1, content="   ")

    @pytest.mark.anyio
    async def test_whitespace_only_content_rejected(self):
        svc, _repo, q_repo, _p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()

        with pytest.raises(BadRequestError, match="content cannot be empty"):
            await svc.create_answer(question_id=10, user_id=1, content="\n\t  ")

    @pytest.mark.anyio
    async def test_duplicate_answer_rejected(self):
        svc, repo, q_repo, _p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()
        repo.has_user_answered_question.return_value = True

        with pytest.raises(BadRequestError, match="already answered"):
            await svc.create_answer(question_id=10, user_id=99, content="Another")


# ---------------------------------------------------------------------------
# list_answers
# ---------------------------------------------------------------------------


class TestListAnswers:
    @pytest.mark.anyio
    async def test_first_page(self):
        svc, repo, q_repo, p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()
        repo.list_all_answer_ids_for_question.return_value = [1, 2, 3, 4, 5]
        rows = [_answer(id=1, created_by_id=99), _answer(id=2, created_by_id=100)]
        repo.list_answers_for_question.return_value = rows
        p_repo.get_profiles_by_user_ids.return_value = {
            99: _profile(user_id=99),
            100: _profile(user_id=100, nickname="bob"),
        }

        items, page = await svc.list_answers(question_id=10, page_start=None, page_size=2)

        assert len(items) == 2
        assert items[0]["id"] == 1
        assert items[1]["id"] == 2
        assert page["page_start"] == 1
        assert page["page_size"] == 2
        assert page["has_prev"] is False
        assert page["has_more"] is True
        assert page["next_start"] == 3

    @pytest.mark.anyio
    async def test_middle_page(self):
        svc, repo, q_repo, p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()
        repo.list_all_answer_ids_for_question.return_value = [1, 2, 3, 4, 5]
        rows = [_answer(id=3, created_by_id=99)]
        repo.list_answers_for_question.return_value = rows
        p_repo.get_profiles_by_user_ids.return_value = {99: _profile()}

        items, page = await svc.list_answers(question_id=10, page_start=3, page_size=1)

        assert page["has_prev"] is True
        assert page["prev_start"] == 1
        assert page["has_more"] is True
        assert page["next_start"] == 4

    @pytest.mark.anyio
    async def test_last_page(self):
        svc, repo, q_repo, p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()
        repo.list_all_answer_ids_for_question.return_value = [1, 2, 3]
        rows = [_answer(id=3, created_by_id=99)]
        repo.list_answers_for_question.return_value = rows
        p_repo.get_profiles_by_user_ids.return_value = {99: _profile()}

        items, page = await svc.list_answers(question_id=10, page_start=3, page_size=2)

        assert page["has_more"] is False
        assert page["next_start"] == 0

    @pytest.mark.anyio
    async def test_empty_list(self):
        svc, repo, q_repo, p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()
        repo.list_all_answer_ids_for_question.return_value = []
        repo.list_answers_for_question.return_value = []
        p_repo.get_profiles_by_user_ids.return_value = {}

        items, page = await svc.list_answers(question_id=10, page_start=None, page_size=10)

        assert items == []
        assert page["page_start"] == 0
        assert page["page_size"] == 0
        assert page["has_prev"] is False
        assert page["has_more"] is False

    @pytest.mark.anyio
    async def test_invalid_page_start_falls_back_to_zero(self):
        svc, repo, q_repo, p_repo = _make_service()
        q_repo.get_by_id.return_value = _question()
        repo.list_all_answer_ids_for_question.return_value = [1, 2, 3]
        rows = [_answer(id=1, created_by_id=99)]
        repo.list_answers_for_question.return_value = rows
        p_repo.get_profiles_by_user_ids.return_value = {99: _profile()}

        # page_start=999 does not exist in [1,2,3], so falls back to index 0
        items, page = await svc.list_answers(question_id=10, page_start=999, page_size=1)

        assert page["page_start"] == 1
        assert page["has_prev"] is False

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, _repo, q_repo, _p_repo = _make_service()
        q_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.list_answers(question_id=999, page_start=None, page_size=10)


# ---------------------------------------------------------------------------
# vote_answer
# ---------------------------------------------------------------------------


class TestVoteAnswer:
    @pytest.mark.anyio
    async def test_upvote(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)
        repo.vote.return_value = None
        repo.count_votes.return_value = {"POSITIVE": 3, "NEGATIVE": 1}

        result = await svc.vote_answer(answer_id=5, user_id=7, vote_type="POSITIVE")

        repo.vote.assert_awaited_once_with(answer_id=5, user_id=7, vote_type="POSITIVE")
        assert result["upvotes"] == 3
        assert result["downvotes"] == 1
        assert result["userVote"] == "POSITIVE"

    @pytest.mark.anyio
    async def test_downvote(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)
        repo.vote.return_value = None
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 2}

        result = await svc.vote_answer(answer_id=5, user_id=7, vote_type="NEGATIVE")

        assert result["upvotes"] == 0
        assert result["downvotes"] == 2
        assert result["userVote"] == "NEGATIVE"

    @pytest.mark.anyio
    async def test_invalid_vote_type(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)

        with pytest.raises(BadRequestError, match="Invalid vote type"):
            await svc.vote_answer(answer_id=5, user_id=7, vote_type="LOVE")

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Answer not found"):
            await svc.vote_answer(answer_id=404, user_id=7, vote_type="POSITIVE")


# ---------------------------------------------------------------------------
# remove_answer_vote
# ---------------------------------------------------------------------------


class TestRemoveAnswerVote:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)
        repo.remove_vote.return_value = True
        repo.count_votes.return_value = {"POSITIVE": 1, "NEGATIVE": 0}

        result = await svc.remove_answer_vote(answer_id=5, user_id=7)

        repo.remove_vote.assert_awaited_once_with(answer_id=5, user_id=7)
        assert result["upvotes"] == 1
        assert result["downvotes"] == 0
        assert result["userVote"] is None

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Answer not found"):
            await svc.remove_answer_vote(answer_id=404, user_id=7)


# ---------------------------------------------------------------------------
# get_answer_votes
# ---------------------------------------------------------------------------


class TestGetAnswerVotes:
    @pytest.mark.anyio
    async def test_with_user_id(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)
        repo.count_votes.return_value = {"POSITIVE": 2, "NEGATIVE": 1}
        repo.get_user_vote.return_value = "POSITIVE"

        result = await svc.get_answer_votes(answer_id=5, user_id=7)

        assert result["upvotes"] == 2
        assert result["downvotes"] == 1
        assert result["userVote"] == "POSITIVE"

    @pytest.mark.anyio
    async def test_without_user_id(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)
        repo.count_votes.return_value = {"POSITIVE": 4, "NEGATIVE": 0}

        result = await svc.get_answer_votes(answer_id=5, user_id=None)

        repo.get_user_vote.assert_not_awaited()
        assert result["userVote"] is None

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.get_answer_votes(answer_id=404, user_id=1)


# ---------------------------------------------------------------------------
# get_answer  (single answer detail with question)
# ---------------------------------------------------------------------------


class TestGetAnswer:
    @pytest.mark.anyio
    async def test_with_user_and_positive_vote(self):
        svc, repo, q_repo, p_repo = _make_service()
        answer = _answer(id=1, question_id=10, created_by_id=99)
        repo.get_by_id.return_value = answer
        p_repo.get_profile_by_user_id.side_effect = lambda uid: (
            _profile(user_id=uid, nickname="alice")
            if uid == 99
            else _profile(user_id=uid, nickname="qauthor")
        )
        repo.count_votes.return_value = {"POSITIVE": 5, "NEGATIVE": 2}
        repo.get_user_vote.return_value = "POSITIVE"
        repo.count_favorites.return_value = 3
        repo.is_favorited.return_value = True
        q_repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        dto, q_dto = await svc.get_answer(answer_id=1, user_id=7)

        assert dto["id"] == 1
        assert dto["attitudes"]["positive_count"] == 5
        assert dto["attitudes"]["negative_count"] == 2
        assert dto["attitudes"]["difference"] == 3
        assert dto["attitudes"]["user_attitude"] == "POSITIVE"
        assert dto["favorite_count"] == 3
        assert dto["is_favorite"] is True
        assert dto["comment_count"] == 0
        assert dto["view_count"] == 0
        assert dto["is_group"] is False
        assert q_dto is not None
        assert q_dto["id"] == 10
        assert q_dto["title"] == "How?"

    @pytest.mark.anyio
    async def test_with_user_and_negative_vote(self):
        svc, repo, q_repo, p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=1)
        p_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 1}
        repo.get_user_vote.return_value = "NEGATIVE"
        repo.count_favorites.return_value = 0
        repo.is_favorited.return_value = False
        q_repo.get_by_id.return_value = _question()

        dto, _ = await svc.get_answer(answer_id=1, user_id=7)

        assert dto["attitudes"]["user_attitude"] == "NEGATIVE"

    @pytest.mark.anyio
    async def test_without_user_id(self):
        svc, repo, q_repo, p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=1)
        p_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_votes.return_value = {"POSITIVE": 2, "NEGATIVE": 0}
        repo.count_favorites.return_value = 1
        q_repo.get_by_id.return_value = _question()

        dto, _ = await svc.get_answer(answer_id=1, user_id=None)

        repo.get_user_vote.assert_not_awaited()
        repo.is_favorited.assert_not_awaited()
        assert dto["attitudes"]["user_attitude"] == "UNDEFINED"
        assert dto["is_favorite"] is False

    @pytest.mark.anyio
    async def test_no_vote_returns_undefined(self):
        svc, repo, q_repo, p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=1)
        p_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 0}
        repo.get_user_vote.return_value = None
        repo.count_favorites.return_value = 0
        repo.is_favorited.return_value = False
        q_repo.get_by_id.return_value = _question()

        dto, _ = await svc.get_answer(answer_id=1, user_id=7)

        assert dto["attitudes"]["user_attitude"] == "UNDEFINED"

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Answer not found"):
            await svc.get_answer(answer_id=404, user_id=1)

    @pytest.mark.anyio
    async def test_question_deleted_returns_none_question_dto(self):
        svc, repo, q_repo, p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=1, question_id=10)
        p_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 0}
        repo.get_user_vote.return_value = None
        repo.count_favorites.return_value = 0
        repo.is_favorited.return_value = False
        q_repo.get_by_id.return_value = None

        dto, q_dto = await svc.get_answer(answer_id=1, user_id=7)

        assert dto["id"] == 1
        assert q_dto is None


# ---------------------------------------------------------------------------
# update_answer
# ---------------------------------------------------------------------------


class TestUpdateAnswer:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, _q_repo, p_repo = _make_service()
        original = _answer(id=3, created_by_id=99)
        repo.get_by_id.return_value = original
        updated = _answer(id=3, created_by_id=99, content="Updated content")
        repo.update_answer.return_value = updated
        p_repo.get_profile_by_user_id.return_value = _profile()

        result = await svc.update_answer(answer_id=3, user_id=99, content="Updated content")

        repo.update_answer.assert_awaited_once_with(original, content="Updated content")
        assert result["content"] == "Updated content"

    @pytest.mark.anyio
    async def test_not_owner_forbidden(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=3, created_by_id=99)

        with pytest.raises(ForbiddenError, match="Only the answer owner"):
            await svc.update_answer(answer_id=3, user_id=77, content="Hack")

    @pytest.mark.anyio
    async def test_empty_content_rejected(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=3, created_by_id=99)

        with pytest.raises(BadRequestError, match="content cannot be empty"):
            await svc.update_answer(answer_id=3, user_id=99, content="  ")

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Answer not found"):
            await svc.update_answer(answer_id=404, user_id=1, content="text")


# ---------------------------------------------------------------------------
# delete_answer
# ---------------------------------------------------------------------------


class TestDeleteAnswer:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        answer = _answer(id=3, created_by_id=99)
        repo.get_by_id.return_value = answer

        await svc.delete_answer(answer_id=3, user_id=99)

        repo.soft_delete.assert_awaited_once_with(answer)

    @pytest.mark.anyio
    async def test_not_owner_forbidden(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=3, created_by_id=99)

        with pytest.raises(ForbiddenError, match="Only the answer owner"):
            await svc.delete_answer(answer_id=3, user_id=77)

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Answer not found"):
            await svc.delete_answer(answer_id=404, user_id=1)


# ---------------------------------------------------------------------------
# add_favorite
# ---------------------------------------------------------------------------


class TestAddFavorite:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)
        repo.add_favorite.return_value = True
        repo.count_favorites.return_value = 4

        result = await svc.add_favorite(answer_id=5, user_id=7)

        repo.add_favorite.assert_awaited_once_with(answer_id=5, user_id=7)
        assert result == {"favoriteCount": 4, "isFavorited": True}

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Answer not found"):
            await svc.add_favorite(answer_id=404, user_id=7)


# ---------------------------------------------------------------------------
# remove_favorite
# ---------------------------------------------------------------------------


class TestRemoveFavorite:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)
        repo.remove_favorite.return_value = True
        repo.count_favorites.return_value = 2

        result = await svc.remove_favorite(answer_id=5, user_id=7)

        repo.remove_favorite.assert_awaited_once_with(answer_id=5, user_id=7)
        assert result == {"favoriteCount": 2, "isFavorited": False}

    @pytest.mark.anyio
    async def test_not_favorited_raises(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _answer(id=5)
        repo.remove_favorite.return_value = False

        with pytest.raises(BadRequestError, match="Answer not favorited"):
            await svc.remove_favorite(answer_id=5, user_id=7)

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _q_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Answer not found"):
            await svc.remove_favorite(answer_id=404, user_id=7)
