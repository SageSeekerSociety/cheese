from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.discussion.reaction_services import DiscussionReactionService
from app.domain.discussion.services import DiscussionService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC).replace(tzinfo=None)


def _make_entity(**overrides) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "model_type": "PROJECT",
        "model_id": 10,
        "parent_id": None,
        "sender_id": 99,
        "content": {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}],
        },
        "mentioned_user_ids": [],
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_reaction_type(**overrides) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "code": "LIKE",
        "name": "Like",
        "description": "thumbs up",
        "display_order": 0,
        "is_active": True,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_profile(user_id: int, **overrides) -> SimpleNamespace:
    defaults = {
        "user_id": user_id,
        "nickname": f"user_{user_id}",
        "avatar_id": user_id * 100,
        "intro": f"Hello from user {user_id}",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_reaction_service(
    reaction_repo: AsyncMock | None = None,
    reaction_type_repo: AsyncMock | None = None,
) -> tuple[DiscussionReactionService, AsyncMock, AsyncMock]:
    reaction_repo = reaction_repo or AsyncMock()
    reaction_type_repo = reaction_type_repo or AsyncMock()
    reaction_type_repo.ensure_defaults = AsyncMock()
    svc = DiscussionReactionService(
        reaction_repo=reaction_repo,
        reaction_type_repo=reaction_type_repo,
    )
    return svc, reaction_repo, reaction_type_repo


def _make_discussion_service(
    repo: AsyncMock | None = None,
    reaction_service: DiscussionReactionService | AsyncMock | None = None,
    profile_repo: AsyncMock | None = None,
    session: AsyncMock | None = None,
) -> tuple[DiscussionService, AsyncMock, AsyncMock, AsyncMock]:
    repo = repo or AsyncMock()
    reaction_service = reaction_service or AsyncMock()
    profile_repo = profile_repo or AsyncMock()
    session = session or AsyncMock()
    svc = DiscussionService(
        repo=repo,
        reaction_service=reaction_service,
        profile_repo=profile_repo,
        session=session,
    )
    return svc, repo, reaction_service, profile_repo


# ===========================================================================
# DiscussionReactionService
# ===========================================================================


class TestReactionServiceEnsureDefaults:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        svc, _rr, rt_repo = _make_reaction_service()

        await svc.ensure_default_reaction_types()

        rt_repo.ensure_defaults.assert_awaited_once()


# ---------------------------------------------------------------------------
# toggle
# ---------------------------------------------------------------------------


class TestReactionServiceToggle:
    @pytest.mark.anyio
    async def test_toggle_on_returns_active_true(self):
        svc, r_repo, rt_repo = _make_reaction_service()
        rt_repo.get_by_id.return_value = _make_reaction_type(id=1)
        reaction_entity = SimpleNamespace(id=42)
        r_repo.toggle.return_value = reaction_entity
        r_repo.count_by_discussion.return_value = {1: 3}
        r_repo.has_user_reacted.return_value = True
        rt_repo.list_active.return_value = [_make_reaction_type(id=1)]

        result = await svc.toggle(discussion_id=10, user_id=5, reaction_type_id=1)

        assert result["active"] is True
        assert isinstance(result["summary"], list)
        r_repo.toggle.assert_awaited_once_with(
            discussion_id=10,
            user_id=5,
            reaction_type_id=1,
        )

    @pytest.mark.anyio
    async def test_toggle_off_returns_active_false(self):
        svc, r_repo, rt_repo = _make_reaction_service()
        rt_repo.get_by_id.return_value = _make_reaction_type(id=1)
        r_repo.toggle.return_value = None  # toggled off
        r_repo.count_by_discussion.return_value = {}
        rt_repo.list_active.return_value = [_make_reaction_type(id=1)]

        result = await svc.toggle(discussion_id=10, user_id=5, reaction_type_id=1)

        assert result["active"] is False

    @pytest.mark.anyio
    async def test_toggle_unknown_type_raises_not_found(self):
        svc, _r_repo, rt_repo = _make_reaction_service()
        rt_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.toggle(discussion_id=10, user_id=5, reaction_type_id=999)


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestReactionServiceRemove:
    @pytest.mark.anyio
    async def test_remove_existing_reaction(self):
        svc, r_repo, rt_repo = _make_reaction_service()
        rt_repo.get_by_id.return_value = _make_reaction_type(id=1)
        r_repo.remove.return_value = True
        r_repo.count_by_discussion.return_value = {}
        rt_repo.list_active.return_value = [_make_reaction_type(id=1)]

        result = await svc.remove(discussion_id=10, user_id=5, reaction_type_id=1)

        assert result["removed"] is True
        assert isinstance(result["summary"], list)
        r_repo.remove.assert_awaited_once_with(
            discussion_id=10,
            user_id=5,
            reaction_type_id=1,
        )

    @pytest.mark.anyio
    async def test_remove_nonexistent_reaction_returns_false(self):
        svc, r_repo, rt_repo = _make_reaction_service()
        rt_repo.get_by_id.return_value = _make_reaction_type(id=1)
        r_repo.remove.return_value = False
        r_repo.count_by_discussion.return_value = {}
        rt_repo.list_active.return_value = [_make_reaction_type(id=1)]

        result = await svc.remove(discussion_id=10, user_id=5, reaction_type_id=1)

        assert result["removed"] is False

    @pytest.mark.anyio
    async def test_remove_unknown_type_raises_not_found(self):
        svc, _r_repo, rt_repo = _make_reaction_service()
        rt_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.remove(discussion_id=10, user_id=5, reaction_type_id=999)


# ---------------------------------------------------------------------------
# get_reaction_summary
# ---------------------------------------------------------------------------


class TestReactionServiceGetSummary:
    @pytest.mark.anyio
    async def test_builds_summary_for_active_types(self):
        svc, r_repo, rt_repo = _make_reaction_service()
        like = _make_reaction_type(id=1, code="LIKE", name="Like", description="thumbs up")
        cheers = _make_reaction_type(id=2, code="CHEERS", name="Cheers", description="celebration")
        rt_repo.list_active.return_value = [like, cheers]
        r_repo.count_by_discussion.return_value = {1: 5, 2: 0}
        r_repo.has_user_reacted.return_value = True

        result = await svc.get_reaction_summary(discussion_id=10, current_user_id=7)

        assert len(result) == 2
        like_entry = result[0]
        assert like_entry["reactionTypeId"] == 1
        assert like_entry["code"] == "LIKE"
        assert like_entry["count"] == 5
        assert like_entry["reacted"] is True

        cheers_entry = result[1]
        assert cheers_entry["reactionTypeId"] == 2
        assert cheers_entry["count"] == 0
        assert cheers_entry["reacted"] is False  # count=0, no check needed

    @pytest.mark.anyio
    async def test_summary_without_user_sets_reacted_false(self):
        svc, r_repo, rt_repo = _make_reaction_service()
        like = _make_reaction_type(id=1)
        rt_repo.list_active.return_value = [like]
        r_repo.count_by_discussion.return_value = {1: 3}

        result = await svc.get_reaction_summary(discussion_id=10, current_user_id=None)

        assert result[0]["reacted"] is False
        r_repo.has_user_reacted.assert_not_awaited()

    @pytest.mark.anyio
    async def test_summary_user_not_reacted(self):
        svc, r_repo, rt_repo = _make_reaction_service()
        like = _make_reaction_type(id=1)
        rt_repo.list_active.return_value = [like]
        r_repo.count_by_discussion.return_value = {1: 2}
        r_repo.has_user_reacted.return_value = False

        result = await svc.get_reaction_summary(discussion_id=10, current_user_id=7)

        assert result[0]["reacted"] is False

    @pytest.mark.anyio
    async def test_summary_empty_when_no_active_types(self):
        svc, r_repo, rt_repo = _make_reaction_service()
        rt_repo.list_active.return_value = []
        r_repo.count_by_discussion.return_value = {}

        result = await svc.get_reaction_summary(discussion_id=10, current_user_id=7)

        assert result == []


# ---------------------------------------------------------------------------
# list_reaction_types
# ---------------------------------------------------------------------------


class TestReactionServiceListTypes:
    @pytest.mark.anyio
    async def test_returns_formatted_list(self):
        svc, _r_repo, rt_repo = _make_reaction_service()
        like = _make_reaction_type(
            id=1, code="LIKE", name="Like", description="thumbs up", display_order=0
        )
        cheers = _make_reaction_type(
            id=2, code="CHEERS", name="Cheers", description="celebration", display_order=1
        )
        rt_repo.list_active.return_value = [like, cheers]

        result = await svc.list_reaction_types()

        assert len(result) == 2
        assert result[0] == {
            "id": 1,
            "code": "LIKE",
            "name": "Like",
            "description": "thumbs up",
            "displayOrder": 0,
        }
        assert result[1] == {
            "id": 2,
            "code": "CHEERS",
            "name": "Cheers",
            "description": "celebration",
            "displayOrder": 1,
        }

    @pytest.mark.anyio
    async def test_empty_list(self):
        svc, _r_repo, rt_repo = _make_reaction_service()
        rt_repo.list_active.return_value = []

        result = await svc.list_reaction_types()

        assert result == []


# ---------------------------------------------------------------------------
# _reaction_type_to_dict (static method)
# ---------------------------------------------------------------------------


class TestReactionTypeToDict:
    def test_converts_all_fields(self):
        rt = _make_reaction_type(
            id=3, code="HEART", name="Heart", description="love", display_order=5
        )

        result = DiscussionReactionService._reaction_type_to_dict(rt)

        assert result == {
            "id": 3,
            "code": "HEART",
            "name": "Heart",
            "description": "love",
            "displayOrder": 5,
        }

    def test_none_description(self):
        rt = _make_reaction_type(description=None)

        result = DiscussionReactionService._reaction_type_to_dict(rt)

        assert result["description"] is None


# ===========================================================================
# DiscussionService
# ===========================================================================


# ---------------------------------------------------------------------------
# create_discussion
# ---------------------------------------------------------------------------


class TestCreateDiscussion:
    @pytest.mark.anyio
    @patch("app.domain.discussion.services.publish_notification_event", new_callable=AsyncMock)
    async def test_creates_and_returns_dto(self, mock_publish):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=42, sender_id=5, mentioned_user_ids=[])
        repo.create.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile = _make_profile(5)
        profile_repo.get_profiles_by_user_ids.return_value = {5: profile}

        result = await svc.create_discussion(
            user_id=5,
            content="hello world",
            model_type="PROJECT",
            model_id=10,
        )

        assert result["id"] == 42
        assert result["sender"]["id"] == 5
        assert result["sender"]["nickname"] == "user_5"
        repo.create.assert_awaited_once()
        mock_publish.assert_not_awaited()

    @pytest.mark.anyio
    @patch("app.domain.discussion.services.publish_notification_event", new_callable=AsyncMock)
    async def test_publishes_notification_for_mentions(self, mock_publish):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(
            id=42, sender_id=5, mentioned_user_ids=[10, 20], content="hey @user10 @user20"
        )
        repo.create.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {
            5: _make_profile(5),
            10: _make_profile(10),
            20: _make_profile(20),
        }

        await svc.create_discussion(
            user_id=5,
            content="hey @user10 @user20",
            model_type="PROJECT",
            model_id=10,
            mentioned_user_ids=[10, 20],
        )

        mock_publish.assert_awaited_once()
        call_kwargs = mock_publish.call_args.kwargs
        assert call_kwargs["recipient_ids"] == {10, 20}
        assert call_kwargs["actor_id"] == 5

    @pytest.mark.anyio
    async def test_empty_content_raises_bad_request(self):
        svc, _repo, _rxn, _pr = _make_discussion_service()

        with pytest.raises(BadRequestError, match="content is required"):
            await svc.create_discussion(
                user_id=5,
                content="",
                model_type="PROJECT",
                model_id=10,
            )

    @pytest.mark.anyio
    async def test_whitespace_only_content_raises_bad_request(self):
        svc, _repo, _rxn, _pr = _make_discussion_service()

        with pytest.raises(BadRequestError, match="content is required"):
            await svc.create_discussion(
                user_id=5,
                content="   ",
                model_type="PROJECT",
                model_id=10,
            )

    @pytest.mark.anyio
    async def test_invalid_model_type_raises_bad_request(self):
        svc, _repo, _rxn, _pr = _make_discussion_service()

        with pytest.raises(BadRequestError, match="Invalid modelType"):
            await svc.create_discussion(
                user_id=5,
                content="hello",
                model_type="BOGUS",
                model_id=10,
            )

    @pytest.mark.anyio
    @patch("app.domain.discussion.services.publish_notification_event", new_callable=AsyncMock)
    async def test_filters_non_positive_mention_ids(self, mock_publish):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=1, sender_id=5, mentioned_user_ids=[10], content="hello")
        repo.create.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {
            5: _make_profile(5),
            10: _make_profile(10),
        }

        await svc.create_discussion(
            user_id=5,
            content="hello",
            model_type="PROJECT",
            model_id=10,
            mentioned_user_ids=[10, 0, -1],
        )

        create_kwargs = repo.create.call_args.kwargs
        assert create_kwargs["mentioned_user_ids"] == [10]

    @pytest.mark.anyio
    @patch("app.domain.discussion.services.publish_notification_event", new_callable=AsyncMock)
    async def test_deduplicates_mention_ids(self, mock_publish):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=1, sender_id=5, mentioned_user_ids=[10], content="hello")
        repo.create.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {
            5: _make_profile(5),
            10: _make_profile(10),
        }

        await svc.create_discussion(
            user_id=5,
            content="hello",
            model_type="PROJECT",
            model_id=10,
            mentioned_user_ids=[10, 10, 10],
        )

        create_kwargs = repo.create.call_args.kwargs
        assert create_kwargs["mentioned_user_ids"] == [10]

    @pytest.mark.anyio
    @patch("app.domain.discussion.services.publish_notification_event", new_callable=AsyncMock)
    async def test_model_type_uppercased(self, mock_publish):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=1, sender_id=5, mentioned_user_ids=[])
        repo.create.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        await svc.create_discussion(
            user_id=5,
            content="hello",
            model_type="project",
            model_id=10,
        )

        create_kwargs = repo.create.call_args.kwargs
        assert create_kwargs["model_type"] == "PROJECT"

    @pytest.mark.anyio
    @patch("app.domain.discussion.services.publish_notification_event", new_callable=AsyncMock)
    async def test_content_is_stripped(self, mock_publish):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=1, sender_id=5, mentioned_user_ids=[])
        repo.create.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        await svc.create_discussion(
            user_id=5,
            content="  hello  ",
            model_type="PROJECT",
            model_id=10,
        )

        create_kwargs = repo.create.call_args.kwargs
        assert create_kwargs["content"] == "hello"


