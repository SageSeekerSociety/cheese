from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.comments.services import CommentService, _comment_to_dto

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_comment(**overrides) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "commentable_type": "QUESTION",
        "commentable_id": 10,
        "content": "Great question!",
        "created_by_id": 99,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(repo: AsyncMock | None = None) -> tuple[CommentService, AsyncMock]:
    repo = repo or AsyncMock()
    # Defaults so list_comments / get_comment enrichment helpers don't choke
    # on AsyncMock auto-return values when tests don't care about enrichment.
    repo.bulk_count_votes.return_value = {}
    repo.bulk_get_user_votes.return_value = {}
    repo.list_sub_comments.return_value = {}
    return CommentService(repo=repo), repo


# ---------------------------------------------------------------------------
# _comment_to_dto
# ---------------------------------------------------------------------------


class TestCommentToDto:
    def test_converts_all_fields(self):
        comment = _make_comment(id=7, commentable_type="ANSWER", commentable_id=42)
        dto = _comment_to_dto(comment)

        assert dto["id"] == 7
        assert dto["commentable_type"] == "ANSWER"
        assert dto["commentable_id"] == 42
        assert dto["content"] == "Great question!"
        assert dto["created_by_id"] == 99
        assert isinstance(dto["created_at"], int)
        assert isinstance(dto["updated_at"], int)

    def test_timestamps_as_epoch_ms(self):
        ts = datetime(2025, 6, 15, 12, 0, 0)
        comment = _make_comment(created_at=ts, updated_at=ts)
        dto = _comment_to_dto(comment)
        expected_ms = int(ts.timestamp() * 1000)
        assert dto["created_at"] == expected_ms
        assert dto["updated_at"] == expected_ms

    def test_none_timestamps_become_zero(self):
        comment = _make_comment(created_at=None, updated_at=None)
        dto = _comment_to_dto(comment)
        assert dto["created_at"] == 0
        assert dto["updated_at"] == 0


# ---------------------------------------------------------------------------
# create_comment
# ---------------------------------------------------------------------------


class TestCreateComment:
    @pytest.mark.anyio
    async def test_returns_id(self):
        svc, repo = _make_service()
        repo.create.return_value = _make_comment(id=55)

        result = await svc.create_comment(
            commentable_type="QUESTION",
            commentable_id=10,
            content="Nice",
            created_by_id=3,
        )

        assert result == {"id": 55}
        repo.create.assert_awaited_once_with(
            commentable_type="QUESTION",
            commentable_id=10,
            content="Nice",
            created_by_id=3,
        )


# ---------------------------------------------------------------------------
# list_comments
# ---------------------------------------------------------------------------


class TestListComments:
    @pytest.mark.anyio
    async def test_returns_items_and_page_metadata(self):
        svc, repo = _make_service()
        comments = [_make_comment(id=i) for i in range(3)]
        repo.list_comments.return_value = (comments, 3)

        items, page = await svc.list_comments(
            commentable_type="QUESTION",
            commentable_id=10,
            page_start=0,
            page_size=10,
        )

        assert len(items) == 3
        assert [it["id"] for it in items] == [0, 1, 2]
        assert page["pageStart"] == 0
        assert page["pageSize"] == 3
        assert page["total"] == 3
        assert page["hasMore"] is False
        assert page["nextStart"] is None

    @pytest.mark.anyio
    async def test_has_more_when_total_exceeds_returned(self):
        svc, repo = _make_service()
        comments = [_make_comment(id=i) for i in range(2)]
        repo.list_comments.return_value = (comments, 5)

        items, page = await svc.list_comments(
            commentable_type="QUESTION",
            commentable_id=10,
            page_start=0,
            page_size=2,
        )

        assert page["hasMore"] is True
        assert page["nextStart"] == 2

    @pytest.mark.anyio
    async def test_page_start_none_defaults_to_zero(self):
        svc, repo = _make_service()
        repo.list_comments.return_value = ([], 0)

        _, page = await svc.list_comments(
            commentable_type="QUESTION",
            commentable_id=10,
            page_start=None,
            page_size=10,
        )

        repo.list_comments.assert_awaited_once_with(
            commentable_type="QUESTION",
            commentable_id=10,
            limit=10,
            offset=0,
        )
        assert page["pageStart"] == 0

    @pytest.mark.anyio
    async def test_empty_result(self):
        svc, repo = _make_service()
        repo.list_comments.return_value = ([], 0)

        items, page = await svc.list_comments(
            commentable_type="ANSWER",
            commentable_id=99,
            page_start=0,
            page_size=10,
        )

        assert items == []
        assert page["total"] == 0
        assert page["hasMore"] is False
        assert page["nextStart"] is None

    @pytest.mark.anyio
    async def test_last_page_has_more_false(self):
        """Request the second page that finishes the result set."""
        svc, repo = _make_service()
        comments = [_make_comment(id=3)]
        repo.list_comments.return_value = (comments, 4)

        _, page = await svc.list_comments(
            commentable_type="QUESTION",
            commentable_id=10,
            page_start=3,
            page_size=10,
        )

        assert page["hasMore"] is False
        assert page["nextStart"] is None


# ---------------------------------------------------------------------------
# get_comment
# ---------------------------------------------------------------------------


