from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.questions.services import (
    QuestionInvitationService,
    QuestionsService,
    _invitation_to_dto,
    _profile_to_user,
    _question_to_dto,
)

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


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
        "user_id": 50,
        "nickname": "alice",
        "avatar_id": 5,
        "intro": "hi there",
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


def _make_service(
    repo=None, topic_repo=None, answer_repo=None, profile_repo=None
) -> tuple[QuestionsService, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    repo = repo or AsyncMock()
    topic_repo = topic_repo or AsyncMock()
    # _enrich_question_list now also bulk-fetches topic objects and author
    # profiles. Default these to empty so tests that don't care about author
    # rendering still pass; tests that do care override explicitly.
    topic_repo.get_topics_for_questions.return_value = {}
    answer_repo = answer_repo or AsyncMock()
    profile_repo = profile_repo or AsyncMock()
    profile_repo.get_profiles_by_user_ids.return_value = {}
    svc = QuestionsService(
        repo=repo,
        topic_repo=topic_repo,
        answer_repo=answer_repo,
        profile_repo=profile_repo,
    )
    return svc, repo, topic_repo, answer_repo, profile_repo


def _make_invitation_service(
    repo=None, question_repo=None, profile_repo=None, answer_repo=None
) -> tuple[QuestionInvitationService, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    repo = repo or AsyncMock()
    question_repo = question_repo or AsyncMock()
    profile_repo = profile_repo or AsyncMock()
    answer_repo = answer_repo or AsyncMock()
    svc = QuestionInvitationService(
        repo=repo,
        question_repo=question_repo,
        profile_repo=profile_repo,
        answer_repo=answer_repo,
    )
    return svc, repo, question_repo, profile_repo, answer_repo


# ---------------------------------------------------------------------------
# _question_to_dto (pure helper)
# ---------------------------------------------------------------------------


class TestQuestionToDto:
    def test_basic_conversion(self):
        q = _question(id=7, title="Why?", content="Body", type=2, bounty=5)
        dto = _question_to_dto(q)
        assert dto["id"] == 7
        assert dto["title"] == "Why?"
        assert dto["content"] == "Body"
        assert dto["type"] == 2
        assert dto["bounty"] == 5
        assert dto["createdBy"] == 50
        assert dto["createdAt"] == NOW_MS
        assert dto["updatedAt"] == NOW_MS
        assert dto["groupId"] is None
        assert dto["acceptedAnswerId"] is None

    def test_none_timestamps(self):
        q = _question(created_at=None, updated_at=None)
        dto = _question_to_dto(q)
        assert dto["createdAt"] == 0
        assert dto["updatedAt"] == 0

    def test_exclude_content(self):
        q = _question(content="secret details")
        dto = _question_to_dto(q, include_content=False)
        assert dto["content"] is None

    def test_include_content_default(self):
        q = _question(content="visible")
        dto = _question_to_dto(q)
        assert dto["content"] == "visible"


# ---------------------------------------------------------------------------
# _invitation_to_dto (pure helper)
# ---------------------------------------------------------------------------


class TestInvitationToDto:
    def test_basic_conversion(self):
        inv = _invitation(id=3, question_id=10, user_id=99)
        user = {"id": 99, "nickname": "bob"}
        dto = _invitation_to_dto(inv, user=user)
        assert dto["id"] == 3
        assert dto["question_id"] == 10
        assert dto["user_id"] == 99
        assert dto["user"] == user
        assert dto["created_at"] == NOW_MS
        assert dto["updated_at"] == NOW_MS
        assert dto["is_answered"] is False

    def test_none_timestamps(self):
        inv = _invitation(created_at=None, updated_at=None)
        dto = _invitation_to_dto(inv)
        assert dto["created_at"] == 0
        assert dto["updated_at"] == 0

    def test_none_user(self):
        inv = _invitation()
        dto = _invitation_to_dto(inv, user=None)
        assert dto["user"] is None


# ---------------------------------------------------------------------------
# _profile_to_user (pure helper)
# ---------------------------------------------------------------------------


class TestProfileToUser:
    def test_non_none_profile(self):
        p = _profile(user_id=3, nickname="bob", avatar_id=7, intro="hey")
        result = _profile_to_user(p)
        assert result == {"id": 3, "nickname": "bob", "avatarId": 7, "intro": "hey"}

    def test_none_profile(self):
        assert _profile_to_user(None) is None


# ---------------------------------------------------------------------------
# create_question
# ---------------------------------------------------------------------------


class TestCreateQuestion:
    @pytest.mark.anyio
    async def test_success_with_topics(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        created = _question(id=42)
        repo.create_question.return_value = created
        topic_repo.validate_topic_ids.return_value = {1, 2}

        result = await svc.create_question(
            user_id=50,
            title="How?",
            content="Details",
            type_=1,
            group_id=None,
            bounty=0,
            topic_ids=[1, 2],
        )

        repo.create_question.assert_awaited_once_with(
            created_by_id=50,
            title="How?",
            content="Details",
            type_=1,
            group_id=None,
            bounty=0,
        )
        topic_repo.replace_topics.assert_awaited_once_with(
            question_id=42, topic_ids=[1, 2], user_id=50
        )
        assert result["id"] == 42
        assert result["topicIds"] == [1, 2]

    @pytest.mark.anyio
    async def test_success_no_topics(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        repo.create_question.return_value = _question(id=42)

        result = await svc.create_question(
            user_id=50,
            title="How?",
            content="Details",
            type_=1,
            group_id=None,
            bounty=0,
            topic_ids=[],
        )

        topic_repo.validate_topic_ids.assert_not_awaited()
        topic_repo.replace_topics.assert_not_awaited()
        assert result["topicIds"] == []

    @pytest.mark.anyio
    async def test_empty_title_rejected(self):
        svc, _repo, _t_repo, _a_repo, _p_repo = _make_service()

        with pytest.raises(BadRequestError, match="title cannot be empty"):
            await svc.create_question(
                user_id=50,
                title="   ",
                content="Details",
                type_=1,
                group_id=None,
                bounty=0,
                topic_ids=[],
            )

    @pytest.mark.anyio
    async def test_empty_content_rejected(self):
        svc, _repo, _t_repo, _a_repo, _p_repo = _make_service()

        with pytest.raises(BadRequestError, match="content cannot be empty"):
            await svc.create_question(
                user_id=50,
                title="Valid title",
                content="   ",
                type_=1,
                group_id=None,
                bounty=0,
                topic_ids=[],
            )

    @pytest.mark.anyio
    async def test_invalid_topic_id_raises_not_found(self):
        svc, _repo, topic_repo, _a_repo, _p_repo = _make_service()
        topic_repo.validate_topic_ids.return_value = {1}  # topic 999 is not valid

        with pytest.raises(NotFoundError, match="Topic not found"):
            await svc.create_question(
                user_id=50,
                title="Valid",
                content="Content",
                type_=1,
                group_id=None,
                bounty=0,
                topic_ids=[1, 999],
            )

    @pytest.mark.anyio
    async def test_title_is_stripped(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        repo.create_question.return_value = _question(id=1, title="Stripped")

        await svc.create_question(
            user_id=50,
            title="  Stripped  ",
            content="Content",
            type_=1,
            group_id=None,
            bounty=0,
            topic_ids=[],
        )

        repo.create_question.assert_awaited_once()
        call_kwargs = repo.create_question.call_args.kwargs
        assert call_kwargs["title"] == "Stripped"


# ---------------------------------------------------------------------------
# get_question
# ---------------------------------------------------------------------------


class TestGetQuestion:
    @pytest.mark.anyio
    async def test_full_detail_with_viewer(self):
        svc, repo, topic_repo, answer_repo, profile_repo = _make_service()
        q = _question(id=10, accepted_answer_id=1)
        repo.get_by_id.return_value = q
        topic_repo.get_topics_for_question.return_value = [{"id": 1, "name": "Python"}]
        profile_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_followers.return_value = 5
        repo.is_following.return_value = True
        repo.count_votes.return_value = {"POSITIVE": 3, "NEGATIVE": 1}
        repo.get_user_vote.return_value = "POSITIVE"
        answer_repo.count_answers_for_question.return_value = 7
        repo.count_comments.return_value = 2
        accepted = _answer(id=1, content="Accepted answer")
        answer_repo.get_by_id.return_value = accepted

        result = await svc.get_question(question_id=10, viewer_id=42)

        assert result["id"] == 10
        assert result["topics"] == [{"id": 1, "name": "Python"}]
        assert result["author"] == {
            "id": 50,
            "nickname": "alice",
            "avatarId": 5,
            "intro": "hi there",
        }
        assert result["follow_count"] == 5
        assert result["is_follow"] is True
        assert result["attitudes"]["positive_count"] == 3
        assert result["attitudes"]["negative_count"] == 1
        assert result["attitudes"]["difference"] == 2
        assert result["attitudes"]["user_attitude"] == "POSITIVE"
        assert result["answer_count"] == 7
        assert result["comment_count"] == 2
        # accepted_answer now carries author + timestamps so the frontend can
        # render Detail.vue:180 (accepted_answer.author.nickname) without crashing.
        assert result["accepted_answer"]["id"] == 1
        assert result["accepted_answer"]["content"] == "Accepted answer"
        assert "author" in result["accepted_answer"]
        assert result["view_count"] == 0
        assert result["is_solved"] is True
        # createdAt/updatedAt keys renamed
        assert "created_at" in result
        assert "updated_at" in result
        assert "createdAt" not in result
        assert "updatedAt" not in result

    @pytest.mark.anyio
    async def test_without_viewer(self):
        svc, repo, topic_repo, answer_repo, profile_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        topic_repo.get_topics_for_question.return_value = []
        profile_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_followers.return_value = 0
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 0}
        answer_repo.count_answers_for_question.return_value = 0
        repo.count_comments.return_value = 0

        result = await svc.get_question(question_id=10, viewer_id=None)

        repo.is_following.assert_not_awaited()
        repo.get_user_vote.assert_not_awaited()
        assert result["is_follow"] is False
        assert result["attitudes"]["user_attitude"] == "UNDEFINED"

    @pytest.mark.anyio
    async def test_viewer_with_no_vote_returns_undefined(self):
        svc, repo, topic_repo, answer_repo, profile_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        topic_repo.get_topics_for_question.return_value = []
        profile_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_followers.return_value = 0
        repo.is_following.return_value = False
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 0}
        repo.get_user_vote.return_value = None
        answer_repo.count_answers_for_question.return_value = 0
        repo.count_comments.return_value = 0

        result = await svc.get_question(question_id=10, viewer_id=42)

        assert result["attitudes"]["user_attitude"] == "UNDEFINED"

    @pytest.mark.anyio
    async def test_no_profile_repo(self):
        repo = AsyncMock()
        topic_repo = AsyncMock()
        answer_repo = AsyncMock()
        svc = QuestionsService(repo=repo, topic_repo=topic_repo, answer_repo=answer_repo)

        repo.get_by_id.return_value = _question(id=10)
        topic_repo.get_topics_for_question.return_value = []
        repo.count_followers.return_value = 0
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 0}
        answer_repo.count_answers_for_question.return_value = 0
        repo.count_comments.return_value = 0

        result = await svc.get_question(question_id=10, viewer_id=None)

        assert result["author"] == {"id": 50}

    @pytest.mark.anyio
    async def test_profile_not_found_fallback(self):
        svc, repo, topic_repo, answer_repo, profile_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        topic_repo.get_topics_for_question.return_value = []
        profile_repo.get_profile_by_user_id.return_value = None
        repo.count_followers.return_value = 0
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 0}
        answer_repo.count_answers_for_question.return_value = 0
        repo.count_comments.return_value = 0

        result = await svc.get_question(question_id=10, viewer_id=None)

        assert result["author"] == {"id": 50}

    @pytest.mark.anyio
    async def test_no_answer_repo(self):
        repo = AsyncMock()
        topic_repo = AsyncMock()
        profile_repo = AsyncMock()
        svc = QuestionsService(repo=repo, topic_repo=topic_repo, profile_repo=profile_repo)

        repo.get_by_id.return_value = _question(id=10)
        topic_repo.get_topics_for_question.return_value = []
        profile_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_followers.return_value = 0
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 0}
        repo.count_comments.return_value = 0

        result = await svc.get_question(question_id=10, viewer_id=None)

        assert result["answer_count"] == 0
        assert result["accepted_answer"] is None

    @pytest.mark.anyio
    async def test_accepted_answer_deleted(self):
        svc, repo, topic_repo, answer_repo, profile_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, accepted_answer_id=99)
        topic_repo.get_topics_for_question.return_value = []
        profile_repo.get_profile_by_user_id.return_value = _profile()
        repo.count_followers.return_value = 0
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 0}
        answer_repo.count_answers_for_question.return_value = 0
        repo.count_comments.return_value = 0
        answer_repo.get_by_id.return_value = None  # answer was deleted

        result = await svc.get_question(question_id=10, viewer_id=None)

        assert result["accepted_answer"] is None

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.get_question(question_id=999, viewer_id=None)