# ---------------------------------------------------------------------------
# get_discussion
# ---------------------------------------------------------------------------


class TestGetDiscussion:
    @pytest.mark.anyio
    async def test_returns_dto(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=8, sender_id=5)
        repo.get_by_id.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        result = await svc.get_discussion(8, current_user_id=5)

        assert result["id"] == 8
        assert result["sender"]["id"] == 5
        repo.get_by_id.assert_awaited_once_with(8)

    @pytest.mark.anyio
    async def test_not_found_raises(self):
        svc, repo, _rxn, _pr = _make_discussion_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.get_discussion(999, current_user_id=5)

    @pytest.mark.anyio
    async def test_includes_sub_discussions(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=1, sender_id=5)
        repo.get_by_id.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 3
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        result = await svc.get_discussion(1, current_user_id=5)

        assert result["subDiscussions"] is not None
        assert result["subDiscussions"]["count"] == 3
        assert result["subDiscussions"]["examples"] == []


# ---------------------------------------------------------------------------
# list_discussions
# ---------------------------------------------------------------------------


class TestListDiscussions:
    @pytest.mark.anyio
    async def test_returns_items_and_page_metadata(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entities = [_make_entity(id=i, sender_id=5) for i in range(3)]
        repo.find_all.return_value = (entities, 3)
        rxn_svc.get_reaction_summary.return_value = []
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        items, page = await svc.list_discussions(
            model_type="PROJECT",
            model_id=10,
            parent_id=None,
            page_start=0,
            page_size=10,
            sort_by="createdAt",
            sort_order="desc",
            current_user_id=5,
            include_subs=False,
            with_reactions=False,
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
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entities = [_make_entity(id=i, sender_id=5) for i in range(2)]
        repo.find_all.return_value = (entities, 5)
        rxn_svc.get_reaction_summary.return_value = []
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        _, page = await svc.list_discussions(
            model_type="PROJECT",
            model_id=10,
            parent_id=None,
            page_start=0,
            page_size=2,
            sort_by="createdAt",
            sort_order="desc",
            current_user_id=5,
            include_subs=False,
            with_reactions=False,
        )

        assert page["hasMore"] is True
        assert page["nextStart"] == 2

    @pytest.mark.anyio
    async def test_page_start_none_defaults_to_zero(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        repo.find_all.return_value = ([], 0)

        _, page = await svc.list_discussions(
            model_type="PROJECT",
            model_id=10,
            parent_id=None,
            page_start=None,
            page_size=10,
            sort_by="createdAt",
            sort_order="desc",
            current_user_id=5,
            include_subs=False,
            with_reactions=False,
        )

        assert page["pageStart"] == 0

    @pytest.mark.anyio
    async def test_empty_result(self):
        svc, repo, _rxn, _pr = _make_discussion_service()
        repo.find_all.return_value = ([], 0)

        items, page = await svc.list_discussions(
            model_type="PROJECT",
            model_id=10,
            parent_id=None,
            page_start=0,
            page_size=10,
            sort_by="createdAt",
            sort_order="desc",
            current_user_id=5,
            include_subs=False,
            with_reactions=False,
        )

        assert items == []
        assert page["total"] == 0
        assert page["hasMore"] is False

    @pytest.mark.anyio
    async def test_last_page_has_more_false(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entities = [_make_entity(id=3, sender_id=5)]
        repo.find_all.return_value = (entities, 4)
        rxn_svc.get_reaction_summary.return_value = []
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        _, page = await svc.list_discussions(
            model_type="PROJECT",
            model_id=10,
            parent_id=None,
            page_start=3,
            page_size=10,
            sort_by="createdAt",
            sort_order="desc",
            current_user_id=5,
            include_subs=False,
            with_reactions=False,
        )

        assert page["hasMore"] is False
        assert page["nextStart"] is None


# ---------------------------------------------------------------------------
# update_discussion
# ---------------------------------------------------------------------------


class TestUpdateDiscussion:
    @pytest.mark.anyio
    async def test_updates_and_returns_dto(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=5, sender_id=7)
        repo.get_by_id.return_value = entity
        updated_entity = _make_entity(
            id=5,
            sender_id=7,
            content={
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "new"}]}],
            },
        )
        repo.update_content.return_value = updated_entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {7: _make_profile(7)}

        result = await svc.update_discussion(5, content="new", user_id=7)

        assert result["id"] == 5
        repo.update_content.assert_awaited_once()
        call_args = repo.update_content.call_args
        assert call_args[0][0] == 5
        content_json = call_args[0][1]
        assert content_json["type"] == "doc"
        assert content_json["content"][0]["content"][0]["text"] == "new"

    @pytest.mark.anyio
    async def test_not_found_raises(self):
        svc, repo, _rxn, _pr = _make_discussion_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.update_discussion(999, content="x", user_id=7)

    @pytest.mark.anyio
    async def test_wrong_user_raises_forbidden(self):
        svc, repo, _rxn, _pr = _make_discussion_service()
        repo.get_by_id.return_value = _make_entity(id=5, sender_id=7)

        with pytest.raises(ForbiddenError):
            await svc.update_discussion(5, content="x", user_id=999)

        repo.update_content.assert_not_awaited()


