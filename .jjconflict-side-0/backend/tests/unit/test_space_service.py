"""Unit tests for SpaceService – targeting 100 % coverage of app/domain/space/services.py."""  # noqa: E501

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.space.models import SpaceAdminRole
from app.domain.space.services import SpaceService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_space(**overrides):
    defaults = {
        "id": 1,
        "name": "Test Space",
        "intro": "An intro",
        "description": "A description",
        "avatar_id": None,
        "enable_rank": False,
        "default_category_id": 10,
        "announcements": [],
        "task_templates": [],
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_category(**overrides):
    defaults = {
        "id": 10,
        "space_id": 1,
        "name": "General",
        "description": "Default",
        "display_order": 0,
        "archived_at": None,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_admin_relation(**overrides):
    defaults = {
        "id": 100,
        "space_id": 1,
        "user_id": 42,
        "role": SpaceAdminRole.OWNER.value,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _build_service(
    *,
    repo=None,
    category_repo=None,
    admin_repo=None,
    rank_repo=None,
    task_repo=None,
):
    repo = repo or AsyncMock()
    category_repo = category_repo or AsyncMock()
    return SpaceService(
        repo=repo,
        category_repo=category_repo,
        admin_repo=admin_repo,
        rank_repo=rank_repo,
        task_repo=task_repo,
    )


# ===========================================================================
# Basic queries
# ===========================================================================


class TestGetSpace:
    @pytest.mark.anyio
    async def test_returns_space(self):
        repo = AsyncMock()
        space = _make_space()
        repo.get_by_id.return_value = space

        svc = _build_service(repo=repo)
        result = await svc.get_space(1)

        assert result is space
        repo.get_by_id.assert_awaited_once_with(1)

    @pytest.mark.anyio
    async def test_returns_none_when_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = _build_service(repo=repo)
        result = await svc.get_space(999)

        assert result is None


class TestExistsByName:
    @pytest.mark.anyio
    async def test_returns_true(self):
        repo = AsyncMock()
        repo.exists_by_name.return_value = True

        svc = _build_service(repo=repo)
        assert await svc.exists_by_name("My Space") is True

    @pytest.mark.anyio
    async def test_returns_false(self):
        repo = AsyncMock()
        repo.exists_by_name.return_value = False

        svc = _build_service(repo=repo)
        assert await svc.exists_by_name("Nope") is False


class TestListSpaces:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        repo = AsyncMock()
        spaces = [_make_space(id=1), _make_space(id=2)]
        repo.list_spaces.return_value = spaces

        svc = _build_service(repo=repo)
        result = await svc.list_spaces(limit=20, offset=5)

        assert result == spaces
        repo.list_spaces.assert_awaited_once_with(limit=20, offset=5)


class TestCountSpaces:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        repo = AsyncMock()
        repo.count_spaces.return_value = 42

        svc = _build_service(repo=repo)
        assert await svc.count_spaces() == 42


class TestListCategories:
    @pytest.mark.anyio
    async def test_without_archived(self):
        cat_repo = AsyncMock()
        cats = [_make_category()]
        cat_repo.list_categories_for_space.return_value = cats

        svc = _build_service(category_repo=cat_repo)
        result = await svc.list_categories(1)

        assert result == cats
        cat_repo.list_categories_for_space.assert_awaited_once_with(
            space_id=1, include_archived=False
        )

    @pytest.mark.anyio
    async def test_with_archived(self):
        cat_repo = AsyncMock()
        cat_repo.list_categories_for_space.return_value = []

        svc = _build_service(category_repo=cat_repo)
        await svc.list_categories(1, include_archived=True)

        cat_repo.list_categories_for_space.assert_awaited_once_with(
            space_id=1, include_archived=True
        )


class TestGetCategoryDetail:
    @pytest.mark.anyio
    async def test_returns_category(self):
        cat_repo = AsyncMock()
        cat = _make_category(id=10, space_id=1)
        cat_repo.get_by_id.return_value = cat

        svc = _build_service(category_repo=cat_repo)
        result = await svc.get_category_detail(space_id=1, category_id=10)

        assert result is cat

    @pytest.mark.anyio
    async def test_raises_when_not_found(self):
        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = None

        svc = _build_service(category_repo=cat_repo)

        with pytest.raises(NotFoundError, match="Space category not found"):
            await svc.get_category_detail(space_id=1, category_id=999)

    @pytest.mark.anyio
    async def test_raises_when_wrong_space(self):
        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = _make_category(id=10, space_id=99)

        svc = _build_service(category_repo=cat_repo)

        with pytest.raises(NotFoundError, match="Space category not found"):
            await svc.get_category_detail(space_id=1, category_id=10)


class TestGetUserRank:
    @pytest.mark.anyio
    async def test_returns_none_when_user_id_is_none(self):
        svc = _build_service()
        assert await svc.get_user_rank(1, None) is None

    @pytest.mark.anyio
    async def test_returns_none_when_rank_repo_is_none(self):
        svc = _build_service(rank_repo=None)
        assert await svc.get_user_rank(1, 42) is None

    @pytest.mark.anyio
    async def test_returns_rank(self):
        rank_repo = AsyncMock()
        rank_repo.get_rank.return_value = 5

        svc = _build_service(rank_repo=rank_repo)
        result = await svc.get_user_rank(1, 42)

        assert result == 5
        rank_repo.get_rank.assert_awaited_once_with(space_id=1, user_id=42)


# ===========================================================================
# Space lifecycle
# ===========================================================================


class TestCreateSpace:
    @pytest.mark.anyio
    async def test_happy_path_with_admin_repo(self):
        repo = AsyncMock()
        space = _make_space(id=1)
        repo.create_space.return_value = space
        repo.save.side_effect = lambda s: s

        cat_repo = AsyncMock()
        default_cat = _make_category(id=10)
        cat_repo.create_category.return_value = default_cat

        admin_repo = AsyncMock()

        svc = _build_service(repo=repo, category_repo=cat_repo, admin_repo=admin_repo)
        result = await svc.create_space(
            name="My Space",
            intro="Hello",
            description="A space",
            avatar_id=5,
            enable_rank=True,
            owner_id=42,
            announcements=[{"title": "hi"}],
            task_templates=[],
        )

        assert result is space
        assert result.default_category_id == 10
        repo.create_space.assert_awaited_once()
        cat_repo.create_category.assert_awaited_once_with(
            space_id=1,
            name="General",
            description="Auto generated default category",
            display_order=0,
        )
        repo.save.assert_awaited_once_with(space)
        admin_repo.add_admin.assert_awaited_once_with(
            space_id=1,
            user_id=42,
            role=SpaceAdminRole.OWNER,
        )

    @pytest.mark.anyio
    async def test_happy_path_without_admin_repo(self):
        repo = AsyncMock()
        space = _make_space(id=1)
        repo.create_space.return_value = space
        repo.save.side_effect = lambda s: s

        cat_repo = AsyncMock()
        cat_repo.create_category.return_value = _make_category(id=10)

        svc = _build_service(repo=repo, category_repo=cat_repo, admin_repo=None)
        result = await svc.create_space(
            name="Space",
            intro="intro",
            description="desc",
            avatar_id=None,
            enable_rank=False,
            owner_id=42,
            announcements=[],
            task_templates=[],
        )

        assert result is space

    @pytest.mark.anyio
    async def test_empty_name_raises(self):
        svc = _build_service()

        with pytest.raises(BadRequestError, match="name cannot be empty"):
            await svc.create_space(
                name="   ",
                intro="intro",
                description="desc",
                avatar_id=None,
                enable_rank=False,
                owner_id=42,
                announcements=[],
                task_templates=[],
            )

    @pytest.mark.anyio
    async def test_non_string_name_raises(self):
        svc = _build_service()

        with pytest.raises(BadRequestError, match="name cannot be empty"):
            await svc.create_space(
                name=123,  # type: ignore[arg-type]
                intro="intro",
                description="desc",
                avatar_id=None,
                enable_rank=False,
                owner_id=42,
                announcements=[],
                task_templates=[],
            )

    @pytest.mark.anyio
    async def test_non_string_intro_raises(self):
        svc = _build_service()

        with pytest.raises(
            BadRequestError, match="intro and description must be strings"
        ):
            await svc.create_space(
                name="Name",
                intro=123,  # type: ignore[arg-type]
                description="desc",
                avatar_id=None,
                enable_rank=False,
                owner_id=42,
                announcements=[],
                task_templates=[],
            )

    @pytest.mark.anyio
    async def test_non_string_description_raises(self):
        svc = _build_service()

        with pytest.raises(
            BadRequestError, match="intro and description must be strings"
        ):
            await svc.create_space(
                name="Name",
                intro="intro",
                description=123,  # type: ignore[arg-type]
                avatar_id=None,
                enable_rank=False,
                owner_id=42,
                announcements=[],
                task_templates=[],
            )

    @pytest.mark.anyio
    async def test_normalize_none_announcements(self):
        repo = AsyncMock()
        space = _make_space(id=1)
        repo.create_space.return_value = space
        repo.save.side_effect = lambda s: s

        cat_repo = AsyncMock()
        cat_repo.create_category.return_value = _make_category(id=10)

        svc = _build_service(repo=repo, category_repo=cat_repo)
        await svc.create_space(
            name="Space",
            intro="intro",
            description="desc",
            avatar_id=None,
            enable_rank=False,
            owner_id=42,
            announcements=None,  # type: ignore[arg-type]
            task_templates=None,  # type: ignore[arg-type]
        )

        call_kwargs = repo.create_space.call_args.kwargs
        assert call_kwargs["announcements"] == []
        assert call_kwargs["task_templates"] == []


class TestUpdateSpace:
    @pytest.mark.anyio
    async def test_happy_path_all_fields(self):
        repo = AsyncMock()
        space = _make_space(id=1)
        repo.get_by_id.return_value = space
        repo.save.side_effect = lambda s: s

        cat_repo = AsyncMock()
        cat = _make_category(id=20, space_id=1)
        cat_repo.get_by_id.return_value = cat

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.OWNER.value
        )

        svc = _build_service(repo=repo, category_repo=cat_repo, admin_repo=admin_repo)
        result = await svc.update_space(
            space_id=1,
            actor_user_id=42,
            name="New Name",
            intro="New intro",
            description="New desc",
            avatar_id=99,
            enable_rank=True,
            announcements=[{"a": 1}],
            task_templates=[{"t": 1}],
            default_category_id=20,
        )

        assert result.name == "New Name"
        assert result.intro == "New intro"
        assert result.description == "New desc"
        assert result.avatar_id == 99
        assert result.enable_rank is True
        assert result.announcements == [{"a": 1}]
        assert result.task_templates == [{"t": 1}]
        assert result.default_category_id == 20
        assert result.updated_at is not None

    @pytest.mark.anyio
    async def test_space_not_found_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = _build_service(repo=repo)

        with pytest.raises(NotFoundError, match="Resource space not found"):
            await svc.update_space(space_id=999, actor_user_id=42)

    @pytest.mark.anyio
    async def test_empty_name_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = _make_space()

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.ADMIN.value
        )

        svc = _build_service(repo=repo, admin_repo=admin_repo)

        with pytest.raises(BadRequestError, match="Space name cannot be empty"):
            await svc.update_space(space_id=1, actor_user_id=42, name="   ")

    @pytest.mark.anyio
    async def test_non_string_name_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = _make_space()

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.ADMIN.value
        )

        svc = _build_service(repo=repo, admin_repo=admin_repo)

        with pytest.raises(BadRequestError, match="Space name cannot be empty"):
            await svc.update_space(space_id=1, actor_user_id=42, name=123)  # type: ignore[arg-type]

    @pytest.mark.anyio
    async def test_non_string_intro_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = _make_space()

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.ADMIN.value
        )

        svc = _build_service(repo=repo, admin_repo=admin_repo)

        with pytest.raises(BadRequestError, match="Space intro must be string"):
            await svc.update_space(space_id=1, actor_user_id=42, intro=123)  # type: ignore[arg-type]

    @pytest.mark.anyio
    async def test_non_string_description_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = _make_space()

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.ADMIN.value
        )

        svc = _build_service(repo=repo, admin_repo=admin_repo)

        with pytest.raises(BadRequestError, match="Space description must be string"):
            await svc.update_space(space_id=1, actor_user_id=42, description=123)  # type: ignore[arg-type]

    @pytest.mark.anyio
    async def test_default_category_not_found_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = _make_space()

        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = None

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.OWNER.value
        )

        svc = _build_service(repo=repo, category_repo=cat_repo, admin_repo=admin_repo)

        with pytest.raises(NotFoundError, match="Space category not found"):
            await svc.update_space(
                space_id=1, actor_user_id=42, default_category_id=999
            )

    @pytest.mark.anyio
    async def test_default_category_wrong_space_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = _make_space()

        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = _make_category(id=20, space_id=99)

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.OWNER.value
        )

        svc = _build_service(repo=repo, category_repo=cat_repo, admin_repo=admin_repo)

        with pytest.raises(NotFoundError, match="Space category not found"):
            await svc.update_space(space_id=1, actor_user_id=42, default_category_id=20)

    @pytest.mark.anyio
    async def test_no_fields_updated_still_saves(self):
        """When no optional fields are passed, the space is still saved with an updated timestamp."""  # noqa: E501
        repo = AsyncMock()
        space = _make_space()
        repo.get_by_id.return_value = space
        repo.save.side_effect = lambda s: s

        svc = _build_service(repo=repo, admin_repo=None)
        result = await svc.update_space(space_id=1, actor_user_id=42)

        assert result.updated_at is not None
        repo.save.assert_awaited_once()


