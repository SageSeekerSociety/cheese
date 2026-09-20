"""Unit tests for app.domain.space.repositories.

Covers SpaceRepository, SpaceCategoryRepository, SpaceUserRankRepository,
SpaceAdminRelationRepository, SpaceClassificationTopicsRepository.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.space.models import SpaceAdminRole
from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceCategoryRepository,
    SpaceClassificationTopicsRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _space(**overrides):
    defaults = {
        "id": 1,
        "name": "Space One",
        "intro": "Intro",
        "description": "Desc",
        "avatar_id": None,
        "enable_rank": True,
        "announcements": [],
        "task_templates": [],
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _category(**overrides):
    defaults = {
        "id": 1,
        "space_id": 1,
        "name": "Category A",
        "description": "Desc",
        "display_order": 0,
        "archived_at": None,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _rank(**overrides):
    defaults = {
        "id": 1,
        "space_id": 1,
        "user_id": 10,
        "rank": 100,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _admin_relation(**overrides):
    defaults = {
        "id": 1,
        "space_id": 1,
        "user_id": 10,
        "role": SpaceAdminRole.ADMIN.value,
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
# SpaceRepository
# ---------------------------------------------------------------------------


class TestSpaceRepository:
    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        sp = _space()
        session.execute.return_value = _mock_scalar(sp)
        repo = SpaceRepository(session)

        result = await repo.get_by_id(1)
        assert result is sp

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_list_spaces(self):
        session = _mock_session()
        sp = _space()
        session.execute.return_value = _mock_scalars([sp])
        repo = SpaceRepository(session)

        result = await repo.list_spaces(
            limit=10, offset=0, visible_to_user_id=42
        )
        assert list(result) == [sp]

    @pytest.mark.anyio
    async def test_count_spaces(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(5)
        repo = SpaceRepository(session)

        assert await repo.count_spaces(visible_to_user_id=42) == 5

    @pytest.mark.anyio
    async def test_exists_by_name_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = SpaceRepository(session)

        assert await repo.exists_by_name("Space One") is True

    @pytest.mark.anyio
    async def test_exists_by_name_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceRepository(session)

        assert await repo.exists_by_name("Nobody") is False

    @pytest.mark.anyio
    async def test_create_space(self):
        session = _mock_session()
        repo = SpaceRepository(session)

        result = await repo.create_space(
            name="New Space",
            intro="Intro",
            description="Desc",
            avatar_id=None,
            enable_rank=True,
            announcements=[],
            task_templates=[],
        )
        assert result.name == "New Space"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        sp = _space()
        repo = SpaceRepository(session)

        result = await repo.save(sp)
        assert result is sp
        session.add.assert_called_once_with(sp)


# ---------------------------------------------------------------------------
# SpaceCategoryRepository
# ---------------------------------------------------------------------------


class TestSpaceCategoryRepository:
    @pytest.mark.anyio
    async def test_get_by_id_and_space(self):
        session = _mock_session()
        cat = _category()
        session.execute.return_value = _mock_scalar(cat)
        repo = SpaceCategoryRepository(session)

        result = await repo.get_by_id_and_space(1, 1)
        assert result is cat

    @pytest.mark.anyio
    async def test_list_categories_for_space(self):
        session = _mock_session()
        cat = _category()
        session.execute.return_value = _mock_scalars([cat])
        repo = SpaceCategoryRepository(session)

        result = await repo.list_categories_for_space(1)
        assert list(result) == [cat]

    @pytest.mark.anyio
    async def test_list_categories_for_space_include_archived(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = SpaceCategoryRepository(session)

        result = await repo.list_categories_for_space(1, include_archived=True)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_create_category(self):
        session = _mock_session()
        repo = SpaceCategoryRepository(session)

        result = await repo.create_category(
            space_id=1, name="New Cat", description="Desc", display_order=1
        )
        assert result.name == "New Cat"
        assert result.display_order == 1
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        cat = _category()
        repo = SpaceCategoryRepository(session)

        result = await repo.save(cat)
        assert result is cat

    @pytest.mark.anyio
    async def test_exists_unarchived_name_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = SpaceCategoryRepository(session)

        assert await repo.exists_unarchived_name(1, "Cat A") is True

    @pytest.mark.anyio
    async def test_exists_unarchived_name_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceCategoryRepository(session)

        assert await repo.exists_unarchived_name(1, "Nonexistent") is False

    @pytest.mark.anyio
    async def test_get_by_id(self):
        session = _mock_session()
        cat = _category()
        session.execute.return_value = _mock_scalar(cat)
        repo = SpaceCategoryRepository(session)

        result = await repo.get_by_id(1)
        assert result is cat


# ---------------------------------------------------------------------------
# SpaceUserRankRepository
# ---------------------------------------------------------------------------


class TestSpaceUserRankRepository:
    @pytest.mark.anyio
    async def test_get_rank_found(self):
        session = _mock_session()
        r = _rank(rank=100)
        session.execute.return_value = _mock_scalar(r)
        repo = SpaceUserRankRepository(session)

        assert await repo.get_rank(1, 10) == 100

    @pytest.mark.anyio
    async def test_get_rank_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceUserRankRepository(session)

        assert await repo.get_rank(1, 10) == 0

    @pytest.mark.anyio
    async def test_get_rank_none_rank_value(self):
        session = _mock_session()
        r = _rank(rank=None)
        session.execute.return_value = _mock_scalar(r)
        repo = SpaceUserRankRepository(session)

        assert await repo.get_rank(1, 10) == 0

    @pytest.mark.anyio
    async def test_increment_rank_new_row(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceUserRankRepository(session)

        result = await repo.increment_rank(space_id=1, user_id=10, delta=50)
        assert result == 50
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_increment_rank_new_row_negative_delta(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceUserRankRepository(session)

        result = await repo.increment_rank(space_id=1, user_id=10, delta=-10)
        assert result == 0  # max(0, -10)

    @pytest.mark.anyio
    async def test_increment_rank_existing(self):
        session = _mock_session()
        r = _rank(rank=100)
        session.execute.return_value = _mock_scalar(r)
        repo = SpaceUserRankRepository(session)

        result = await repo.increment_rank(space_id=1, user_id=10, delta=25)
        assert result == 125
        assert r.rank == 125

    @pytest.mark.anyio
    async def test_increment_rank_existing_clamps_to_zero(self):
        session = _mock_session()
        r = _rank(rank=10)
        session.execute.return_value = _mock_scalar(r)
        repo = SpaceUserRankRepository(session)

        result = await repo.increment_rank(space_id=1, user_id=10, delta=-50)
        assert result == 0
        assert r.rank == 0

    @pytest.mark.anyio
    async def test_set_rank_new_row(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceUserRankRepository(session)

        result = await repo.set_rank(space_id=1, user_id=10, rank=200)
        assert result == 200
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_set_rank_existing(self):
        session = _mock_session()
        r = _rank(rank=100)
        session.execute.return_value = _mock_scalar(r)
        repo = SpaceUserRankRepository(session)

        result = await repo.set_rank(space_id=1, user_id=10, rank=300)
        assert result == 300
        assert r.rank == 300

    @pytest.mark.anyio
    async def test_set_rank_clamps_to_zero(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceUserRankRepository(session)

        result = await repo.set_rank(space_id=1, user_id=10, rank=-5)
        assert result == 0


# ---------------------------------------------------------------------------
# SpaceAdminRelationRepository
# ---------------------------------------------------------------------------


class TestSpaceAdminRelationRepository:
    @pytest.mark.anyio
    async def test_add_admin(self):
        session = _mock_session()
        repo = SpaceAdminRelationRepository(session)

        result = await repo.add_admin(space_id=1, user_id=10, role=SpaceAdminRole.ADMIN)
        assert result.space_id == 1
        assert result.user_id == 10
        assert result.role == SpaceAdminRole.ADMIN.value
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_get_relation_found(self):
        session = _mock_session()
        rel = _admin_relation()
        session.execute.return_value = _mock_scalar(rel)
        repo = SpaceAdminRelationRepository(session)

        result = await repo.get_relation(1, 10)
        assert result is rel

    @pytest.mark.anyio
    async def test_get_relation_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceAdminRelationRepository(session)

        assert await repo.get_relation(1, 999) is None

    @pytest.mark.anyio
    async def test_list_admins(self):
        session = _mock_session()
        rel = _admin_relation()
        session.execute.return_value = _mock_scalars([rel])
        repo = SpaceAdminRelationRepository(session)

        result = await repo.list_admins(1)
        assert list(result) == [rel]

    @pytest.mark.anyio
    async def test_remove_admin(self):
        session = _mock_session()
        rel = _admin_relation()
        repo = SpaceAdminRelationRepository(session)

        await repo.remove_admin(rel)
        assert rel.deleted_at is not None
        assert rel.updated_at == rel.deleted_at

    @pytest.mark.anyio
    async def test_get_owner(self):
        session = _mock_session()
        rel = _admin_relation(role=SpaceAdminRole.OWNER.value)
        session.execute.return_value = _mock_scalar(rel)
        repo = SpaceAdminRelationRepository(session)

        result = await repo.get_owner(1)
        assert result is rel

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        rel = _admin_relation()
        repo = SpaceAdminRelationRepository(session)

        result = await repo.save(rel)
        assert result is rel


# ---------------------------------------------------------------------------
# SpaceClassificationTopicsRepository
# ---------------------------------------------------------------------------


class TestSpaceClassificationTopicsRepository:
    @pytest.mark.anyio
    async def test_list_topics_for_space(self):
        session = _mock_session()
        topic = SimpleNamespace(id=1, name="Topic A")
        session.execute.return_value = _mock_scalars([topic])
        repo = SpaceClassificationTopicsRepository(session)

        result = await repo.list_topics_for_space(1)
        assert result == [topic]

    @pytest.mark.anyio
    async def test_list_topics_for_spaces_empty(self):
        session = _mock_session()
        repo = SpaceClassificationTopicsRepository(session)

        result = await repo.list_topics_for_spaces([])
        assert result == {}

    @pytest.mark.anyio
    async def test_list_topics_for_spaces(self):
        session = _mock_session()
        t1 = SimpleNamespace(id=1, name="Topic A")
        t2 = SimpleNamespace(id=2, name="Topic B")
        session.execute.return_value = _mock_rows([(1, t1), (1, t2), (2, t1)])
        repo = SpaceClassificationTopicsRepository(session)

        result = await repo.list_topics_for_spaces([1, 2])
        assert len(result[1]) == 2
        assert len(result[2]) == 1

    @pytest.mark.anyio
    async def test_replace_topics_for_space(self):
        session = _mock_session()
        old_rel = SimpleNamespace(deleted_at=None, updated_at=None)
        inner_mock = MagicMock()
        inner_mock.scalars.return_value = MagicMock(
            all=MagicMock(return_value=[old_rel])
        )
        session.execute.return_value = inner_mock
        repo = SpaceClassificationTopicsRepository(session)

        await repo.replace_topics_for_space(space_id=1, topic_ids=[10, 20])
        assert old_rel.deleted_at is not None
        # 2 new topics added
        assert session.add.call_count == 2