# ---------------------------------------------------------------------------
# delete_discussion
# ---------------------------------------------------------------------------


class TestDeleteDiscussion:
    @pytest.mark.anyio
    async def test_soft_deletes(self):
        svc, repo, _rxn, _pr = _make_discussion_service()
        repo.soft_delete.return_value = True

        result = await svc.delete_discussion(3)

        assert result is None
        repo.soft_delete.assert_awaited_once_with(3)

    @pytest.mark.anyio
    async def test_not_found_raises(self):
        svc, repo, _rxn, _pr = _make_discussion_service()
        repo.soft_delete.return_value = False

        with pytest.raises(NotFoundError):
            await svc.delete_discussion(999)


# ---------------------------------------------------------------------------
# get_reaction_summary (delegating method)
# ---------------------------------------------------------------------------


class TestDiscussionServiceGetReactionSummary:
    @pytest.mark.anyio
    async def test_delegates_to_reaction_service(self):
        svc, _repo, rxn_svc, _pr = _make_discussion_service()
        rxn_svc.get_reaction_summary.return_value = [{"reactionTypeId": 1, "count": 3}]

        result = await svc.get_reaction_summary(discussion_id=10, user_id=7)

        assert result == [{"reactionTypeId": 1, "count": 3}]
        rxn_svc.get_reaction_summary.assert_awaited_once_with(
            discussion_id=10,
            current_user_id=7,
        )


