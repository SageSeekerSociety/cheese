"""Unit tests for ProjectService."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError, NotFoundError
from app.domain.project.models import ProjectMemberRole
from app.domain.project.services import ProjectService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_project(**overrides):
    defaults = {
        "id": 1,
        "name": "My Project",
        "description": "A test project",
        "color_code": "#FF0000",
        "content": "",
        "team_id": 10,
        "leader_id": 42,
        "parent_id": None,
        "external_task_id": None,
        "github_repo": None,
        "start_date": _NOW,
        "end_date": _NOW,
        "archived": False,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_membership(**overrides):
    defaults = {
        "id": 100,
        "project_id": 1,
        "user_id": 42,
        "role": ProjectMemberRole.MEMBER,
        "notes": "",
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(
    *,
    repo: AsyncMock | None = None,
    membership_repo: AsyncMock | None = None,
) -> ProjectService:
    if repo is None:
        repo = AsyncMock()
    return ProjectService(repo=repo, membership_repo=membership_repo)


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------


class TestCreateProject:
    @pytest.mark.anyio
    async def test_creates_project_with_required_fields(self):
        repo = AsyncMock()
        expected = _make_project()
        repo.create_project.return_value = expected
        svc = _make_service(repo=repo)

        result = await svc.create_project(
            name="My Project",
            description="A test project",
            color_code="#FF0000",
            team_id=10,
            leader_id=42,
            start_date=1_700_000_000_000,
            end_date=1_700_100_000_000,
        )

        assert result is expected
        repo.create_project.assert_awaited_once()
        call_kw = repo.create_project.call_args.kwargs
        assert call_kw["name"] == "My Project"
        assert call_kw["description"] == "A test project"
        assert call_kw["color_code"] == "#FF0000"
        assert call_kw["team_id"] == 10
        assert call_kw["leader_id"] == 42
        assert call_kw["content"] is None
        assert call_kw["parent_id"] is None
        assert call_kw["external_task_id"] is None
        assert call_kw["github_repo"] is None

    @pytest.mark.anyio
    async def test_creates_project_with_optional_fields(self):
        repo = AsyncMock()
        expected = _make_project(
            content="detailed content",
            parent_id=5,
            external_task_id=99,
            github_repo="org/repo",
        )
        repo.create_project.return_value = expected
        svc = _make_service(repo=repo)

        result = await svc.create_project(
            name="My Project",
            description="A test project",
            color_code="#FF0000",
            team_id=10,
            leader_id=42,
            start_date=1_700_000_000_000,
            end_date=1_700_100_000_000,
            content="detailed content",
            parent_id=5,
            external_task_id=99,
            github_repo="org/repo",
        )

        assert result is expected
        call_kw = repo.create_project.call_args.kwargs
        assert call_kw["content"] == "detailed content"
        assert call_kw["parent_id"] == 5
        assert call_kw["external_task_id"] == 99
        assert call_kw["github_repo"] == "org/repo"

    @pytest.mark.anyio
    async def test_converts_millisecond_timestamps_to_utc_datetimes(self):
        repo = AsyncMock()
        repo.create_project.return_value = _make_project()
        svc = _make_service(repo=repo)

        await svc.create_project(
            name="P",
            description="d",
            color_code="#000",
            team_id=1,
            leader_id=1,
            start_date=0,
            end_date=1_000,
        )

        call_kw = repo.create_project.call_args.kwargs
        assert call_kw["start_date"].tzinfo == UTC
        assert call_kw["end_date"].tzinfo == UTC
        # 0 ms -> epoch, 1000 ms -> 1 second after epoch
        assert call_kw["start_date"].timestamp() == 0.0
        assert call_kw["end_date"].timestamp() == 1.0


# ---------------------------------------------------------------------------
# get_projects_by_ids
# ---------------------------------------------------------------------------


class TestGetProjectsByIds:
    @pytest.mark.anyio
    async def test_returns_dict_from_repo(self):
        repo = AsyncMock()
        p1, p2 = _make_project(id=1), _make_project(id=2)
        repo.get_by_ids.return_value = {1: p1, 2: p2}
        svc = _make_service(repo=repo)

        result = await svc.get_projects_by_ids([1, 2])

        assert result == {1: p1, 2: p2}
        repo.get_by_ids.assert_awaited_once_with([1, 2])

    @pytest.mark.anyio
    async def test_returns_empty_dict_for_empty_list(self):
        repo = AsyncMock()
        repo.get_by_ids.return_value = {}
        svc = _make_service(repo=repo)

        result = await svc.get_projects_by_ids([])

        assert result == {}


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------


class TestGetProject:
    @pytest.mark.anyio
    async def test_returns_project_when_found(self):
        repo = AsyncMock()
        proj = _make_project()
        repo.get_by_id.return_value = proj
        svc = _make_service(repo=repo)

        result = await svc.get_project(1)

        assert result is proj
        repo.get_by_id.assert_awaited_once_with(1)

    @pytest.mark.anyio
    async def test_returns_none_when_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        result = await svc.get_project(999)

        assert result is None


# ---------------------------------------------------------------------------
# update_project
# ---------------------------------------------------------------------------


class TestUpdateProject:
    @pytest.mark.anyio
    async def test_updates_name_only(self):
        repo = AsyncMock()
        proj = _make_project()
        repo.save.return_value = proj
        svc = _make_service(repo=repo)

        result = await svc.update_project(proj, name="New Name")

        assert proj.name == "New Name"
        assert proj.description == "A test project"  # unchanged
        repo.save.assert_awaited_once_with(proj)
        assert result is proj

    @pytest.mark.anyio
    async def test_updates_description_only(self):
        repo = AsyncMock()
        proj = _make_project()
        repo.save.return_value = proj
        svc = _make_service(repo=repo)

        await svc.update_project(proj, description="Updated desc")

        assert proj.description == "Updated desc"
        assert proj.name == "My Project"  # unchanged

    @pytest.mark.anyio
    async def test_updates_color_code_only(self):
        repo = AsyncMock()
        proj = _make_project()
        repo.save.return_value = proj
        svc = _make_service(repo=repo)

        await svc.update_project(proj, color_code="#00FF00")

        assert proj.color_code == "#00FF00"

    @pytest.mark.anyio
    async def test_updates_archived_only(self):
        repo = AsyncMock()
        proj = _make_project(archived=False)
        repo.save.return_value = proj
        svc = _make_service(repo=repo)

        await svc.update_project(proj, archived=True)

        assert proj.archived is True

    @pytest.mark.anyio
    async def test_updates_all_fields(self):
        repo = AsyncMock()
        proj = _make_project()
        repo.save.return_value = proj
        svc = _make_service(repo=repo)

        await svc.update_project(
            proj,
            name="N",
            description="D",
            color_code="#CCC",
            archived=True,
        )

        assert proj.name == "N"
        assert proj.description == "D"
        assert proj.color_code == "#CCC"
        assert proj.archived is True

    @pytest.mark.anyio
    async def test_no_fields_leaves_project_unchanged(self):
        repo = AsyncMock()
        proj = _make_project()
        repo.save.return_value = proj
        svc = _make_service(repo=repo)

        await svc.update_project(proj)

        assert proj.name == "My Project"
        assert proj.description == "A test project"
        assert proj.color_code == "#FF0000"
        assert proj.archived is False
        repo.save.assert_awaited_once_with(proj)


# ---------------------------------------------------------------------------
# soft_delete_project
# ---------------------------------------------------------------------------


class TestSoftDeleteProject:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        repo = AsyncMock()
        proj = _make_project()
        svc = _make_service(repo=repo)

        await svc.soft_delete_project(proj)

        repo.soft_delete.assert_awaited_once_with(proj)


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


class TestListProjects:
    @pytest.mark.anyio
    async def test_lists_with_team_id_only(self):
        repo = AsyncMock()
        projects = [_make_project(id=1), _make_project(id=2)]
        repo.list_projects.return_value = projects
        svc = _make_service(repo=repo)

        result = await svc.list_projects(team_id=10)

        assert result == projects
        repo.list_projects.assert_awaited_once_with(
            team_id=10,
            parent_id=None,
            leader_id=None,
            member_id=None,
            archived=None,
        )

    @pytest.mark.anyio
    async def test_lists_with_all_filters(self):
        repo = AsyncMock()
        repo.list_projects.return_value = []
        svc = _make_service(repo=repo)

        result = await svc.list_projects(
            team_id=10,
            parent_id=5,
            leader_id=42,
            member_id=7,
            archived=True,
        )

        assert result == []
        repo.list_projects.assert_awaited_once_with(
            team_id=10,
            parent_id=5,
            leader_id=42,
            member_id=7,
            archived=True,
        )


# ---------------------------------------------------------------------------
# _require_membership_repo
# ---------------------------------------------------------------------------


class TestRequireMembershipRepo:
    def test_raises_when_membership_repo_is_none(self):
        svc = _make_service(membership_repo=None)

        with pytest.raises(BadRequestError, match="unavailable"):
            svc._require_membership_repo()

    def test_returns_repo_when_set(self):
        membership_repo = AsyncMock()
        svc = _make_service(membership_repo=membership_repo)

        result = svc._require_membership_repo()

        assert result is membership_repo


# ---------------------------------------------------------------------------
# list_members
# ---------------------------------------------------------------------------


class TestListMembers:
    @pytest.mark.anyio
    async def test_lists_members_with_defaults(self):
        membership_repo = AsyncMock()
        members = [_make_membership()]
        membership_repo.list_members.return_value = (members, 1)
        svc = _make_service(membership_repo=membership_repo)

        result, total = await svc.list_members(project_id=1)

        assert result == members
        assert total == 1
        membership_repo.list_members.assert_awaited_once_with(1, limit=20, offset=0)

    @pytest.mark.anyio
    async def test_lists_members_with_custom_pagination(self):
        membership_repo = AsyncMock()
        membership_repo.list_members.return_value = ([], 0)
        svc = _make_service(membership_repo=membership_repo)

        result, total = await svc.list_members(project_id=1, limit=5, offset=10)

        assert result == []
        assert total == 0
        membership_repo.list_members.assert_awaited_once_with(1, limit=5, offset=10)

    @pytest.mark.anyio
    async def test_raises_when_membership_repo_unavailable(self):
        svc = _make_service(membership_repo=None)

        with pytest.raises(BadRequestError, match="unavailable"):
            await svc.list_members(project_id=1)


# ---------------------------------------------------------------------------
# add_member
# ---------------------------------------------------------------------------


class TestAddMember:
    @pytest.mark.anyio
    async def test_adds_new_member(self):
        membership_repo = AsyncMock()
        membership_repo.get_relation.return_value = None
        expected = _make_membership()
        membership_repo.add_member.return_value = expected
        svc = _make_service(membership_repo=membership_repo)

        result = await svc.add_member(project_id=1, user_id=42, role="MEMBER")

        assert result is expected
        membership_repo.get_relation.assert_awaited_once_with(1, 42)
        membership_repo.add_member.assert_awaited_once_with(
            project_id=1,
            user_id=42,
            role=ProjectMemberRole.MEMBER,
            notes="",
        )

    @pytest.mark.anyio
    async def test_adds_member_with_notes(self):
        membership_repo = AsyncMock()
        membership_repo.get_relation.return_value = None
        expected = _make_membership(notes="Lead dev")
        membership_repo.add_member.return_value = expected
        svc = _make_service(membership_repo=membership_repo)

        result = await svc.add_member(project_id=1, user_id=42, role="ADMIN", notes="Lead dev")

        assert result is expected
        membership_repo.add_member.assert_awaited_once_with(
            project_id=1,
            user_id=42,
            role=ProjectMemberRole.ADMIN,
            notes="Lead dev",
        )

    @pytest.mark.anyio
    async def test_raises_when_user_already_member(self):
        membership_repo = AsyncMock()
        membership_repo.get_relation.return_value = _make_membership()
        svc = _make_service(membership_repo=membership_repo)

        with pytest.raises(BadRequestError, match="already a member"):
            await svc.add_member(project_id=1, user_id=42, role="MEMBER")

        membership_repo.add_member.assert_not_awaited()

    @pytest.mark.anyio
    async def test_raises_when_membership_repo_unavailable(self):
        svc = _make_service(membership_repo=None)

        with pytest.raises(BadRequestError, match="unavailable"):
            await svc.add_member(project_id=1, user_id=42, role="MEMBER")


# ---------------------------------------------------------------------------
# remove_member
# ---------------------------------------------------------------------------


class TestRemoveMember:
    @pytest.mark.anyio
    async def test_removes_existing_member(self):
        membership_repo = AsyncMock()
        existing = _make_membership()
        membership_repo.get_relation.return_value = existing
        svc = _make_service(membership_repo=membership_repo)

        await svc.remove_member(project_id=1, user_id=42)

        membership_repo.get_relation.assert_awaited_once_with(1, 42)
        membership_repo.remove_member.assert_awaited_once_with(existing)

    @pytest.mark.anyio
    async def test_raises_when_membership_not_found(self):
        membership_repo = AsyncMock()
        membership_repo.get_relation.return_value = None
        svc = _make_service(membership_repo=membership_repo)

        with pytest.raises(NotFoundError, match="not found"):
            await svc.remove_member(project_id=1, user_id=42)

        membership_repo.remove_member.assert_not_awaited()

    @pytest.mark.anyio
    async def test_raises_when_membership_repo_unavailable(self):
        svc = _make_service(membership_repo=None)

        with pytest.raises(BadRequestError, match="unavailable"):
            await svc.remove_member(project_id=1, user_id=42)


# ---------------------------------------------------------------------------
# _parse_role
# ---------------------------------------------------------------------------


class TestParseRole:
    def test_parses_member(self):
        assert ProjectService._parse_role("MEMBER") == ProjectMemberRole.MEMBER

    def test_parses_admin(self):
        assert ProjectService._parse_role("ADMIN") == ProjectMemberRole.ADMIN

    def test_parses_owner(self):
        assert ProjectService._parse_role("OWNER") == ProjectMemberRole.OWNER

    def test_parses_case_insensitive(self):
        assert ProjectService._parse_role("member") == ProjectMemberRole.MEMBER
        assert ProjectService._parse_role("Admin") == ProjectMemberRole.ADMIN
        assert ProjectService._parse_role("oWnEr") == ProjectMemberRole.OWNER

    def test_raises_for_invalid_role(self):
        with pytest.raises(BadRequestError, match="Invalid role: invalid"):
            ProjectService._parse_role("invalid")

    def test_raises_for_empty_string(self):
        with pytest.raises(BadRequestError, match="Invalid role"):
            ProjectService._parse_role("")
