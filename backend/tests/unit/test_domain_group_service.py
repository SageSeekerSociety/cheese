"""Unit tests for SpaceService domain group methods."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.space.services import SpaceService

_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group(**overrides):
    defaults = {
        "id": 1,
        "space_id": 100,
        "name": "计算机学院",
        "description": "计算机相关专业",
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_admin_rel(**overrides):
    defaults = {
        "id": 1,
        "space_id": 100,
        "user_id": 42,
        "role": 0,  # OWNER
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _svc(*, group_repo=None, domain_repo=None, admin_repo=None):
    _admin_repo_provided = admin_repo is not None
    group_repo = group_repo or AsyncMock()
    domain_repo = domain_repo or AsyncMock()
    admin_repo = admin_repo or AsyncMock()
    if not _admin_repo_provided:
        admin_repo.get_relation.return_value = _make_admin_rel()
    return SpaceService(
        repo=AsyncMock(),
        category_repo=AsyncMock(),
        admin_repo=admin_repo,
        domain_group_repo=group_repo,
        domain_group_domain_repo=domain_repo,
    )


# ---------------------------------------------------------------------------
# list_domain_groups
# ---------------------------------------------------------------------------


class TestListDomainGroups:
    @pytest.mark.anyio
    async def test_returns_groups_with_domains(self):
        group_repo = AsyncMock()
        g1 = _make_group(id=1, name="A")
        g2 = _make_group(id=2, name="B")
        group_repo.list_groups.return_value = [g1, g2]

        domain_repo = AsyncMock()
        domain_repo.list_domains_for_groups.return_value = {
            1: ["cs.edu.cn", "ai.edu.cn"],
            2: ["math.edu.cn"],
        }

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        result = await svc.list_domain_groups(space_id=100, actor_user_id=42)

        assert len(result) == 2
        assert result[0][0] is g1
        assert result[0][1] == ["cs.edu.cn", "ai.edu.cn"]
        assert result[1][0] is g2
        assert result[1][1] == ["math.edu.cn"]

    @pytest.mark.anyio
    async def test_returns_empty_list(self):
        group_repo = AsyncMock()
        group_repo.list_groups.return_value = []
        domain_repo = AsyncMock()
        domain_repo.list_domains_for_groups.return_value = {}

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        result = await svc.list_domain_groups(space_id=100, actor_user_id=42)
        assert result == []

    @pytest.mark.anyio
    async def test_allows_non_admin_to_list(self):
        """Non-admin users can list domain groups (needed for task publish/edit)."""
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None  # not an admin

        group_repo = AsyncMock()
        g = _make_group()
        group_repo.list_groups.return_value = [g]
        domain_repo = AsyncMock()
        domain_repo.list_domains_for_groups.return_value = {1: ["example.com"]}

        svc = _svc(
            group_repo=group_repo, domain_repo=domain_repo, admin_repo=admin_repo
        )
        result = await svc.list_domain_groups(space_id=100, actor_user_id=42)

        assert len(result) == 1
        assert result[0][0] is g
        assert result[0][1] == ["example.com"]


# ---------------------------------------------------------------------------
# create_domain_group
# ---------------------------------------------------------------------------


class TestCreateDomainGroup:
    @pytest.mark.anyio
    async def test_creates_with_valid_data(self):
        group_repo = AsyncMock()
        group_repo.exists_name.return_value = False
        g = _make_group(name="物理学院")
        group_repo.create_group.return_value = g

        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        group, domains = await svc.create_domain_group(
            space_id=100,
            name="物理学院",
            description="物理系",
            domains=["phy.edu.cn", "phys.edu.cn"],
            actor_user_id=42,
        )

        assert group is g
        assert domains == ["phy.edu.cn", "phys.edu.cn"]
        group_repo.create_group.assert_awaited_once_with(
            space_id=100, name="物理学院", description="物理系"
        )
        domain_repo.replace_domains.assert_awaited_once_with(
            group_id=g.id, domains=["phy.edu.cn", "phys.edu.cn"]
        )

    @pytest.mark.anyio
    async def test_raises_when_name_empty(self):
        group_repo = AsyncMock()
        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        with pytest.raises(BadRequestError, match="name cannot be empty"):
            await svc.create_domain_group(
                space_id=100,
                name="   ",
                description=None,
                domains=["cs.edu.cn"],
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_raises_when_domains_empty(self):
        group_repo = AsyncMock()
        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        with pytest.raises(BadRequestError, match="Domain list cannot be empty"):
            await svc.create_domain_group(
                space_id=100,
                name="学院",
                description=None,
                domains=[],
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_raises_when_name_duplicate(self):
        group_repo = AsyncMock()
        group_repo.exists_name.return_value = True
        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        with pytest.raises(BadRequestError, match="already exists"):
            await svc.create_domain_group(
                space_id=100,
                name="计算机学院",
                description=None,
                domains=["cs.edu.cn"],
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_raises_when_not_admin(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None

        svc = _svc(admin_repo=admin_repo)
        with pytest.raises(ForbiddenError):
            await svc.create_domain_group(
                space_id=100,
                name="test",
                description=None,
                domains=["cs.edu.cn"],
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_rejects_invalid_domain_with_at(self):
        group_repo = AsyncMock()
        group_repo.exists_name.return_value = False
        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        with pytest.raises(BadRequestError, match="Invalid domain"):
            await svc.create_domain_group(
                space_id=100,
                name="test",
                description=None,
                domains=["user@cs.edu.cn"],
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_normalizes_domains_lowercase_and_dedup(self):
        group_repo = AsyncMock()
        group_repo.exists_name.return_value = False
        g = _make_group()
        group_repo.create_group.return_value = g
        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        _, domains = await svc.create_domain_group(
            space_id=100,
            name="test",
            description=None,
            domains=["CS.EDU.CN", "Cs.Edu.cn", "cs.edu.cn"],
            actor_user_id=42,
        )
        assert domains == ["cs.edu.cn"]


# ---------------------------------------------------------------------------
# update_domain_group
# ---------------------------------------------------------------------------


class TestUpdateDomainGroup:
    @pytest.mark.anyio
    async def test_updates_name(self):
        group_repo = AsyncMock()
        g = _make_group(name="old")
        group_repo.get_by_id.return_value = g
        group_repo.exists_name.return_value = False

        domain_repo = AsyncMock()
        domain_repo.list_domains_for_group.return_value = ["cs.edu.cn"]

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        group, domains = await svc.update_domain_group(
            space_id=100,
            group_id=1,
            name="new_name",
            description=None,
            domains=None,
            actor_user_id=42,
        )

        assert g.name == "new_name"
        assert domains == ["cs.edu.cn"]

    @pytest.mark.anyio
    async def test_updates_description(self):
        group_repo = AsyncMock()
        g = _make_group(description="old")
        group_repo.get_by_id.return_value = g

        domain_repo = AsyncMock()
        domain_repo.list_domains_for_group.return_value = ["cs.edu.cn"]

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        _, _ = await svc.update_domain_group(
            space_id=100,
            group_id=1,
            name=None,
            description="  new desc  ",
            domains=None,
            actor_user_id=42,
        )
        assert g.description == "new desc"

    @pytest.mark.anyio
    async def test_updates_domains(self):
        group_repo = AsyncMock()
        g = _make_group()
        group_repo.get_by_id.return_value = g

        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        _, domains = await svc.update_domain_group(
            space_id=100,
            group_id=1,
            name=None,
            description=None,
            domains=["new.edu.cn"],
            actor_user_id=42,
        )

        assert domains == ["new.edu.cn"]
        domain_repo.replace_domains.assert_awaited_once_with(
            group_id=1, domains=["new.edu.cn"]
        )

    @pytest.mark.anyio
    async def test_raises_when_group_not_found(self):
        group_repo = AsyncMock()
        group_repo.get_by_id.return_value = None
        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        with pytest.raises(NotFoundError, match="domain group not found"):
            await svc.update_domain_group(
                space_id=100,
                group_id=999,
                name="test",
                description=None,
                domains=None,
                actor_user_id=42,
            )

    @pytest.mark.anyio
    async def test_raises_when_name_duplicate(self):
        group_repo = AsyncMock()
        g = _make_group(name="old")
        group_repo.get_by_id.return_value = g
        group_repo.exists_name.return_value = True

        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        with pytest.raises(BadRequestError, match="already exists"):
            await svc.update_domain_group(
                space_id=100,
                group_id=1,
                name="other",
                description=None,
                domains=None,
                actor_user_id=42,
            )


# ---------------------------------------------------------------------------
# delete_domain_group
# ---------------------------------------------------------------------------


class TestDeleteDomainGroup:
    @pytest.mark.anyio
    async def test_soft_deletes_group_and_domains(self):
        group_repo = AsyncMock()
        g = _make_group()
        group_repo.get_by_id.return_value = g

        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        await svc.delete_domain_group(space_id=100, group_id=1, actor_user_id=42)

        assert g.deleted_at is not None
        group_repo.save.assert_awaited_once_with(g)
        domain_repo.soft_delete_by_group.assert_awaited_once_with(group_id=1)

    @pytest.mark.anyio
    async def test_raises_when_group_not_found(self):
        group_repo = AsyncMock()
        group_repo.get_by_id.return_value = None
        domain_repo = AsyncMock()

        svc = _svc(group_repo=group_repo, domain_repo=domain_repo)
        with pytest.raises(NotFoundError, match="domain group not found"):
            await svc.delete_domain_group(space_id=100, group_id=999, actor_user_id=42)

    @pytest.mark.anyio
    async def test_raises_when_not_admin(self):
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None

        svc = _svc(admin_repo=admin_repo)
        with pytest.raises(ForbiddenError):
            await svc.delete_domain_group(space_id=100, group_id=1, actor_user_id=42)