# ===========================================================================
# Categories
# ===========================================================================


class TestCreateCategory:
    @pytest.mark.anyio
    async def test_happy_path(self):
        cat_repo = AsyncMock()
        cat_repo.exists_unarchived_name.return_value = False
        new_cat = _make_category(id=20, name="Tasks")
        cat_repo.create_category.return_value = new_cat

        svc = _build_service(category_repo=cat_repo, admin_repo=None)
        result = await svc.create_category(
            space_id=1,
            name="Tasks",
            description="Task category",
            display_order=1,
            actor_user_id=42,
        )

        assert result is new_cat
        cat_repo.create_category.assert_awaited_once()

    @pytest.mark.anyio
    async def test_empty_name_raises(self):
        svc = _build_service(admin_repo=None)

        with pytest.raises(BadRequestError, match="Category name cannot be empty"):
            await svc.create_category(
                space_id=1,
                name="   ",
                description=None,
                display_order=0,
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_duplicate_name_raises(self):
        cat_repo = AsyncMock()
        cat_repo.exists_unarchived_name.return_value = True

        svc = _build_service(category_repo=cat_repo, admin_repo=None)

        with pytest.raises(BadRequestError, match="Category name already exists"):
            await svc.create_category(
                space_id=1,
                name="General",
                description=None,
                display_order=0,
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_description_none_passed_through(self):
        cat_repo = AsyncMock()
        cat_repo.exists_unarchived_name.return_value = False
        cat_repo.create_category.return_value = _make_category()

        svc = _build_service(category_repo=cat_repo, admin_repo=None)
        await svc.create_category(
            space_id=1, name="Cat", description=None, display_order=0, actor_user_id=42
        )

        call_kwargs = cat_repo.create_category.call_args.kwargs
        assert call_kwargs["description"] is None

    @pytest.mark.anyio
    async def test_description_string_stripped(self):
        cat_repo = AsyncMock()
        cat_repo.exists_unarchived_name.return_value = False
        cat_repo.create_category.return_value = _make_category()

        svc = _build_service(category_repo=cat_repo, admin_repo=None)
        await svc.create_category(
            space_id=1,
            name="Cat",
            description="  padded  ",
            display_order=0,
            actor_user_id=42,
        )

        call_kwargs = cat_repo.create_category.call_args.kwargs
        assert call_kwargs["description"] == "padded"


class TestUpdateCategory:
    @pytest.mark.anyio
    async def test_happy_path_all_fields(self):
        cat_repo = AsyncMock()
        cat = _make_category(id=10, space_id=1, name="Old")
        cat_repo.get_by_id.return_value = cat
        cat_repo.exists_unarchived_name.return_value = False
        cat_repo.save.side_effect = lambda c: c

        svc = _build_service(category_repo=cat_repo, admin_repo=None)
        result = await svc.update_category(
            space_id=1,
            category_id=10,
            actor_user_id=42,
            name="New",
            description="Updated desc",
            display_order=5,
            archived=True,
        )

        assert result.name == "New"
        assert result.description == "Updated desc"
        assert result.display_order == 5
        assert result.archived_at is not None
        assert result.updated_at is not None

    @pytest.mark.anyio
    async def test_empty_name_raises(self):
        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = _make_category(id=10, space_id=1)
        cat_repo.save.side_effect = lambda c: c

        svc = _build_service(category_repo=cat_repo, admin_repo=None)

        with pytest.raises(BadRequestError, match="Category name cannot be empty"):
            await svc.update_category(
                space_id=1, category_id=10, actor_user_id=42, name="   "
            )

    @pytest.mark.anyio
    async def test_duplicate_name_raises(self):
        cat_repo = AsyncMock()
        cat = _make_category(id=10, space_id=1, name="Old")
        cat_repo.get_by_id.return_value = cat
        cat_repo.exists_unarchived_name.return_value = True

        svc = _build_service(category_repo=cat_repo, admin_repo=None)

        with pytest.raises(BadRequestError, match="Category name already exists"):
            await svc.update_category(
                space_id=1, category_id=10, actor_user_id=42, name="Taken"
            )

    @pytest.mark.anyio
    async def test_same_name_skips_uniqueness_check(self):
        cat_repo = AsyncMock()
        cat = _make_category(id=10, space_id=1, name="Same")
        cat_repo.get_by_id.return_value = cat
        cat_repo.save.side_effect = lambda c: c

        svc = _build_service(category_repo=cat_repo, admin_repo=None)
        result = await svc.update_category(
            space_id=1, category_id=10, actor_user_id=42, name="Same"
        )

        assert result.name == "Same"
        cat_repo.exists_unarchived_name.assert_not_awaited()

    @pytest.mark.anyio
    async def test_unarchive_sets_none(self):
        cat_repo = AsyncMock()
        cat = _make_category(id=10, space_id=1, archived_at=_NOW)
        cat_repo.get_by_id.return_value = cat
        cat_repo.save.side_effect = lambda c: c

        svc = _build_service(category_repo=cat_repo, admin_repo=None)
        result = await svc.update_category(
            space_id=1, category_id=10, actor_user_id=42, archived=False
        )

        assert result.archived_at is None

    @pytest.mark.anyio
    async def test_category_not_found_raises(self):
        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = None

        svc = _build_service(category_repo=cat_repo, admin_repo=None)

        with pytest.raises(NotFoundError, match="Space category not found"):
            await svc.update_category(
                space_id=1, category_id=999, actor_user_id=42, name="X"
            )


class TestDeleteCategory:
    @pytest.mark.anyio
    async def test_happy_path(self):
        repo = AsyncMock()
        space = _make_space(id=1, default_category_id=10)
        repo.get_by_id.return_value = space

        cat_repo = AsyncMock()
        cat = _make_category(id=20, space_id=1)
        cat_repo.get_by_id.return_value = cat
        cat_repo.save.side_effect = lambda c: c

        task_repo = AsyncMock()
        task_repo.count_tasks.return_value = 0

        svc = _build_service(
            repo=repo, category_repo=cat_repo, admin_repo=None, task_repo=task_repo
        )
        await svc.delete_category(space_id=1, category_id=20, actor_user_id=42)

        assert cat.deleted_at is not None
        assert cat.updated_at is not None
        cat_repo.save.assert_awaited_once()

    @pytest.mark.anyio
    async def test_default_category_raises(self):
        repo = AsyncMock()
        space = _make_space(id=1, default_category_id=10)
        repo.get_by_id.return_value = space

        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = _make_category(id=10, space_id=1)

        svc = _build_service(repo=repo, category_repo=cat_repo, admin_repo=None)

        with pytest.raises(BadRequestError, match="Cannot delete the default category"):
            await svc.delete_category(space_id=1, category_id=10, actor_user_id=42)

    @pytest.mark.anyio
    async def test_category_with_tasks_raises(self):
        repo = AsyncMock()
        space = _make_space(id=1, default_category_id=10)
        repo.get_by_id.return_value = space

        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = _make_category(id=20, space_id=1)

        task_repo = AsyncMock()
        task_repo.count_tasks.return_value = 3

        svc = _build_service(
            repo=repo, category_repo=cat_repo, admin_repo=None, task_repo=task_repo
        )

        with pytest.raises(
            BadRequestError, match="Cannot delete a category that contains tasks"
        ):
            await svc.delete_category(space_id=1, category_id=20, actor_user_id=42)

    @pytest.mark.anyio
    async def test_space_not_found_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        cat_repo = AsyncMock()
        cat_repo.get_by_id.return_value = _make_category(id=20, space_id=1)

        svc = _build_service(repo=repo, category_repo=cat_repo, admin_repo=None)

        with pytest.raises(NotFoundError, match="Resource space not found"):
            await svc.delete_category(space_id=1, category_id=20, actor_user_id=42)

    @pytest.mark.anyio
    async def test_without_task_repo_skips_task_count(self):
        """When task_repo is None, task count check is skipped."""
        repo = AsyncMock()
        space = _make_space(id=1, default_category_id=10)
        repo.get_by_id.return_value = space

        cat_repo = AsyncMock()
        cat = _make_category(id=20, space_id=1)
        cat_repo.get_by_id.return_value = cat
        cat_repo.save.side_effect = lambda c: c

        svc = _build_service(
            repo=repo, category_repo=cat_repo, admin_repo=None, task_repo=None
        )
        await svc.delete_category(space_id=1, category_id=20, actor_user_id=42)

        assert cat.deleted_at is not None


class TestSetCategoryArchived:
    @pytest.mark.anyio
    async def test_archive(self):
        cat_repo = AsyncMock()
        cat = _make_category(id=10, space_id=1, archived_at=None)
        cat_repo.get_by_id.return_value = cat
        cat_repo.save.side_effect = lambda c: c

        svc = _build_service(category_repo=cat_repo, admin_repo=None)
        result = await svc.set_category_archived(
            space_id=1, category_id=10, archived=True, actor_user_id=42
        )

        assert result.archived_at is not None

    @pytest.mark.anyio
    async def test_unarchive(self):
        cat_repo = AsyncMock()
        cat = _make_category(id=10, space_id=1, archived_at=_NOW)
        cat_repo.get_by_id.return_value = cat
        cat_repo.save.side_effect = lambda c: c

        svc = _build_service(category_repo=cat_repo, admin_repo=None)
        result = await svc.set_category_archived(
            space_id=1, category_id=10, archived=False, actor_user_id=42
        )

        assert result.archived_at is None


# ===========================================================================
# Admin relations
# ===========================================================================


class TestListAdmins:
    @pytest.mark.anyio
    async def test_returns_empty_when_no_admin_repo(self):
        svc = _build_service(admin_repo=None)
        result = await svc.list_admins(1)

        assert result == []

    @pytest.mark.anyio
    async def test_delegates_to_admin_repo(self):
        admin_repo = AsyncMock()
        admins = [_make_admin_relation()]
        admin_repo.list_admins.return_value = admins

        svc = _build_service(admin_repo=admin_repo)
        result = await svc.list_admins(1)

        assert result == admins
        admin_repo.list_admins.assert_awaited_once_with(1)


class TestAddAdmin:
    @pytest.mark.anyio
    async def test_happy_path_add_new_admin(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.side_effect = [
            # First call: _ensure_admin for actor (OWNER)
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            # Second call: check if target already exists
            None,
        ]

        svc = _build_service(admin_repo=admin_repo)
        await svc.add_admin(
            space_id=1, target_user_id=99, role=SpaceAdminRole.ADMIN, actor_user_id=42
        )

        admin_repo.add_admin.assert_awaited_once_with(
            space_id=1, user_id=99, role=SpaceAdminRole.ADMIN
        )

    @pytest.mark.anyio
    async def test_admin_repo_none_raises(self):
        svc = _build_service(admin_repo=None)
        # Without admin_repo, _ensure_admin returns immediately, then we hit the None check  # noqa: E501
        with pytest.raises(BadRequestError, match="Space admin repository unavailable"):
            await svc.add_admin(
                space_id=1,
                target_user_id=99,
                role=SpaceAdminRole.ADMIN,
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_existing_admin_raises(self):
        admin_repo = AsyncMock()
        existing = _make_admin_relation(
            user_id=99, role=SpaceAdminRole.ADMIN.value, deleted_at=None
        )
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            existing,
        ]

        svc = _build_service(admin_repo=admin_repo)

        with pytest.raises(BadRequestError, match="User already has a space role"):
            await svc.add_admin(
                space_id=1,
                target_user_id=99,
                role=SpaceAdminRole.ADMIN,
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_promote_existing_admin_to_owner(self):
        admin_repo = AsyncMock()
        existing_admin = _make_admin_relation(
            user_id=99, role=SpaceAdminRole.ADMIN.value, deleted_at=None
        )
        current_owner = _make_admin_relation(
            user_id=42, role=SpaceAdminRole.OWNER.value, deleted_at=None
        )
        admin_repo.get_relation.side_effect = [
            current_owner,  # _ensure_admin for actor
            existing_admin,  # check if target exists
        ]
        admin_repo.get_owner.return_value = current_owner
        admin_repo.save.side_effect = lambda r: r

        svc = _build_service(admin_repo=admin_repo)
        await svc.add_admin(
            space_id=1, target_user_id=99, role=SpaceAdminRole.OWNER, actor_user_id=42
        )

        # The current owner should be demoted to ADMIN
        assert current_owner.role == SpaceAdminRole.ADMIN.value
        # The target should be promoted to OWNER
        assert existing_admin.role == SpaceAdminRole.OWNER.value

    @pytest.mark.anyio
    async def test_add_new_owner_demotes_existing_owner(self):
        admin_repo = AsyncMock()
        current_owner = _make_admin_relation(
            user_id=42, role=SpaceAdminRole.OWNER.value, deleted_at=None
        )
        admin_repo.get_relation.side_effect = [
            current_owner,  # _ensure_admin for actor
            None,  # target doesn't exist
        ]
        admin_repo.get_owner.return_value = current_owner
        admin_repo.save.side_effect = lambda r: r

        svc = _build_service(admin_repo=admin_repo)
        await svc.add_admin(
            space_id=1, target_user_id=99, role=SpaceAdminRole.OWNER, actor_user_id=42
        )

        assert current_owner.role == SpaceAdminRole.ADMIN.value
        admin_repo.add_admin.assert_awaited_once_with(
            space_id=1, user_id=99, role=SpaceAdminRole.OWNER
        )

    @pytest.mark.anyio
    async def test_add_new_owner_no_existing_owner(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            None,  # target doesn't exist
        ]
        admin_repo.get_owner.return_value = None

        svc = _build_service(admin_repo=admin_repo)
        await svc.add_admin(
            space_id=1, target_user_id=99, role=SpaceAdminRole.OWNER, actor_user_id=42
        )

        admin_repo.add_admin.assert_awaited_once_with(
            space_id=1, user_id=99, role=SpaceAdminRole.OWNER
        )


class TestRemoveAdmin:
    @pytest.mark.anyio
    async def test_happy_path(self):
        admin_repo = AsyncMock()
        relation = _make_admin_relation(user_id=99, role=SpaceAdminRole.ADMIN.value)
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            relation,
        ]

        svc = _build_service(admin_repo=admin_repo)
        await svc.remove_admin(space_id=1, target_user_id=99, actor_user_id=42)

        admin_repo.remove_admin.assert_awaited_once_with(relation)

    @pytest.mark.anyio
    async def test_admin_repo_none_raises(self):
        svc = _build_service(admin_repo=None)

        with pytest.raises(BadRequestError, match="Space admin repository unavailable"):
            await svc.remove_admin(space_id=1, target_user_id=99, actor_user_id=42)

    @pytest.mark.anyio
    async def test_relation_not_found_raises(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            None,
        ]

        svc = _build_service(admin_repo=admin_repo)

        with pytest.raises(NotFoundError, match="Space admin relation not found"):
            await svc.remove_admin(space_id=1, target_user_id=99, actor_user_id=42)

    @pytest.mark.anyio
    async def test_cannot_remove_owner_raises(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            _make_admin_relation(user_id=99, role=SpaceAdminRole.OWNER.value),
        ]

        svc = _build_service(admin_repo=admin_repo)

        with pytest.raises(BadRequestError, match="Cannot remove space owner"):
            await svc.remove_admin(space_id=1, target_user_id=99, actor_user_id=42)


class TestUpdateAdminRole:
    @pytest.mark.anyio
    async def test_promote_to_owner(self):
        admin_repo = AsyncMock()
        target_relation = _make_admin_relation(
            user_id=99, role=SpaceAdminRole.ADMIN.value
        )
        current_owner = _make_admin_relation(
            user_id=42, role=SpaceAdminRole.OWNER.value
        )
        admin_repo.get_relation.side_effect = [
            current_owner,  # _ensure_admin for actor
            target_relation,  # get relation for target
        ]
        admin_repo.get_owner.return_value = current_owner
        admin_repo.save.side_effect = lambda r: r

        svc = _build_service(admin_repo=admin_repo)
        await svc.update_admin_role(
            space_id=1,
            target_user_id=99,
            new_role=SpaceAdminRole.OWNER,
            actor_user_id=42,
        )

        assert current_owner.role == SpaceAdminRole.ADMIN.value
        assert target_relation.role == SpaceAdminRole.OWNER.value

    @pytest.mark.anyio
    async def test_already_desired_role_is_noop(self):
        admin_repo = AsyncMock()
        target_relation = _make_admin_relation(
            user_id=99, role=SpaceAdminRole.ADMIN.value
        )
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            target_relation,
        ]

        svc = _build_service(admin_repo=admin_repo)
        await svc.update_admin_role(
            space_id=1,
            target_user_id=99,
            new_role=SpaceAdminRole.ADMIN,
            actor_user_id=42,
        )

        admin_repo.save.assert_not_awaited()

    @pytest.mark.anyio
    async def test_admin_repo_none_raises(self):
        svc = _build_service(admin_repo=None)

        with pytest.raises(BadRequestError, match="Space admin repository unavailable"):
            await svc.update_admin_role(
                space_id=1,
                target_user_id=99,
                new_role=SpaceAdminRole.ADMIN,
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_relation_not_found_raises(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            None,
        ]

        svc = _build_service(admin_repo=admin_repo)

        with pytest.raises(NotFoundError, match="Space admin relation not found"):
            await svc.update_admin_role(
                space_id=1,
                target_user_id=99,
                new_role=SpaceAdminRole.ADMIN,
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_demote_owner_raises(self):
        admin_repo = AsyncMock()
        owner_relation = _make_admin_relation(
            user_id=99, role=SpaceAdminRole.OWNER.value
        )
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            owner_relation,
        ]

        svc = _build_service(admin_repo=admin_repo)

        with pytest.raises(BadRequestError, match="Cannot demote owner directly"):
            await svc.update_admin_role(
                space_id=1,
                target_user_id=99,
                new_role=SpaceAdminRole.ADMIN,
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_change_non_owner_role(self):
        """Test updating an ADMIN's role to another non-OWNER role (direct save path)."""  # noqa: E501
        admin_repo = AsyncMock()
        # In practice SpaceAdminRole only has OWNER=0 and ADMIN=1 but the code path
        # supports the else branch when new_role is not OWNER and relation is not OWNER.
        # We simulate: current role is ADMIN (1), target new role is something else.
        # Since only ADMIN/OWNER exist, we test updating from a custom value.
        target_relation = _make_admin_relation(
            user_id=99, role=2
        )  # hypothetical role=2
        admin_repo.get_relation.side_effect = [
            _make_admin_relation(user_id=42, role=SpaceAdminRole.OWNER.value),
            target_relation,
        ]
        admin_repo.save.side_effect = lambda r: r

        svc = _build_service(admin_repo=admin_repo)
        await svc.update_admin_role(
            space_id=1,
            target_user_id=99,
            new_role=SpaceAdminRole.ADMIN,
            actor_user_id=42,
        )

        assert target_relation.role == SpaceAdminRole.ADMIN.value
        admin_repo.save.assert_awaited_once()


class TestDeleteSpace:
    @pytest.mark.anyio
    async def test_happy_path(self):
        repo = AsyncMock()
        space = _make_space(id=1)
        repo.get_by_id.return_value = space
        repo.save.side_effect = lambda s: s

        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            user_id=42, role=SpaceAdminRole.OWNER.value
        )

        svc = _build_service(repo=repo, admin_repo=admin_repo)
        await svc.delete_space(space_id=1, actor_user_id=42)

        assert space.deleted_at is not None
        assert space.updated_at is not None
        repo.save.assert_awaited_once_with(space)

    @pytest.mark.anyio
    async def test_space_not_found_raises(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None

        svc = _build_service(repo=repo)

        with pytest.raises(NotFoundError, match="Resource space not found"):
            await svc.delete_space(space_id=999, actor_user_id=42)


# ===========================================================================
# _ensure_admin helper
# ===========================================================================


class TestEnsureAdmin:
    @pytest.mark.anyio
    async def test_no_admin_repo_allows_all(self):
        svc = _build_service(admin_repo=None)
        # Should not raise
        await svc._ensure_admin(1, 42, allow_admin=True)

    @pytest.mark.anyio
    async def test_none_user_id_raises(self):
        admin_repo = AsyncMock()
        svc = _build_service(admin_repo=admin_repo)

        with pytest.raises(ForbiddenError, match="Authentication required"):
            await svc._ensure_admin(1, None, allow_admin=True)

    @pytest.mark.anyio
    async def test_no_relation_raises(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None

        svc = _build_service(admin_repo=admin_repo)

        with pytest.raises(
            ForbiddenError, match="Only space admins can perform this action"
        ):
            await svc._ensure_admin(1, 42, allow_admin=True)

    @pytest.mark.anyio
    async def test_admin_allowed_when_allow_admin_true(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.ADMIN.value
        )

        svc = _build_service(admin_repo=admin_repo)
        # Should not raise
        await svc._ensure_admin(1, 42, allow_admin=True)

    @pytest.mark.anyio
    async def test_admin_forbidden_when_allow_admin_false(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.ADMIN.value
        )

        svc = _build_service(admin_repo=admin_repo)

        with pytest.raises(
            ForbiddenError, match="Only space owner can perform this action"
        ):
            await svc._ensure_admin(1, 42, allow_admin=False)

    @pytest.mark.anyio
    async def test_owner_allowed_when_allow_admin_false(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = _make_admin_relation(
            role=SpaceAdminRole.OWNER.value
        )

        svc = _build_service(admin_repo=admin_repo)
        # Should not raise
        await svc._ensure_admin(1, 42, allow_admin=False)


# ===========================================================================
# _promote_admin_to_owner helper
# ===========================================================================


class TestPromoteAdminToOwner:
    @pytest.mark.anyio
    async def test_admin_repo_none_raises(self):
        svc = _build_service(admin_repo=None)
        relation = _make_admin_relation(role=SpaceAdminRole.ADMIN.value)

        with pytest.raises(BadRequestError, match="Admin operations not available"):
            await svc._promote_admin_to_owner(1, relation)

    @pytest.mark.anyio
    async def test_no_existing_owner(self):
        admin_repo = AsyncMock()
        admin_repo.get_owner.return_value = None
        admin_repo.save.side_effect = lambda r: r

        relation = _make_admin_relation(user_id=99, role=SpaceAdminRole.ADMIN.value)

        svc = _build_service(admin_repo=admin_repo)
        await svc._promote_admin_to_owner(1, relation)

        assert relation.role == SpaceAdminRole.OWNER.value
        # save called once for the promoted relation only
        admin_repo.save.assert_awaited_once()

    @pytest.mark.anyio
    async def test_demotes_existing_owner(self):
        admin_repo = AsyncMock()
        current_owner = _make_admin_relation(
            user_id=42, role=SpaceAdminRole.OWNER.value
        )
        admin_repo.get_owner.return_value = current_owner
        admin_repo.save.side_effect = lambda r: r

        relation = _make_admin_relation(user_id=99, role=SpaceAdminRole.ADMIN.value)

        svc = _build_service(admin_repo=admin_repo)
        await svc._promote_admin_to_owner(1, relation)

        assert current_owner.role == SpaceAdminRole.ADMIN.value
        assert relation.role == SpaceAdminRole.OWNER.value
        assert admin_repo.save.await_count == 2


# ===========================================================================
# Static helpers
# ===========================================================================


class TestValidateStrings:
    def test_valid_string(self):
        SpaceService._validate_strings(name="hello")

    def test_empty_string_raises(self):
        with pytest.raises(BadRequestError, match="name cannot be empty"):
            SpaceService._validate_strings(name="   ")

    def test_non_string_raises(self):
        with pytest.raises(BadRequestError, match="name cannot be empty"):
            SpaceService._validate_strings(name=123)  # type: ignore[arg-type]

    def test_multiple_keys(self):
        with pytest.raises(BadRequestError, match="title cannot be empty"):
            SpaceService._validate_strings(name="valid", title="")  # type: ignore[arg-type]


class TestNormalizeJsonList:
    def test_none_returns_empty_list(self):
        assert SpaceService._normalize_json_list(None) == []

    def test_list_returns_same(self):
        data = [1, 2, 3]
        assert SpaceService._normalize_json_list(data) == [1, 2, 3]

    def test_non_list_raises(self):
        with pytest.raises(BadRequestError, match="Expected list value"):
            SpaceService._normalize_json_list("not a list")  # type: ignore[arg-type]
