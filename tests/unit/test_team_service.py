"""Unit tests for TeamService and TeamMembershipService."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.models import (
    ApplicationStatus,
    ApplicationType,
    TeamMemberRole,
)
from app.domain.team.services import TeamService


@pytest.fixture(autouse=True)
def _patch_team_locking_check():
    """Bypass team-locking DB queries in unit tests (covered by integration tests)."""
    with patch("app.domain.team.services.check_team_locking_status", new_callable=AsyncMock):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_team(**overrides):
    defaults = {
        "id": 1,
        "name": "Test Team",
        "intro": "intro",
        "description": "desc",
        "avatar_id": 10,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_relation(**overrides):
    defaults = {
        "id": 100,
        "team_id": 1,
        "user_id": 42,
        "role": TeamMemberRole.MEMBER,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_application(**overrides):
    defaults = {
        "id": 200,
        "user_id": 42,
        "team_id": 1,
        "initiator_id": 42,
        "type": ApplicationType.REQUEST.value,
        "status": ApplicationStatus.PENDING.value,
        "role": "MEMBER",
        "message": "",
        "processed_by_id": None,
        "processed_at": None,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _team_repo_mock() -> AsyncMock:
    repo = AsyncMock()
    repo._session = MagicMock()
    repo._session.add = MagicMock()
    repo._session.flush = AsyncMock()
    return repo


def _app_repo_mock() -> AsyncMock:
    return AsyncMock()


def _build_team_service(repo=None) -> tuple[TeamService, AsyncMock]:
    repo = repo or _team_repo_mock()
    svc = TeamService(repo)
    return svc, repo


def _build_membership_service(
    team_repo=None, app_repo=None
) -> tuple[TeamMembershipService, AsyncMock, AsyncMock]:
    team_repo = team_repo or _team_repo_mock()
    app_repo = app_repo or _app_repo_mock()
    session = AsyncMock()
    # _validate_user_can_apply_or_be_invited does
    #   await self._session.execute(select(User.id).where(...))
    # The mock must return a result whose scalar_one_or_none() is non-None
    # (= user exists). Tests that want "user not found" override this.
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = 1
    session.execute.return_value = mock_result
    svc = TeamMembershipService(session=session, team_repo=team_repo, application_repo=app_repo)
    return svc, team_repo, app_repo


# ===================================================================
# TeamService tests
# ===================================================================


class TestTeamServiceGetTeam:
    @pytest.mark.anyio
    async def test_returns_team_when_found(self):
        svc, repo = _build_team_service()
        team = _make_team(id=5)
        repo.get_by_id.return_value = team

        result = await svc.get_team(5)

        assert result is team
        repo.get_by_id.assert_awaited_once_with(5)

    @pytest.mark.anyio
    async def test_returns_none_when_not_found(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = None

        result = await svc.get_team(999)

        assert result is None


class TestTeamServiceEnumerateTeams:
    @pytest.mark.anyio
    async def test_returns_list_of_teams(self):
        svc, repo = _build_team_service()
        teams = [_make_team(id=1), _make_team(id=2)]
        repo.list_teams.return_value = teams

        result = await svc.enumerate_teams(query=None, limit=20, offset=0)

        assert result == teams
        repo.list_teams.assert_awaited_once_with(query=None, limit=20, offset=0)

    @pytest.mark.anyio
    async def test_passes_query_filter(self):
        svc, repo = _build_team_service()
        repo.list_teams.return_value = []

        await svc.enumerate_teams(query="search", limit=10, offset=5)

        repo.list_teams.assert_awaited_once_with(query="search", limit=10, offset=5)


class TestTeamServiceGetTeamsByIds:
    @pytest.mark.anyio
    async def test_returns_dict_of_teams(self):
        svc, repo = _build_team_service()
        t1 = _make_team(id=1)
        t2 = _make_team(id=2)
        repo.get_by_ids.return_value = {1: t1, 2: t2}

        result = await svc.get_teams_by_ids([1, 2])

        assert result == {1: t1, 2: t2}
        repo.get_by_ids.assert_awaited_once_with([1, 2])


class TestTeamServiceGetTeamsOfUser:
    @pytest.mark.anyio
    async def test_returns_user_teams(self):
        svc, repo = _build_team_service()
        teams = [_make_team(id=10)]
        repo.list_teams_of_user.return_value = teams

        result = await svc.get_teams_of_user(user_id=42)

        assert result == teams
        repo.list_teams_of_user.assert_awaited_once_with(user_id=42)


class TestTeamServiceGetTeamMembers:
    @pytest.mark.anyio
    async def test_returns_member_relations(self):
        svc, repo = _build_team_service()
        rels = [_make_relation(user_id=1), _make_relation(user_id=2)]
        repo.list_members_of_team.return_value = rels

        result = await svc.get_team_members(team_id=1)

        assert result == rels
        repo.list_members_of_team.assert_awaited_once_with(team_id=1)


class TestTeamServiceIsTeamMember:
    @pytest.mark.anyio
    async def test_true_when_member(self):
        svc, repo = _build_team_service()
        repo.is_team_member.return_value = True

        assert await svc.is_team_member(1, 42) is True

    @pytest.mark.anyio
    async def test_false_when_not_member(self):
        svc, repo = _build_team_service()
        repo.is_team_member.return_value = False

        assert await svc.is_team_member(1, 42) is False


class TestTeamServiceIsTeamAtLeastAdmin:
    @pytest.mark.anyio
    async def test_true_for_admin(self):
        svc, repo = _build_team_service()
        repo.is_team_at_least_admin.return_value = True

        assert await svc.is_team_at_least_admin(1, 42) is True

    @pytest.mark.anyio
    async def test_false_for_regular_member(self):
        svc, repo = _build_team_service()
        repo.is_team_at_least_admin.return_value = False

        assert await svc.is_team_at_least_admin(1, 42) is False


class TestTeamServiceCreateTeam:
    @pytest.mark.anyio
    async def test_creates_team_and_owner_relation(self):
        svc, repo = _build_team_service()
        repo.exists_by_name.return_value = False

        result = await svc.create_team(
            name="New Team",
            intro="Hello",
            description="A description",
            avatar_id=5,
            owner_id=42,
        )

        assert result.name == "New Team"
        assert result.intro == "Hello"
        assert result.description == "A description"
        assert result.avatar_id == 5
        assert result.deleted_at is None
        # session.add called twice: once for team, once for owner relation
        assert repo._session.add.call_count == 2
        assert repo._session.flush.await_count == 2

    @pytest.mark.anyio
    async def test_strips_whitespace_from_name(self):
        svc, repo = _build_team_service()
        repo.exists_by_name.return_value = False

        result = await svc.create_team(
            name="  Spacey Name  ",
            intro="",
            description="",
            avatar_id=1,
            owner_id=42,
        )

        assert result.name == "Spacey Name"
        repo.exists_by_name.assert_awaited_once_with("Spacey Name")

    @pytest.mark.anyio
    async def test_rejects_empty_name(self):
        svc, repo = _build_team_service()

        with pytest.raises(BadRequestError, match="cannot be empty"):
            await svc.create_team(
                name="   ",
                intro="",
                description="",
                avatar_id=1,
                owner_id=42,
            )

    @pytest.mark.anyio
    async def test_rejects_duplicate_name(self):
        svc, repo = _build_team_service()
        repo.exists_by_name.return_value = True

        with pytest.raises(ConflictError, match="already exists"):
            await svc.create_team(
                name="Existing",
                intro="",
                description="",
                avatar_id=1,
                owner_id=42,
            )

    @pytest.mark.anyio
    async def test_owner_relation_uses_owner_role(self):
        svc, repo = _build_team_service()
        repo.exists_by_name.return_value = False

        await svc.create_team(name="T", intro="", description="", avatar_id=1, owner_id=99)

        # The second add() call should be the owner relation
        second_add_call = repo._session.add.call_args_list[1]
        relation = second_add_call[0][0]
        assert relation.role == TeamMemberRole.OWNER
        assert relation.user_id == 99


class TestTeamServiceUpdateTeam:
    @pytest.mark.anyio
    async def test_updates_team_name(self):
        svc, repo = _build_team_service()
        team = _make_team(id=1, name="Old Name")
        repo.get_by_id.return_value = team
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.OWNER)
        repo.exists_by_name.return_value = False

        result = await svc.update_team(team_id=1, actor_user_id=42, name="New Name")

        assert result.name == "New Name"

    @pytest.mark.anyio
    async def test_updates_intro_and_description(self):
        svc, repo = _build_team_service()
        team = _make_team(id=1)
        repo.get_by_id.return_value = team
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.ADMIN)

        result = await svc.update_team(
            team_id=1,
            actor_user_id=42,
            intro="new intro",
            description="new desc",
        )

        assert result.intro == "new intro"
        assert result.description == "new desc"

    @pytest.mark.anyio
    async def test_updates_avatar_id(self):
        svc, repo = _build_team_service()
        team = _make_team(id=1)
        repo.get_by_id.return_value = team
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.OWNER)

        result = await svc.update_team(team_id=1, actor_user_id=42, avatar_id=99)

        assert result.avatar_id == 99

    @pytest.mark.anyio
    async def test_rejects_non_admin(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = _make_team(id=1)
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.MEMBER)

        with pytest.raises(ForbiddenError, match="Only team owner or admins"):
            await svc.update_team(team_id=1, actor_user_id=42, name="X")

    @pytest.mark.anyio
    async def test_rejects_non_member(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = _make_team(id=1)
        repo.get_member_relation.return_value = None

        with pytest.raises(ForbiddenError):
            await svc.update_team(team_id=1, actor_user_id=42, name="X")

    @pytest.mark.anyio
    async def test_rejects_empty_name_update(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = _make_team(id=1)
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.OWNER)

        with pytest.raises(BadRequestError, match="cannot be empty"):
            await svc.update_team(team_id=1, actor_user_id=42, name="   ")

    @pytest.mark.anyio
    async def test_rejects_duplicate_name_update(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = _make_team(id=1, name="Old")
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.OWNER)
        repo.exists_by_name.return_value = True

        with pytest.raises(ConflictError, match="already exists"):
            await svc.update_team(team_id=1, actor_user_id=42, name="Taken")

    @pytest.mark.anyio
    async def test_allows_same_name_unchanged(self):
        svc, repo = _build_team_service()
        team = _make_team(id=1, name="Same")
        repo.get_by_id.return_value = team
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.OWNER)
        # exists_by_name should not matter since name is unchanged
        repo.exists_by_name.return_value = True

        result = await svc.update_team(team_id=1, actor_user_id=42, name="Same")

        assert result.name == "Same"

    @pytest.mark.anyio
    async def test_rejects_invalid_avatar_id(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = _make_team(id=1)
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.OWNER)

        with pytest.raises(BadRequestError, match="positive integer"):
            await svc.update_team(team_id=1, actor_user_id=42, avatar_id=-1)

    @pytest.mark.anyio
    async def test_rejects_non_string_name(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = _make_team(id=1)
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.OWNER)

        with pytest.raises(BadRequestError, match="must be string"):
            await svc.update_team(team_id=1, actor_user_id=42, name=12345)

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_team(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="team not found"):
            await svc.update_team(team_id=999, actor_user_id=42, name="X")


class TestTeamServiceDeleteTeam:
    @pytest.mark.anyio
    async def test_soft_deletes_team_and_members(self):
        svc, repo = _build_team_service()
        team = _make_team(id=1)
        repo.get_by_id.return_value = team
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.OWNER)

        members = [_make_relation(user_id=1), _make_relation(user_id=2)]
        repo.list_members_of_team.return_value = members

        await svc.delete_team(team_id=1, actor_user_id=42)

        # Members should have been soft-deleted
        for m in members:
            assert m.deleted_at is not None
            assert m.updated_at is not None

        repo.soft_delete_team.assert_awaited_once_with(team)

    @pytest.mark.anyio
    async def test_non_owner_cannot_delete(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = _make_team(id=1)
        repo.get_member_relation.return_value = _make_relation(role=TeamMemberRole.ADMIN)

        with pytest.raises(ForbiddenError, match="Only the team owner"):
            await svc.delete_team(team_id=1, actor_user_id=42)

    @pytest.mark.anyio
    async def test_non_member_cannot_delete(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = _make_team(id=1)
        repo.get_member_relation.return_value = None

        with pytest.raises(ForbiddenError):
            await svc.delete_team(team_id=1, actor_user_id=42)

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_team(self):
        svc, repo = _build_team_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await svc.delete_team(team_id=999, actor_user_id=42)


class TestTeamServiceRemoveMember:
    @pytest.mark.anyio
    async def test_member_can_leave_themselves(self):
        svc, repo = _build_team_service()
        relation = _make_relation(user_id=42, role=TeamMemberRole.MEMBER)
        repo.get_member_relation.return_value = relation

        await svc.remove_team_member(team_id=1, target_user_id=42, actor_user_id=42)

        repo.soft_delete_member.assert_awaited_once_with(relation)

    @pytest.mark.anyio
    async def test_admin_can_remove_member(self):
        svc, repo = _build_team_service()
        target_rel = _make_relation(user_id=50, role=TeamMemberRole.MEMBER)
        actor_rel = _make_relation(user_id=42, role=TeamMemberRole.ADMIN)
        repo.get_member_relation.side_effect = [target_rel, actor_rel]

        await svc.remove_team_member(team_id=1, target_user_id=50, actor_user_id=42)

        repo.soft_delete_member.assert_awaited_once_with(target_rel)

    @pytest.mark.anyio
    async def test_owner_can_remove_admin(self):
        svc, repo = _build_team_service()
        target_rel = _make_relation(user_id=50, role=TeamMemberRole.ADMIN)
        actor_rel = _make_relation(user_id=42, role=TeamMemberRole.OWNER)
        repo.get_member_relation.side_effect = [target_rel, actor_rel]

        await svc.remove_team_member(team_id=1, target_user_id=50, actor_user_id=42)

        repo.soft_delete_member.assert_awaited_once_with(target_rel)

    @pytest.mark.anyio
    async def test_cannot_remove_owner(self):
        svc, repo = _build_team_service()
        repo.get_member_relation.return_value = _make_relation(
            user_id=50, role=TeamMemberRole.OWNER
        )

        with pytest.raises(BadRequestError, match="owner cannot be removed"):
            await svc.remove_team_member(team_id=1, target_user_id=50, actor_user_id=42)

    @pytest.mark.anyio
    async def test_member_cannot_remove_others(self):
        svc, repo = _build_team_service()
        target_rel = _make_relation(user_id=50, role=TeamMemberRole.MEMBER)
        actor_rel = _make_relation(user_id=42, role=TeamMemberRole.MEMBER)
        repo.get_member_relation.side_effect = [target_rel, actor_rel]

        with pytest.raises(ForbiddenError, match="Only admins or owner"):
            await svc.remove_team_member(team_id=1, target_user_id=50, actor_user_id=42)

    @pytest.mark.anyio
    async def test_admin_cannot_remove_other_admin(self):
        svc, repo = _build_team_service()
        target_rel = _make_relation(user_id=50, role=TeamMemberRole.ADMIN)
        actor_rel = _make_relation(user_id=42, role=TeamMemberRole.ADMIN)
        repo.get_member_relation.side_effect = [target_rel, actor_rel]

        with pytest.raises(ForbiddenError, match="Admins cannot remove other admins"):
            await svc.remove_team_member(team_id=1, target_user_id=50, actor_user_id=42)

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_member(self):
        svc, repo = _build_team_service()
        repo.get_member_relation.return_value = None

        with pytest.raises(NotFoundError, match="team member not found"):
            await svc.remove_team_member(team_id=1, target_user_id=50, actor_user_id=42)


class TestTeamServiceUpdateMemberRole:
    @pytest.mark.anyio
    async def test_owner_can_promote_member_to_admin(self):
        svc, repo = _build_team_service()
        target = _make_relation(user_id=50, role=TeamMemberRole.MEMBER)
        actor = _make_relation(user_id=42, role=TeamMemberRole.OWNER)
        repo.get_member_relation.side_effect = [target, actor]

        await svc.update_team_member_role(
            team_id=1,
            target_user_id=50,
            actor_user_id=42,
            new_role=TeamMemberRole.ADMIN,
        )

        assert target.role == TeamMemberRole.ADMIN
        assert target.updated_at is not None

    @pytest.mark.anyio
    async def test_owner_can_demote_admin_to_member(self):
        svc, repo = _build_team_service()
        target = _make_relation(user_id=50, role=TeamMemberRole.ADMIN)
        actor = _make_relation(user_id=42, role=TeamMemberRole.OWNER)
        repo.get_member_relation.side_effect = [target, actor]

        await svc.update_team_member_role(
            team_id=1,
            target_user_id=50,
            actor_user_id=42,
            new_role=TeamMemberRole.MEMBER,
        )

        assert target.role == TeamMemberRole.MEMBER

    @pytest.mark.anyio
    async def test_cannot_change_owner_role(self):
        svc, repo = _build_team_service()
        repo.get_member_relation.return_value = _make_relation(
            user_id=50, role=TeamMemberRole.OWNER
        )

        with pytest.raises(BadRequestError, match="owner transfer flow"):
            await svc.update_team_member_role(
                team_id=1,
                target_user_id=50,
                actor_user_id=42,
                new_role=TeamMemberRole.ADMIN,
            )

    @pytest.mark.anyio
    async def test_cannot_promote_to_owner_directly(self):
        svc, repo = _build_team_service()
        target = _make_relation(user_id=50, role=TeamMemberRole.ADMIN)
        repo.get_member_relation.return_value = target

        with pytest.raises(BadRequestError, match="Cannot promote directly to owner"):
            await svc.update_team_member_role(
                team_id=1,
                target_user_id=50,
                actor_user_id=42,
                new_role=TeamMemberRole.OWNER,
            )

    @pytest.mark.anyio
    async def test_non_owner_cannot_change_roles(self):
        svc, repo = _build_team_service()
        target = _make_relation(user_id=50, role=TeamMemberRole.MEMBER)
        actor = _make_relation(user_id=42, role=TeamMemberRole.ADMIN)
        repo.get_member_relation.side_effect = [target, actor]

        with pytest.raises(ForbiddenError, match="Only team owner"):
            await svc.update_team_member_role(
                team_id=1,
                target_user_id=50,
                actor_user_id=42,
                new_role=TeamMemberRole.ADMIN,
            )

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_member(self):
        svc, repo = _build_team_service()
        repo.get_member_relation.return_value = None

        with pytest.raises(NotFoundError, match="team member not found"):
            await svc.update_team_member_role(
                team_id=1,
                target_user_id=50,
                actor_user_id=42,
                new_role=TeamMemberRole.ADMIN,
            )


class TestTeamServiceTransferOwner:
    @pytest.mark.anyio
    async def test_transfers_ownership(self):
        svc, repo = _build_team_service()
        owner_rel = _make_relation(user_id=42, role=TeamMemberRole.OWNER)
        target_rel = _make_relation(user_id=50, role=TeamMemberRole.ADMIN)
        repo.get_member_relation.side_effect = [owner_rel, target_rel]

        await svc.transfer_team_owner(team_id=1, new_owner_user_id=50, actor_user_id=42)

        assert owner_rel.role == TeamMemberRole.ADMIN
        assert target_rel.role == TeamMemberRole.OWNER

    @pytest.mark.anyio
    async def test_non_owner_cannot_transfer(self):
        svc, repo = _build_team_service()
        repo.get_member_relation.return_value = _make_relation(
            user_id=42, role=TeamMemberRole.ADMIN
        )

        with pytest.raises(ForbiddenError, match="Only current owner"):
            await svc.transfer_team_owner(team_id=1, new_owner_user_id=50, actor_user_id=42)

    @pytest.mark.anyio
    async def test_non_member_cannot_transfer(self):
        svc, repo = _build_team_service()
        repo.get_member_relation.return_value = None

        with pytest.raises(ForbiddenError):
            await svc.transfer_team_owner(team_id=1, new_owner_user_id=50, actor_user_id=42)

    @pytest.mark.anyio
    async def test_target_must_be_existing_member(self):
        svc, repo = _build_team_service()
        owner_rel = _make_relation(user_id=42, role=TeamMemberRole.OWNER)
        repo.get_member_relation.side_effect = [owner_rel, None]

        with pytest.raises(NotFoundError, match="existing team member"):
            await svc.transfer_team_owner(team_id=1, new_owner_user_id=999, actor_user_id=42)


# ===================================================================
# TeamMembershipService tests
# ===================================================================

# All membership_services methods call publish_notification_event, so we
# patch it globally for all membership tests.
_PUBLISH_PATH = "app.domain.team.membership_services.publish_notification_event"


class TestCreateJoinRequest:
    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_creates_join_request(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.get_by_id.return_value = _make_team(id=1)
        team_repo.is_team_member.return_value = False
        app_repo.exists_pending_for_user_and_team.return_value = False
        team_repo.list_admin_and_owner_ids.return_value = {10, 11}

        saved_app = _make_application(id=300)
        app_repo.save.return_value = saved_app

        result = await svc.create_team_join_request(user_id=42, team_id=1, message="Please add me")

        assert result is saved_app
        app_repo.save.assert_awaited_once()
        mock_publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_team(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="team not found"):
            await svc.create_team_join_request(user_id=42, team_id=999, message=None)

    @pytest.mark.anyio
    async def test_raises_conflict_if_already_member(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.get_by_id.return_value = _make_team(id=1)
        team_repo.is_team_member.return_value = True

        with pytest.raises(ConflictError, match="already a member"):
            await svc.create_team_join_request(user_id=42, team_id=1, message=None)

    @pytest.mark.anyio
    async def test_raises_conflict_if_pending_application_exists(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.get_by_id.return_value = _make_team(id=1)
        team_repo.is_team_member.return_value = False
        app_repo.exists_pending_for_user_and_team.return_value = True

        with pytest.raises(ConflictError, match="pending application"):
            await svc.create_team_join_request(user_id=42, team_id=1, message=None)

    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_skips_notification_when_no_admins(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.get_by_id.return_value = _make_team(id=1)
        team_repo.is_team_member.return_value = False
        app_repo.exists_pending_for_user_and_team.return_value = False
        team_repo.list_admin_and_owner_ids.return_value = set()
        app_repo.save.return_value = _make_application(id=300)

        await svc.create_team_join_request(user_id=42, team_id=1, message=None)

        mock_publish.assert_not_awaited()


class TestCancelJoinRequest:
    @pytest.mark.anyio
    async def test_cancels_own_request(self):
        svc, team_repo, app_repo = _build_membership_service()
        app = _make_application(id=200)
        app_repo.find_pending_by_id_and_initiator_and_type.return_value = app
        app_repo.save.return_value = app

        await svc.cancel_my_join_request(user_id=42, request_id=200)

        assert app.status == ApplicationStatus.CANCELED.value
        assert app.processed_by_id == 42
        app_repo.save.assert_awaited_once()

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_request(self):
        svc, team_repo, app_repo = _build_membership_service()
        app_repo.find_pending_by_id_and_initiator_and_type.return_value = None

        with pytest.raises(NotFoundError, match="Pending request"):
            await svc.cancel_my_join_request(user_id=42, request_id=999)


class TestCreateInvitation:
    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_creates_invitation(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        team_repo.is_team_member.return_value = False
        app_repo.exists_pending_for_user_and_team.return_value = False

        saved = _make_application(
            id=300,
            type=ApplicationType.INVITATION.value,
            initiator_id=42,
            user_id=50,
        )
        app_repo.save.return_value = saved

        result = await svc.create_team_invitation(
            initiator_user_id=42,
            team_id=1,
            user_id_to_invite=50,
            role=None,
            message="Join us!",
        )

        assert result is saved
        mock_publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_raises_forbidden_for_non_admin(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = False

        with pytest.raises(ForbiddenError, match="not authorized to invite"):
            await svc.create_team_invitation(
                initiator_user_id=42,
                team_id=1,
                user_id_to_invite=50,
                role=None,
                message=None,
            )

    @pytest.mark.anyio
    async def test_raises_conflict_if_user_already_member(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        team_repo.is_team_member.return_value = True

        with pytest.raises(ConflictError, match="already a member"):
            await svc.create_team_invitation(
                initiator_user_id=42,
                team_id=1,
                user_id_to_invite=50,
                role=None,
                message=None,
            )

    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_invitation_role_mapping_admin(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        team_repo.is_team_member.return_value = False
        app_repo.exists_pending_for_user_and_team.return_value = False

        saved = _make_application(id=300, role="ADMIN")
        app_repo.save.return_value = saved

        await svc.create_team_invitation(
            initiator_user_id=42,
            team_id=1,
            user_id_to_invite=50,
            role=TeamMemberRole.ADMIN,
            message=None,
        )

        # Inspect the application that was passed to save
        create_arg = app_repo.save.call_args[0][0]
        assert create_arg.role == "ADMIN"

    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_invitation_role_mapping_owner(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        team_repo.is_team_member.return_value = False
        app_repo.exists_pending_for_user_and_team.return_value = False

        saved = _make_application(id=300, role="OWNER")
        app_repo.save.return_value = saved

        await svc.create_team_invitation(
            initiator_user_id=42,
            team_id=1,
            user_id_to_invite=50,
            role=TeamMemberRole.OWNER,
            message=None,
        )

        create_arg = app_repo.save.call_args[0][0]
        assert create_arg.role == "OWNER"


class TestAcceptInvitation:
    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_accepts_invitation_and_adds_member(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        app = _make_application(
            id=200,
            type=ApplicationType.INVITATION.value,
            user_id=50,
            team_id=1,
            initiator_id=42,
            role="MEMBER",
        )
        app_repo.find_pending_by_id_and_user_and_type.return_value = app
        app_repo.save.return_value = app
        team_repo.is_team_member.return_value = False

        await svc.accept_team_invitation(user_id=50, invitation_id=200)

        assert app.status == ApplicationStatus.ACCEPTED.value
        team_repo.add_member.assert_awaited_once_with(1, 50, TeamMemberRole.MEMBER)
        mock_publish.assert_awaited_once()

    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_accepts_admin_invitation(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        app = _make_application(
            id=200,
            type=ApplicationType.INVITATION.value,
            user_id=50,
            team_id=1,
            initiator_id=42,
            role="ADMIN",
        )
        app_repo.find_pending_by_id_and_user_and_type.return_value = app
        app_repo.save.return_value = app
        team_repo.is_team_member.return_value = False

        await svc.accept_team_invitation(user_id=50, invitation_id=200)

        team_repo.add_member.assert_awaited_once_with(1, 50, TeamMemberRole.ADMIN)

    @pytest.mark.anyio
    async def test_raises_not_found_if_no_pending_invitation(self):
        svc, team_repo, app_repo = _build_membership_service()
        app_repo.find_pending_by_id_and_user_and_type.return_value = None

        with pytest.raises(NotFoundError, match="Pending invitation"):
            await svc.accept_team_invitation(user_id=50, invitation_id=999)

    @pytest.mark.anyio
    async def test_raises_bad_request_if_already_member(self):
        svc, team_repo, app_repo = _build_membership_service()
        app = _make_application(
            id=200, type=ApplicationType.INVITATION.value, user_id=50, team_id=1
        )
        app_repo.find_pending_by_id_and_user_and_type.return_value = app
        team_repo.is_team_member.return_value = True

        with pytest.raises(BadRequestError, match="already a member"):
            await svc.accept_team_invitation(user_id=50, invitation_id=200)


class TestDeclineInvitation:
    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_declines_invitation(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        app = _make_application(
            id=200,
            type=ApplicationType.INVITATION.value,
            user_id=50,
            initiator_id=42,
            team_id=1,
        )
        app_repo.find_pending_by_id_and_user_and_type.return_value = app
        app_repo.save.return_value = app

        await svc.decline_team_invitation(user_id=50, invitation_id=200)

        assert app.status == ApplicationStatus.DECLINED.value
        assert app.processed_by_id == 50
        mock_publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_invitation(self):
        svc, team_repo, app_repo = _build_membership_service()
        app_repo.find_pending_by_id_and_user_and_type.return_value = None

        with pytest.raises(NotFoundError, match="Pending invitation"):
            await svc.decline_team_invitation(user_id=50, invitation_id=999)


class TestApproveJoinRequest:
    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_approves_request_and_adds_member(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        app = _make_application(id=200, user_id=50, team_id=1)
        app_repo.find_pending_by_id_and_team_and_type.return_value = app
        app_repo.save.return_value = app
        team_repo.is_team_member.return_value = False

        await svc.approve_team_join_request(approver_user_id=42, team_id=1, request_id=200)

        assert app.status == ApplicationStatus.APPROVED.value
        team_repo.add_member.assert_awaited_once_with(1, 50, TeamMemberRole.MEMBER)
        mock_publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_raises_forbidden_for_non_admin(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = False

        with pytest.raises(ForbiddenError, match="not authorized to approve"):
            await svc.approve_team_join_request(approver_user_id=42, team_id=1, request_id=200)

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_request(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        app_repo.find_pending_by_id_and_team_and_type.return_value = None

        with pytest.raises(NotFoundError, match="Pending request"):
            await svc.approve_team_join_request(approver_user_id=42, team_id=1, request_id=999)

    @pytest.mark.anyio
    async def test_raises_bad_request_if_already_member(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        app = _make_application(id=200, user_id=50, team_id=1)
        app_repo.find_pending_by_id_and_team_and_type.return_value = app
        team_repo.is_team_member.return_value = True

        with pytest.raises(BadRequestError, match="already a member"):
            await svc.approve_team_join_request(approver_user_id=42, team_id=1, request_id=200)


class TestRejectJoinRequest:
    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_rejects_request(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        app = _make_application(id=200, user_id=50, team_id=1)
        app_repo.find_pending_by_id_and_team_and_type.return_value = app
        app_repo.save.return_value = app

        await svc.reject_team_join_request(rejector_user_id=42, team_id=1, request_id=200)

        assert app.status == ApplicationStatus.REJECTED.value
        assert app.processed_by_id == 42
        mock_publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_raises_forbidden_for_non_admin(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = False

        with pytest.raises(ForbiddenError, match="not authorized to reject"):
            await svc.reject_team_join_request(rejector_user_id=42, team_id=1, request_id=200)

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_request(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        app_repo.find_pending_by_id_and_team_and_type.return_value = None

        with pytest.raises(NotFoundError, match="Pending request"):
            await svc.reject_team_join_request(rejector_user_id=42, team_id=1, request_id=999)


class TestCancelInvitation:
    @pytest.mark.anyio
    @patch(_PUBLISH_PATH, new_callable=AsyncMock)
    async def test_cancels_invitation(self, mock_publish):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        app = _make_application(
            id=200,
            type=ApplicationType.INVITATION.value,
            user_id=50,
            team_id=1,
        )
        app_repo.find_pending_by_id_and_team_and_type.return_value = app
        app_repo.save.return_value = app

        await svc.cancel_team_invitation(canceler_user_id=42, team_id=1, invitation_id=200)

        assert app.status == ApplicationStatus.CANCELED.value
        assert app.processed_by_id == 42
        mock_publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_raises_forbidden_for_non_admin(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = False

        with pytest.raises(ForbiddenError, match="not authorized to cancel"):
            await svc.cancel_team_invitation(canceler_user_id=42, team_id=1, invitation_id=200)

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_invitation(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        app_repo.find_pending_by_id_and_team_and_type.return_value = None

        with pytest.raises(NotFoundError, match="Pending invitation"):
            await svc.cancel_team_invitation(canceler_user_id=42, team_id=1, invitation_id=999)


class TestListMyInvitations:
    @pytest.mark.anyio
    async def test_returns_invitations_with_pagination(self):
        svc, team_repo, app_repo = _build_membership_service()
        apps = [_make_application(id=i) for i in range(3)]
        app_repo.list_for_user.return_value = (apps, 10)

        result, page = await svc.list_my_invitations(
            user_id=42, status=None, page_start=0, page_size=3
        )

        assert result == apps
        assert page["pageStart"] == 0
        assert page["pageSize"] == 3
        assert page["total"] == 10
        assert page["hasMore"] is True
        assert page["nextStart"] == 3

    @pytest.mark.anyio
    async def test_has_more_false_at_end(self):
        svc, team_repo, app_repo = _build_membership_service()
        apps = [_make_application(id=1)]
        app_repo.list_for_user.return_value = (apps, 1)

        _, page = await svc.list_my_invitations(user_id=42, status=None, page_start=0, page_size=20)

        assert page["hasMore"] is False
        assert page["nextStart"] is None

    @pytest.mark.anyio
    async def test_defaults_page_size_and_offset(self):
        svc, team_repo, app_repo = _build_membership_service()
        app_repo.list_for_user.return_value = ([], 0)

        await svc.list_my_invitations(user_id=42, status=None, page_start=None, page_size=None)

        app_repo.list_for_user.assert_awaited_once_with(
            user_id=42,
            type_=ApplicationType.INVITATION,
            status=None,
            limit=20,
            offset=0,
        )

    @pytest.mark.anyio
    async def test_passes_status_filter(self):
        svc, team_repo, app_repo = _build_membership_service()
        app_repo.list_for_user.return_value = ([], 0)

        await svc.list_my_invitations(
            user_id=42,
            status=ApplicationStatus.PENDING,
            page_start=0,
            page_size=5,
        )

        app_repo.list_for_user.assert_awaited_once_with(
            user_id=42,
            type_=ApplicationType.INVITATION,
            status=ApplicationStatus.PENDING,
            limit=5,
            offset=0,
        )


class TestListMyJoinRequests:
    @pytest.mark.anyio
    async def test_returns_requests_with_pagination(self):
        svc, team_repo, app_repo = _build_membership_service()
        apps = [_make_application(id=i) for i in range(2)]
        app_repo.list_for_user.return_value = (apps, 5)

        result, page = await svc.list_my_join_requests(
            user_id=42, status=None, page_start=0, page_size=2
        )

        assert result == apps
        assert page["total"] == 5
        assert page["hasMore"] is True

    @pytest.mark.anyio
    async def test_uses_request_type(self):
        svc, team_repo, app_repo = _build_membership_service()
        app_repo.list_for_user.return_value = ([], 0)

        await svc.list_my_join_requests(user_id=42, status=None, page_start=None, page_size=None)

        app_repo.list_for_user.assert_awaited_once_with(
            user_id=42,
            type_=ApplicationType.REQUEST,
            status=None,
            limit=20,
            offset=0,
        )


class TestListTeamJoinRequests:
    @pytest.mark.anyio
    async def test_returns_requests_for_admin(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        apps = [_make_application(id=1)]
        app_repo.list_for_team.return_value = (apps, 1)

        result, page = await svc.list_team_join_requests(
            requesting_user_id=42,
            team_id=1,
            status=None,
            page_start=0,
            page_size=10,
        )

        assert result == apps
        assert page["total"] == 1

    @pytest.mark.anyio
    async def test_raises_forbidden_for_non_admin(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = False

        with pytest.raises(ForbiddenError, match="not authorized to view requests"):
            await svc.list_team_join_requests(
                requesting_user_id=42,
                team_id=1,
                status=None,
                page_start=None,
                page_size=None,
            )


class TestListTeamInvitations:
    @pytest.mark.anyio
    async def test_returns_invitations_for_admin(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        apps = [_make_application(id=1)]
        app_repo.list_for_team.return_value = (apps, 1)

        result, page = await svc.list_team_invitations(
            requesting_user_id=42,
            team_id=1,
            status=None,
            page_start=0,
            page_size=10,
        )

        assert result == apps
        assert page["total"] == 1

    @pytest.mark.anyio
    async def test_raises_forbidden_for_non_admin(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = False

        with pytest.raises(ForbiddenError, match="not authorized to view invitations"):
            await svc.list_team_invitations(
                requesting_user_id=42,
                team_id=1,
                status=None,
                page_start=None,
                page_size=None,
            )

    @pytest.mark.anyio
    async def test_uses_invitation_type(self):
        svc, team_repo, app_repo = _build_membership_service()
        team_repo.is_team_at_least_admin.return_value = True
        app_repo.list_for_team.return_value = ([], 0)

        await svc.list_team_invitations(
            requesting_user_id=42,
            team_id=1,
            status=ApplicationStatus.PENDING,
            page_start=5,
            page_size=10,
        )

        app_repo.list_for_team.assert_awaited_once_with(
            team_id=1,
            type_=ApplicationType.INVITATION,
            status=ApplicationStatus.PENDING,
            limit=10,
            offset=5,
        )