# ---------------------------------------------------------------------------
# list_reaction_types (delegating method)
# ---------------------------------------------------------------------------


class TestDiscussionServiceListReactionTypes:
    @pytest.mark.anyio
    async def test_delegates_to_reaction_service(self):
        svc, _repo, rxn_svc, _pr = _make_discussion_service()
        rxn_svc.list_reaction_types.return_value = [{"id": 1, "code": "LIKE"}]

        result = await svc.list_reaction_types()

        assert result == [{"id": 1, "code": "LIKE"}]
        rxn_svc.list_reaction_types.assert_awaited_once()


# ---------------------------------------------------------------------------
# toggle_reaction (delegating method)
# ---------------------------------------------------------------------------


class TestDiscussionServiceToggleReaction:
    @pytest.mark.anyio
    async def test_delegates_to_reaction_service(self):
        svc, _repo, rxn_svc, _pr = _make_discussion_service()
        rxn_svc.toggle.return_value = {"active": True, "summary": []}

        result = await svc.toggle_reaction(discussion_id=10, reaction_type_id=1, user_id=7)

        assert result == {"active": True, "summary": []}
        rxn_svc.toggle.assert_awaited_once_with(
            discussion_id=10,
            user_id=7,
            reaction_type_id=1,
        )


# ---------------------------------------------------------------------------
# remove_reaction (delegating method)
# ---------------------------------------------------------------------------