# ---------------------------------------------------------------------------
# search_questions
# ---------------------------------------------------------------------------


class TestSearchQuestions:
    @pytest.mark.anyio
    async def test_basic_search(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        rows = [_question(id=1), _question(id=2)]
        repo.search.return_value = (rows, 5)
        topic_repo.list_topic_ids.return_value = {1: [10], 2: [20, 30]}

        items, page = await svc.search_questions(
            keyword="test", page_size=2, page_start=0, sort_by="createdAt", sort_order="desc"
        )

        assert len(items) == 2
        assert items[0]["id"] == 1
        assert items[0]["content"] is None  # include_content=False
        assert items[0]["topicIds"] == [10]
        assert items[1]["topicIds"] == [20, 30]
        assert page["pageStart"] == 0
        assert page["pageSize"] == 2
        assert page["hasMore"] is True
        assert page["nextStart"] == 2
        assert page["total"] == 5

    @pytest.mark.anyio
    async def test_last_page(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        rows = [_question(id=5)]
        repo.search.return_value = (rows, 5)
        topic_repo.list_topic_ids.return_value = {5: []}

        items, page = await svc.search_questions(
            keyword=None, page_size=10, page_start=4, sort_by="createdAt", sort_order="desc"
        )

        assert len(items) == 1
        assert page["hasMore"] is False
        assert page["nextStart"] is None

    @pytest.mark.anyio
    async def test_empty_results(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        repo.search.return_value = ([], 0)
        topic_repo.list_topic_ids.return_value = {}

        items, page = await svc.search_questions(
            keyword="nothing", page_size=10, page_start=None, sort_by="createdAt", sort_order="desc"
        )

        assert items == []
        assert page["pageStart"] == 0
        assert page["pageSize"] == 0
        assert page["hasMore"] is False
        assert page["nextStart"] is None
        assert page["total"] == 0

    @pytest.mark.anyio
    async def test_negative_page_start_clamped(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        repo.search.return_value = ([], 0)
        topic_repo.list_topic_ids.return_value = {}

        await svc.search_questions(
            keyword=None, page_size=10, page_start=-5, sort_by="createdAt", sort_order="desc"
        )

        call_kwargs = repo.search.call_args.kwargs
        assert call_kwargs["offset"] == 0


# ---------------------------------------------------------------------------
# follow_question / unfollow_question
# ---------------------------------------------------------------------------


class TestFollowQuestion:
    @pytest.mark.anyio
    async def test_follow_success(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.follow_question.return_value = True

        result = await svc.follow_question(question_id=10, user_id=42)

        repo.follow_question.assert_awaited_once_with(question_id=10, user_id=42)
        assert result is True

    @pytest.mark.anyio
    async def test_follow_already_following(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.follow_question.return_value = False

        result = await svc.follow_question(question_id=10, user_id=42)

        assert result is False

    @pytest.mark.anyio
    async def test_follow_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.follow_question(question_id=999, user_id=42)


class TestUnfollowQuestion:
    @pytest.mark.anyio
    async def test_unfollow_success(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.unfollow_question.return_value = True

        result = await svc.unfollow_question(question_id=10, user_id=42)

        repo.unfollow_question.assert_awaited_once_with(question_id=10, user_id=42)
        assert result is True

    @pytest.mark.anyio
    async def test_unfollow_not_following(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.unfollow_question.return_value = False

        result = await svc.unfollow_question(question_id=10, user_id=42)

        assert result is False

    @pytest.mark.anyio
    async def test_unfollow_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.unfollow_question(question_id=999, user_id=42)


# ---------------------------------------------------------------------------
# list_followed
# ---------------------------------------------------------------------------


class TestListFollowed:
    @pytest.mark.anyio
    async def test_basic(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        rows = [_question(id=1), _question(id=2)]
        repo.list_followed.return_value = (rows, 5)
        topic_repo.list_topic_ids.return_value = {1: [10], 2: []}

        items, page = await svc.list_followed(user_id=42, page_size=2, page_start=0)

        assert len(items) == 2
        assert items[0]["topicIds"] == [10]
        assert items[1]["topicIds"] == []
        assert page["hasMore"] is True
        assert page["nextStart"] == 2
        assert page["total"] == 5

    @pytest.mark.anyio
    async def test_empty(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        repo.list_followed.return_value = ([], 0)
        topic_repo.list_topic_ids.return_value = {}

        items, page = await svc.list_followed(user_id=42, page_size=10, page_start=None)

        assert items == []
        assert page["hasMore"] is False
        assert page["nextStart"] is None

    @pytest.mark.anyio
    async def test_none_page_start_defaults_to_zero(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        repo.list_followed.return_value = ([], 0)
        topic_repo.list_topic_ids.return_value = {}

        await svc.list_followed(user_id=42, page_size=10, page_start=None)

        call_kwargs = repo.list_followed.call_args.kwargs
        assert call_kwargs["offset"] == 0


# ---------------------------------------------------------------------------
# vote_question
# ---------------------------------------------------------------------------


class TestVoteQuestion:
    @pytest.mark.anyio
    async def test_upvote(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.vote.return_value = None
        repo.count_votes.return_value = {"POSITIVE": 3, "NEGATIVE": 1}

        result = await svc.vote_question(question_id=10, user_id=42, vote_type="POSITIVE")

        repo.vote.assert_awaited_once_with(question_id=10, user_id=42, vote_type="POSITIVE")
        assert result["upvotes"] == 3
        assert result["downvotes"] == 1
        assert result["userVote"] == "POSITIVE"

    @pytest.mark.anyio
    async def test_downvote(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.vote.return_value = None
        repo.count_votes.return_value = {"POSITIVE": 0, "NEGATIVE": 2}

        result = await svc.vote_question(question_id=10, user_id=42, vote_type="NEGATIVE")

        assert result["upvotes"] == 0
        assert result["downvotes"] == 2
        assert result["userVote"] == "NEGATIVE"

    @pytest.mark.anyio
    async def test_invalid_vote_type(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)

        with pytest.raises(BadRequestError, match="Invalid vote type"):
            await svc.vote_question(question_id=10, user_id=42, vote_type="LOVE")

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.vote_question(question_id=999, user_id=42, vote_type="POSITIVE")


# ---------------------------------------------------------------------------
# remove_question_vote
# ---------------------------------------------------------------------------


class TestRemoveQuestionVote:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.remove_vote.return_value = True
        repo.count_votes.return_value = {"POSITIVE": 1, "NEGATIVE": 0}

        result = await svc.remove_question_vote(question_id=10, user_id=42)

        repo.remove_vote.assert_awaited_once_with(question_id=10, user_id=42)
        assert result["upvotes"] == 1
        assert result["downvotes"] == 0
        assert result["userVote"] is None

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.remove_question_vote(question_id=999, user_id=42)


# ---------------------------------------------------------------------------
# get_question_votes
# ---------------------------------------------------------------------------


class TestGetQuestionVotes:
    @pytest.mark.anyio
    async def test_with_user_id(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.count_votes.return_value = {"POSITIVE": 2, "NEGATIVE": 1}
        repo.get_user_vote.return_value = "POSITIVE"

        result = await svc.get_question_votes(question_id=10, user_id=42)

        assert result["upvotes"] == 2
        assert result["downvotes"] == 1
        assert result["userVote"] == "POSITIVE"

    @pytest.mark.anyio
    async def test_without_user_id(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.count_votes.return_value = {"POSITIVE": 4, "NEGATIVE": 0}

        result = await svc.get_question_votes(question_id=10, user_id=None)

        repo.get_user_vote.assert_not_awaited()
        assert result["userVote"] is None

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.get_question_votes(question_id=999, user_id=1)


# ---------------------------------------------------------------------------
# accept_answer
# ---------------------------------------------------------------------------


class TestAcceptAnswer:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, topic_repo, answer_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)
        answer_repo.get_by_id.return_value = _answer(id=1, question_id=10)
        updated = _question(id=10, accepted_answer_id=1)
        repo.accept_answer.return_value = updated
        topic_repo.list_topic_ids.return_value = {10: [1, 2]}

        result = await svc.accept_answer(question_id=10, answer_id=1, user_id=50)

        repo.accept_answer.assert_awaited_once_with(question_id=10, answer_id=1)
        assert result["acceptedAnswerId"] == 1
        assert result["topicIds"] == [1, 2]

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.accept_answer(question_id=999, answer_id=1, user_id=50)

    @pytest.mark.anyio
    async def test_not_owner_forbidden(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        with pytest.raises(ForbiddenError, match="Only the question owner"):
            await svc.accept_answer(question_id=10, answer_id=1, user_id=77)

    @pytest.mark.anyio
    async def test_no_answer_repo(self):
        repo = AsyncMock()
        topic_repo = AsyncMock()
        svc = QuestionsService(repo=repo, topic_repo=topic_repo, answer_repo=None)
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        with pytest.raises(BadRequestError, match="Answer repository not configured"):
            await svc.accept_answer(question_id=10, answer_id=1, user_id=50)

    @pytest.mark.anyio
    async def test_answer_not_found(self):
        svc, repo, _t_repo, answer_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)
        answer_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Answer not found"):
            await svc.accept_answer(question_id=10, answer_id=999, user_id=50)

    @pytest.mark.anyio
    async def test_answer_wrong_question(self):
        svc, repo, _t_repo, answer_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)
        answer_repo.get_by_id.return_value = _answer(id=1, question_id=20)  # different question

        with pytest.raises(BadRequestError, match="Answer does not belong"):
            await svc.accept_answer(question_id=10, answer_id=1, user_id=50)

    @pytest.mark.anyio
    async def test_accept_answer_repo_returns_none(self):
        svc, repo, topic_repo, answer_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)
        answer_repo.get_by_id.return_value = _answer(id=1, question_id=10)
        repo.accept_answer.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.accept_answer(question_id=10, answer_id=1, user_id=50)


# ---------------------------------------------------------------------------
# unaccept_answer
# ---------------------------------------------------------------------------


class TestUnacceptAnswer:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50, accepted_answer_id=1)
        updated = _question(id=10, accepted_answer_id=None)
        repo.unaccept_answer.return_value = updated
        topic_repo.list_topic_ids.return_value = {10: [1]}

        result = await svc.unaccept_answer(question_id=10, user_id=50)

        repo.unaccept_answer.assert_awaited_once_with(question_id=10)
        assert result["acceptedAnswerId"] is None
        assert result["topicIds"] == [1]

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.unaccept_answer(question_id=999, user_id=50)

    @pytest.mark.anyio
    async def test_not_owner_forbidden(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        with pytest.raises(ForbiddenError, match="Only the question owner"):
            await svc.unaccept_answer(question_id=10, user_id=77)

    @pytest.mark.anyio
    async def test_unaccept_repo_returns_none(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)
        repo.unaccept_answer.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.unaccept_answer(question_id=10, user_id=50)


# ---------------------------------------------------------------------------
# update_question
# ---------------------------------------------------------------------------


class TestUpdateQuestion:
    @pytest.mark.anyio
    async def test_success_with_topics(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        original = _question(id=10, created_by_id=50)
        repo.get_by_id.return_value = original
        updated = _question(id=10, title="Updated title", content="Updated body")
        repo.update_question.return_value = updated
        topic_repo.list_topic_ids.return_value = {10: [3, 4]}

        result = await svc.update_question(
            question_id=10,
            user_id=50,
            title="Updated title",
            content="Updated body",
            topic_ids=[3, 4],
        )

        repo.update_question.assert_awaited_once_with(
            original, title="Updated title", content="Updated body", type_=None
        )
        topic_repo.replace_topics.assert_awaited_once_with(
            question_id=10, topic_ids=[3, 4], user_id=50
        )
        assert result["topicIds"] == [3, 4]

    @pytest.mark.anyio
    async def test_success_without_topics(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        original = _question(id=10, created_by_id=50)
        repo.get_by_id.return_value = original
        repo.update_question.return_value = original
        topic_repo.list_topic_ids.return_value = {10: [1]}

        result = await svc.update_question(
            question_id=10, user_id=50, title="New title", topic_ids=None
        )

        topic_repo.replace_topics.assert_not_awaited()
        assert result["topicIds"] == [1]

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.update_question(question_id=999, user_id=50, title="X")

    @pytest.mark.anyio
    async def test_not_owner_forbidden(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        with pytest.raises(ForbiddenError, match="Only the question owner can update"):
            await svc.update_question(question_id=10, user_id=77, title="Hack")


# ---------------------------------------------------------------------------
# delete_question
# ---------------------------------------------------------------------------


class TestDeleteQuestion:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        q = _question(id=10, created_by_id=50)
        repo.get_by_id.return_value = q

        await svc.delete_question(question_id=10, user_id=50)

        repo.soft_delete.assert_awaited_once_with(q)

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.delete_question(question_id=999, user_id=50)

    @pytest.mark.anyio
    async def test_not_owner_forbidden(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        with pytest.raises(ForbiddenError, match="Only the question owner can delete"):
            await svc.delete_question(question_id=10, user_id=77)


# ---------------------------------------------------------------------------
# set_bounty
# ---------------------------------------------------------------------------


class TestSetBounty:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        q = _question(id=10, created_by_id=50, bounty=5)
        repo.get_by_id.return_value = q
        updated = _question(id=10, bounty=10)
        repo.set_bounty.return_value = updated
        topic_repo.list_topic_ids.return_value = {10: [1]}

        result = await svc.set_bounty(question_id=10, user_id=50, bounty=10)

        repo.set_bounty.assert_awaited_once_with(q, 10)
        assert result["bounty"] == 10
        assert result["topicIds"] == [1]

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.set_bounty(question_id=999, user_id=50, bounty=5)

    @pytest.mark.anyio
    async def test_not_owner_forbidden(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        with pytest.raises(ForbiddenError, match="Only the question owner can set bounty"):
            await svc.set_bounty(question_id=10, user_id=77, bounty=5)

    @pytest.mark.anyio
    async def test_bounty_negative(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50, bounty=0)

        with pytest.raises(BadRequestError, match="Bounty must be between 0 and 20"):
            await svc.set_bounty(question_id=10, user_id=50, bounty=-1)

    @pytest.mark.anyio
    async def test_bounty_exceeds_max(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50, bounty=0)

        with pytest.raises(BadRequestError, match="Bounty must be between 0 and 20"):
            await svc.set_bounty(question_id=10, user_id=50, bounty=21)

    @pytest.mark.anyio
    async def test_bounty_not_higher_than_current(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50, bounty=10)

        with pytest.raises(BadRequestError, match="New bounty must be higher"):
            await svc.set_bounty(question_id=10, user_id=50, bounty=10)

    @pytest.mark.anyio
    async def test_bounty_lower_than_current(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10, created_by_id=50, bounty=10)

        with pytest.raises(BadRequestError, match="New bounty must be higher"):
            await svc.set_bounty(question_id=10, user_id=50, bounty=5)

    @pytest.mark.anyio
    async def test_bounty_from_none(self):
        """When current bounty is None it defaults to 0, so any positive value works."""
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        q = _question(id=10, created_by_id=50, bounty=None)
        repo.get_by_id.return_value = q
        updated = _question(id=10, bounty=5)
        repo.set_bounty.return_value = updated
        topic_repo.list_topic_ids.return_value = {10: []}

        result = await svc.set_bounty(question_id=10, user_id=50, bounty=5)

        assert result["bounty"] == 5


# ---------------------------------------------------------------------------
# get_trending_questions
# ---------------------------------------------------------------------------


class TestGetTrendingQuestions:
    @pytest.mark.anyio
    async def test_basic(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        questions = [_question(id=1), _question(id=2)]
        repo.get_trending_questions.return_value = questions
        topic_repo.list_topic_ids.return_value = {1: [10], 2: []}

        result = await svc.get_trending_questions(limit=5, days=7)

        repo.get_trending_questions.assert_awaited_once_with(limit=5, days=7)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["content"] is None  # include_content=False
        assert result[0]["topicIds"] == [10]
        assert result[1]["topicIds"] == []

    @pytest.mark.anyio
    async def test_empty(self):
        svc, repo, topic_repo, _a_repo, _p_repo = _make_service()
        repo.get_trending_questions.return_value = []
        topic_repo.list_topic_ids.return_value = {}

        result = await svc.get_trending_questions()

        assert result == []


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        stats = {"totalQuestions": 100, "answeredQuestions": 50}
        repo.get_stats.return_value = stats

        result = await svc.get_stats()

        repo.get_stats.assert_awaited_once()
        assert result == stats


# ---------------------------------------------------------------------------
# get_popular_search_terms
# ---------------------------------------------------------------------------


class TestGetPopularSearchTerms:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        terms = [{"keyword": "python", "count": 42}]
        repo.get_popular_search_terms.return_value = terms

        result = await svc.get_popular_search_terms(limit=5, days=14)

        repo.get_popular_search_terms.assert_awaited_once_with(limit=5, days=14)
        assert result == terms


# ---------------------------------------------------------------------------
# list_followers
# ---------------------------------------------------------------------------


class TestListFollowers:
    @pytest.mark.anyio
    async def test_basic(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.list_followers.return_value = ([42, 43], 5)

        follower_ids, page = await svc.list_followers(question_id=10, page_size=2, page_start=0)

        assert follower_ids == [42, 43]
        assert page["pageStart"] == 0
        assert page["pageSize"] == 2
        assert page["hasMore"] is True
        assert page["nextStart"] == 2
        assert page["total"] == 5

    @pytest.mark.anyio
    async def test_last_page(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.list_followers.return_value = ([45], 5)

        follower_ids, page = await svc.list_followers(question_id=10, page_size=10, page_start=4)

        assert page["hasMore"] is False
        assert page["nextStart"] is None

    @pytest.mark.anyio
    async def test_empty(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = _question(id=10)
        repo.list_followers.return_value = ([], 0)

        follower_ids, page = await svc.list_followers(question_id=10, page_size=10, page_start=None)

        assert follower_ids == []
        assert page["hasMore"] is False

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, repo, _t_repo, _a_repo, _p_repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.list_followers(question_id=999, page_size=10, page_start=None)


# ===========================================================================
# QuestionInvitationService tests
# ===========================================================================


# ---------------------------------------------------------------------------
# create_invitation
# ---------------------------------------------------------------------------


class TestCreateInvitation:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, q_repo, p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = _question(id=10)
        p_repo.get_profile_by_user_id.return_value = _profile(user_id=99, nickname="bob")
        repo._get_invitation.return_value = None
        inv = _invitation(id=7, question_id=10, user_id=99)
        repo.create_invitation.return_value = inv

        result = await svc.create_invitation(question_id=10, inviter_id=50, invitee_id=99)

        repo.create_invitation.assert_awaited_once_with(question_id=10, user_id=99)
        assert result["invitation_id"] == 7
        assert result["invitation"]["id"] == 7
        assert result["invitation"]["user"]["nickname"] == "bob"

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, _repo, q_repo, _p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.create_invitation(question_id=999, inviter_id=50, invitee_id=99)

    @pytest.mark.anyio
    async def test_self_invite_rejected(self):
        svc, _repo, q_repo, _p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = _question(id=10)

        with pytest.raises(BadRequestError, match="Cannot invite yourself"):
            await svc.create_invitation(question_id=10, inviter_id=50, invitee_id=50)

    @pytest.mark.anyio
    async def test_invitee_not_found(self):
        svc, _repo, q_repo, p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = _question(id=10)
        p_repo.get_profile_by_user_id.return_value = None

        with pytest.raises(NotFoundError, match="User not found"):
            await svc.create_invitation(question_id=10, inviter_id=50, invitee_id=99)

    @pytest.mark.anyio
    async def test_duplicate_invitation(self):
        svc, repo, q_repo, p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = _question(id=10)
        p_repo.get_profile_by_user_id.return_value = _profile(user_id=99)
        repo._get_invitation.return_value = _invitation()  # already exists

        with pytest.raises(BadRequestError, match="User already invited"):
            await svc.create_invitation(question_id=10, inviter_id=50, invitee_id=99)


# ---------------------------------------------------------------------------
# get_invitation
# ---------------------------------------------------------------------------


class TestGetInvitation:
    @pytest.mark.anyio
    async def test_success_with_answer_repo(self):
        svc, repo, _q_repo, p_repo, a_repo = _make_invitation_service()
        inv = _invitation(id=3, user_id=99, question_id=10)
        repo.get_by_id.return_value = inv
        p_repo.get_profile_by_user_id.return_value = _profile(user_id=99, nickname="bob")
        a_repo.has_user_answered_question.return_value = True

        result = await svc.get_invitation(invitation_id=3)

        assert result["id"] == 3
        assert result["user"]["nickname"] == "bob"
        assert result["is_answered"] is True

    @pytest.mark.anyio
    async def test_success_no_profile(self):
        svc, repo, _q_repo, p_repo, a_repo = _make_invitation_service()
        inv = _invitation(id=3, user_id=99)
        repo.get_by_id.return_value = inv
        p_repo.get_profile_by_user_id.return_value = None
        a_repo.has_user_answered_question.return_value = False

        result = await svc.get_invitation(invitation_id=3)

        assert result["user"] is None
        assert result["is_answered"] is False

    @pytest.mark.anyio
    async def test_not_found(self):
        svc, repo, _q_repo, _p_repo, _a_repo = _make_invitation_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Invitation not found"):
            await svc.get_invitation(invitation_id=999)

    @pytest.mark.anyio
    async def test_without_answer_repo(self):
        repo = AsyncMock()
        q_repo = AsyncMock()
        p_repo = AsyncMock()
        svc = QuestionInvitationService(
            repo=repo, question_repo=q_repo, profile_repo=p_repo, answer_repo=None
        )
        inv = _invitation(id=3, user_id=99)
        repo.get_by_id.return_value = inv
        p_repo.get_profile_by_user_id.return_value = _profile(user_id=99)

        result = await svc.get_invitation(invitation_id=3)

        # is_answered stays at default False when no answer_repo
        assert result["is_answered"] is False


# ---------------------------------------------------------------------------
# delete_invitation
# ---------------------------------------------------------------------------


class TestDeleteInvitation:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo, q_repo, _p_repo, _a_repo = _make_invitation_service()
        inv = _invitation(id=3, question_id=10)
        repo.get_by_id.return_value = inv
        q_repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        await svc.delete_invitation(invitation_id=3, user_id=50)

        repo.hard_delete.assert_awaited_once_with(inv)

    @pytest.mark.anyio
    async def test_invitation_not_found(self):
        svc, repo, _q_repo, _p_repo, _a_repo = _make_invitation_service()
        repo.get_by_id.return_value = None

        with pytest.raises(BadRequestError, match="Invitation not found"):
            await svc.delete_invitation(invitation_id=999, user_id=50)

    @pytest.mark.anyio
    async def test_not_question_owner(self):
        svc, repo, q_repo, _p_repo, _a_repo = _make_invitation_service()
        inv = _invitation(id=3, question_id=10)
        repo.get_by_id.return_value = inv
        q_repo.get_by_id.return_value = _question(id=10, created_by_id=50)

        with pytest.raises(ForbiddenError, match="Only the question owner"):
            await svc.delete_invitation(invitation_id=3, user_id=77)

    @pytest.mark.anyio
    async def test_question_deleted_forbidden(self):
        svc, repo, q_repo, _p_repo, _a_repo = _make_invitation_service()
        inv = _invitation(id=3, question_id=10)
        repo.get_by_id.return_value = inv
        q_repo.get_by_id.return_value = None  # question was deleted

        with pytest.raises(ForbiddenError, match="Only the question owner"):
            await svc.delete_invitation(invitation_id=3, user_id=50)


# ---------------------------------------------------------------------------
# list_invitations
# ---------------------------------------------------------------------------


class TestListInvitations:
    @pytest.mark.anyio
    async def test_basic(self):
        svc, repo, q_repo, p_repo, a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = _question(id=10)
        inv1 = _invitation(id=1, user_id=99)
        inv2 = _invitation(id=2, user_id=100)
        repo.list_invitations.return_value = ([inv1, inv2], 5)
        p_repo.get_profiles_by_user_ids.return_value = {
            99: _profile(user_id=99, nickname="alice"),
            100: _profile(user_id=100, nickname="bob"),
        }
        a_repo.get_answerer_user_ids.return_value = {99}

        items, page = await svc.list_invitations(question_id=10, page_start=0, page_size=2)

        assert len(items) == 2
        assert items[0]["user"]["nickname"] == "alice"
        assert items[0]["is_answered"] is True
        assert items[1]["user"]["nickname"] == "bob"
        assert items[1]["is_answered"] is False
        assert page["pageStart"] == 0
        assert page["pageSize"] == 2
        assert page["hasMore"] is True
        assert page["total"] == 5

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, _repo, q_repo, _p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.list_invitations(question_id=999, page_start=0, page_size=10)

    @pytest.mark.anyio
    async def test_empty(self):
        svc, repo, q_repo, p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = _question(id=10)
        repo.list_invitations.return_value = ([], 0)
        p_repo.get_profiles_by_user_ids.return_value = {}

        items, page = await svc.list_invitations(question_id=10, page_start=None, page_size=10)

        assert items == []
        assert page["hasMore"] is False
        assert page["total"] == 0


# ---------------------------------------------------------------------------
# get_recommendations
# ---------------------------------------------------------------------------


class TestGetRecommendations:
    @pytest.mark.anyio
    async def test_basic(self):
        svc, _repo, q_repo, p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = _question(id=10)
        profiles = [
            _profile(user_id=1, nickname="a"),
            _profile(user_id=2, nickname="b"),
        ]
        p_repo.list_profiles.return_value = profiles

        result = await svc.get_recommendations(question_id=10, limit=5)

        p_repo.list_profiles.assert_awaited_once_with(limit=5, offset=0)
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["nickname"] == "b"

    @pytest.mark.anyio
    async def test_question_not_found(self):
        svc, _repo, q_repo, _p_repo, _a_repo = _make_invitation_service()
        q_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Question not found"):
            await svc.get_recommendations(question_id=999, limit=5)