class TestGetComment:
    @pytest.mark.anyio
    async def test_returns_dto(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=8)

        result = await svc.get_comment(8)

        assert result["id"] == 8
        repo.get_by_id.assert_awaited_once_with(8)

    @pytest.mark.anyio
    async def test_not_found_raises(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.get_comment(999)


# ---------------------------------------------------------------------------
# update_comment
# ---------------------------------------------------------------------------


class TestUpdateComment:
    @pytest.mark.anyio
    async def test_updates_and_returns_dto(self):
        svc, repo = _make_service()
        comment = _make_comment(id=5, created_by_id=7, content="old")
        repo.get_by_id.return_value = comment
        repo.update.return_value = comment

        result = await svc.update_comment(comment_id=5, user_id=7, content="new")

        repo.update.assert_awaited_once_with(comment, content="new")
        assert result["id"] == 5

    @pytest.mark.anyio
    async def test_not_found_raises(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.update_comment(comment_id=1, user_id=7, content="x")

    @pytest.mark.anyio
    async def test_wrong_user_raises_forbidden(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=5, created_by_id=7)

        with pytest.raises(ForbiddenError):
            await svc.update_comment(comment_id=5, user_id=999, content="x")

        repo.update.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete_comment
# ---------------------------------------------------------------------------


class TestDeleteComment:
    @pytest.mark.anyio
    async def test_soft_deletes(self):
        svc, repo = _make_service()
        comment = _make_comment(id=3, created_by_id=7)
        repo.get_by_id.return_value = comment

        result = await svc.delete_comment(comment_id=3, user_id=7)

        assert result is None
        repo.soft_delete.assert_awaited_once_with(comment)

    @pytest.mark.anyio
    async def test_not_found_raises(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.delete_comment(comment_id=1, user_id=7)

    @pytest.mark.anyio
    async def test_wrong_user_raises_forbidden(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=3, created_by_id=7)

        with pytest.raises(ForbiddenError):
            await svc.delete_comment(comment_id=3, user_id=999)

        repo.soft_delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# vote_comment
# ---------------------------------------------------------------------------


class TestVoteComment:
    @pytest.mark.anyio
    async def test_positive_vote_returns_counts(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=10)
        repo.vote.return_value = None
        repo.count_votes.return_value = {"POSITIVE": 3, "NEGATIVE": 1}

        result = await svc.vote_comment(comment_id=10, user_id=5, vote_type="POSITIVE")

        assert result == {"upvotes": 3, "downvotes": 1, "userVote": "POSITIVE"}
        repo.vote.assert_awaited_once_with(comment_id=10, user_id=5, vote_type="POSITIVE")

    @pytest.mark.anyio
    async def test_negative_vote_returns_counts(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=10)
        repo.vote.return_value = None
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 2}

        result = await svc.vote_comment(comment_id=10, user_id=5, vote_type="NEGATIVE")

        assert result == {"upvotes": 0, "downvotes": 2, "userVote": "NEGATIVE"}

    @pytest.mark.anyio
    async def test_invalid_vote_type_raises_bad_request(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=10)

        with pytest.raises(BadRequestError):
            await svc.vote_comment(comment_id=10, user_id=5, vote_type="NEUTRAL")

        repo.vote.assert_not_awaited()

    @pytest.mark.anyio
    async def test_comment_not_found_raises(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.vote_comment(comment_id=999, user_id=5, vote_type="POSITIVE")

    @pytest.mark.anyio
    async def test_missing_vote_key_defaults_to_zero(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=10)
        repo.vote.return_value = None
        # count_votes returns only POSITIVE; NEGATIVE is absent
        repo.count_votes.return_value = {"POSITIVE": 1}

        result = await svc.vote_comment(comment_id=10, user_id=5, vote_type="POSITIVE")

        assert result["downvotes"] == 0


# ---------------------------------------------------------------------------
# remove_comment_vote
# ---------------------------------------------------------------------------


class TestRemoveCommentVote:
    @pytest.mark.anyio
    async def test_removes_vote_and_returns_counts(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=10)
        repo.remove_vote.return_value = True
        repo.count_votes.return_value = {"POSITIVE": 2, "NEGATIVE": 0}

        result = await svc.remove_comment_vote(comment_id=10, user_id=5)

        assert result == {"upvotes": 2, "downvotes": 0, "userVote": None}
        repo.remove_vote.assert_awaited_once_with(comment_id=10, user_id=5)

    @pytest.mark.anyio
    async def test_comment_not_found_raises(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.remove_comment_vote(comment_id=999, user_id=5)


# ---------------------------------------------------------------------------
# get_comment_votes
# ---------------------------------------------------------------------------


class TestGetCommentVotes:
    @pytest.mark.anyio
    async def test_returns_counts_with_user_vote(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=10)
        repo.count_votes.return_value = {"POSITIVE": 4, "NEGATIVE": 1}
        repo.get_user_vote.return_value = "POSITIVE"

        result = await svc.get_comment_votes(comment_id=10, user_id=5)

        assert result == {"upvotes": 4, "downvotes": 1, "userVote": "POSITIVE"}
        repo.get_user_vote.assert_awaited_once_with(10, 5)

    @pytest.mark.anyio
    async def test_no_user_id_skips_user_vote_lookup(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=10)
        repo.count_votes.return_value = {"POSITIVE": 2, "NEGATIVE": 0}

        result = await svc.get_comment_votes(comment_id=10, user_id=None)

        assert result["userVote"] is None
        repo.get_user_vote.assert_not_awaited()

    @pytest.mark.anyio
    async def test_comment_not_found_raises(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.get_comment_votes(comment_id=999, user_id=5)

    @pytest.mark.anyio
    async def test_user_has_no_vote(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _make_comment(id=10)
        repo.count_votes.return_value = {"POSITIVE": 1, "NEGATIVE": 0}
        repo.get_user_vote.return_value = None

        result = await svc.get_comment_votes(comment_id=10, user_id=5)

        assert result["userVote"] is None