class TestDiscussionServiceRemoveReaction:
    @pytest.mark.anyio
    async def test_delegates_to_reaction_service(self):
        svc, _repo, rxn_svc, _pr = _make_discussion_service()
        rxn_svc.remove.return_value = {"removed": True, "summary": []}

        result = await svc.remove_reaction(discussion_id=10, reaction_type_id=1, user_id=7)

        assert result == {"removed": True, "summary": []}
        rxn_svc.remove.assert_awaited_once_with(
            discussion_id=10,
            user_id=7,
            reaction_type_id=1,
        )


# ---------------------------------------------------------------------------
# _build_discussion_dto (internal, tested through public methods)
# ---------------------------------------------------------------------------


class TestBuildDiscussionDto:
    @pytest.mark.anyio
    async def test_dto_contains_all_fields(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(
            id=42,
            model_type="TEAM",
            model_id=7,
            parent_id=3,
            sender_id=5,
            mentioned_user_ids=[10],
        )
        repo.get_by_id.return_value = entity
        rxn_svc.get_reaction_summary.return_value = [{"reactionTypeId": 1, "count": 2}]
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 1
        profile_repo.get_profiles_by_user_ids.return_value = {
            5: _make_profile(5),
            10: _make_profile(10),
        }

        result = await svc.get_discussion(42, current_user_id=5)

        assert result["id"] == 42
        assert result["modelType"] == "TEAM"
        assert result["modelId"] == 7
        assert result["parentId"] == 3
        assert result["sender"]["id"] == 5
        assert result["sender"]["nickname"] == "user_5"
        assert result["sender"]["avatarId"] == 500
        assert result["sender"]["intro"] == "Hello from user 5"
        assert len(result["mentionedUsers"]) == 1
        assert result["mentionedUsers"][0]["id"] == 10
        assert result["reactions"] == [{"reactionTypeId": 1, "count": 2}]
        assert result["subDiscussions"]["count"] == 1
        assert isinstance(result["createdAt"], int)
        assert isinstance(result["updatedAt"], int)

    @pytest.mark.anyio
    async def test_timestamps_as_epoch_ms(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        ts = datetime(2025, 6, 15, 12, 0, 0)
        entity = _make_entity(id=1, sender_id=5, created_at=ts, updated_at=ts)
        repo.get_by_id.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        result = await svc.get_discussion(1, current_user_id=5)

        expected_ms = int(ts.timestamp() * 1000)
        assert result["createdAt"] == expected_ms
        assert result["updatedAt"] == expected_ms

    @pytest.mark.anyio
    async def test_without_reactions(self):
        """When with_reactions=False, reactions list is empty."""
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entities = [_make_entity(id=1, sender_id=5)]
        repo.find_all.return_value = (entities, 1)
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        items, _ = await svc.list_discussions(
            model_type="PROJECT",
            model_id=10,
            parent_id=None,
            page_start=0,
            page_size=10,
            sort_by="createdAt",
            sort_order="desc",
            current_user_id=5,
            include_subs=False,
            with_reactions=False,
        )

        assert items[0]["reactions"] == []
        rxn_svc.get_reaction_summary.assert_not_awaited()

    @pytest.mark.anyio
    async def test_without_subs(self):
        """When include_subs=False, subDiscussions is None."""
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entities = [_make_entity(id=1, sender_id=5)]
        repo.find_all.return_value = (entities, 1)
        rxn_svc.get_reaction_summary.return_value = []
        profile_repo.get_profiles_by_user_ids.return_value = {5: _make_profile(5)}

        items, _ = await svc.list_discussions(
            model_type="PROJECT",
            model_id=10,
            parent_id=None,
            page_start=0,
            page_size=10,
            sort_by="createdAt",
            sort_order="desc",
            current_user_id=5,
            include_subs=False,
            with_reactions=True,
        )

        assert items[0]["subDiscussions"] is None

    @pytest.mark.anyio
    async def test_missing_profile_excluded_from_mentioned(self):
        """If a mentioned user has no profile, they are excluded from the list."""
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=1, sender_id=5, mentioned_user_ids=[10, 20])
        repo.get_by_id.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        # Only user 10 has a profile, user 20 does not
        profile_repo.get_profiles_by_user_ids.return_value = {
            5: _make_profile(5),
            10: _make_profile(10),
        }

        result = await svc.get_discussion(1, current_user_id=5)

        assert len(result["mentionedUsers"]) == 1
        assert result["mentionedUsers"][0]["id"] == 10

    @pytest.mark.anyio
    async def test_sender_none_when_no_profile(self):
        """If the sender has no profile, sender is None."""
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=1, sender_id=5)
        repo.get_by_id.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        profile_repo.get_profiles_by_user_ids.return_value = {}

        result = await svc.get_discussion(1, current_user_id=5)

        assert result["sender"] is None


# ---------------------------------------------------------------------------
# _load_user_map (internal, tested through public methods)
# ---------------------------------------------------------------------------


class TestLoadUserMap:
    @pytest.mark.anyio
    async def test_maps_profiles_by_user_id(self):
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entity = _make_entity(id=1, sender_id=5, mentioned_user_ids=[10])
        repo.get_by_id.return_value = entity
        rxn_svc.get_reaction_summary.return_value = []
        repo.find_all.return_value = ([], 0)
        repo.count_children.return_value = 0
        p5 = _make_profile(5, nickname="Alice", avatar_id=50, intro="Hi")
        p10 = _make_profile(10, nickname="Bob", avatar_id=100, intro="Hey")
        profile_repo.get_profiles_by_user_ids.return_value = {5: p5, 10: p10}

        result = await svc.get_discussion(1, current_user_id=5)

        assert result["sender"]["nickname"] == "Alice"
        assert result["mentionedUsers"][0]["nickname"] == "Bob"

    @pytest.mark.anyio
    async def test_empty_user_ids_returns_empty_map(self):
        """When there are no mentioned users and sender has no profile."""
        svc, repo, rxn_svc, profile_repo = _make_discussion_service()
        entities = []
        repo.find_all.return_value = (entities, 0)

        items, _ = await svc.list_discussions(
            model_type="PROJECT",
            model_id=10,
            parent_id=None,
            page_start=0,
            page_size=10,
            sort_by="createdAt",
            sort_order="desc",
            current_user_id=5,
            include_subs=False,
            with_reactions=False,
        )

        assert items == []
        profile_repo.get_profiles_by_user_ids.assert_not_awaited()

    @pytest.mark.anyio
    async def test_empty_user_ids_skips_repo_call(self):
        """_load_user_map returns {} immediately when user_ids is empty (line 271)."""
        svc, _repo, _rxn, profile_repo = _make_discussion_service()

        result = await svc._load_user_map(set())

        assert result == {}
        profile_repo.get_profiles_by_user_ids.assert_not_awaited()
