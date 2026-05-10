from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.domain.groups.services import (
    GroupQuestionService,
    GroupsService,
    GroupTargetService,
    _date_to_ms,
    _target_to_dto,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _group(*, id: int = 1, name: str = "Study Group", **kw):
    defaults = {
        "id": id,
        "name": name,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _profile(*, group_id: int = 1, intro: str = "Welcome!", avatar_id: int | None = 42, **kw):
    defaults = {
        "id": 100,
        "group_id": group_id,
        "intro": intro,
        "avatar_id": avatar_id,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _user_profile(*, nickname: str = "Alice", avatar_id: int | None = 7, **kw):
    defaults = {
        "nickname": nickname,
        "avatar_id": avatar_id,
        "intro": "Hello",
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _membership(*, member_id: int, role: str = "MEMBER"):
    return SimpleNamespace(member_id=member_id, role=role)


def _make_service(
    repo=None,
    profile_repo=None,
    membership_repo=None,
    user_profile_repo=None,
):
    return GroupsService(
        repo=repo or AsyncMock(),
        profile_repo=profile_repo or AsyncMock(),
        membership_repo=membership_repo or AsyncMock(),
        user_profile_repo=user_profile_repo or AsyncMock(),
    )


# ---------------------------------------------------------------------------
# create_group
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_group_success():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    group = _group(id=10, name="Chess Club")
    profile = _profile(group_id=10, intro="We play chess", avatar_id=5)
    user_prof = _user_profile(nickname="Bob", avatar_id=3)

    repo.create_group.return_value = group
    profile_repo.create_profile.return_value = profile
    user_profile_repo.get_profile_by_user_id.return_value = user_prof

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.create_group(
        user_id=1, name="Chess Club", intro="We play chess", avatar_id=5
    )

    repo.create_group.assert_awaited_once_with(name="Chess Club")
    profile_repo.create_profile.assert_awaited_once_with(
        group_id=10, intro="We play chess", avatar_id=5
    )
    membership_repo.add_member.assert_awaited_once_with(group_id=10, member_id=1, role="OWNER")
    assert result["id"] == 10
    assert result["name"] == "Chess Club"
    assert result["intro"] == "We play chess"
    assert result["member_count"] == 1
    assert result["is_member"] is True
    assert result["is_owner"] is True
    assert result["owner"]["id"] == 1
    assert result["owner"]["nickname"] == "Bob"


@pytest.mark.anyio
async def test_create_group_strips_name():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    group = _group(id=11, name="Trimmed")
    repo.create_group.return_value = group
    profile_repo.create_profile.return_value = _profile(group_id=11)
    user_profile_repo.get_profile_by_user_id.return_value = None

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.create_group(user_id=2, name="  Trimmed  ", intro="test", avatar_id=None)

    repo.create_group.assert_awaited_once_with(name="Trimmed")
    # When user profile is None, owner should still have the id
    assert result["owner"]["id"] == 2
    assert "nickname" not in result["owner"]


@pytest.mark.anyio
async def test_create_group_empty_name_raises():
    svc = _make_service()

    with pytest.raises(BadRequestError, match="name cannot be empty"):
        await svc.create_group(user_id=1, name="   ", intro="hi", avatar_id=None)


@pytest.mark.anyio
async def test_create_group_whitespace_only_name_raises():
    svc = _make_service()

    with pytest.raises(BadRequestError):
        await svc.create_group(user_id=1, name="", intro="hi", avatar_id=None)


# ---------------------------------------------------------------------------
# get_group
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_group_success_anonymous():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    group = _group(id=5, name="Readers")
    profile = _profile(group_id=5, intro="Book club")

    repo.get_by_id.return_value = group
    profile_repo.get_by_group_id.return_value = profile
    membership_repo.count_members.return_value = 3
    membership_repo.get_owner_id.return_value = 99
    user_profile_repo.get_profile_by_user_id.return_value = _user_profile(nickname="Owner")

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=5, user_id=None)

    assert result["id"] == 5
    assert result["name"] == "Readers"
    assert result["intro"] == "Book club"
    assert result["member_count"] == 3
    assert result["is_member"] is False
    assert result["is_owner"] is False
    assert result["owner"]["id"] == 99
    assert result["owner"]["nickname"] == "Owner"


@pytest.mark.anyio
async def test_get_group_as_member():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    profile_repo.get_by_group_id.return_value = _profile(group_id=5)
    membership_repo.count_members.return_value = 2
    membership_repo.get_owner_id.return_value = 10
    membership_repo.get_member_role.return_value = "MEMBER"
    user_profile_repo.get_profile_by_user_id.return_value = _user_profile()

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=5, user_id=20)

    assert result["is_member"] is True
    assert result["is_owner"] is False


@pytest.mark.anyio
async def test_get_group_as_owner():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    profile_repo.get_by_group_id.return_value = _profile(group_id=5)
    membership_repo.count_members.return_value = 1
    membership_repo.get_owner_id.return_value = 20
    membership_repo.get_member_role.return_value = "OWNER"
    user_profile_repo.get_profile_by_user_id.return_value = _user_profile()

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=5, user_id=20)

    assert result["is_member"] is True
    assert result["is_owner"] is True


@pytest.mark.anyio
async def test_get_group_not_found():
    repo = AsyncMock()
    repo.get_by_id.return_value = None

    svc = _make_service(repo=repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.get_group(group_id=999)


@pytest.mark.anyio
async def test_get_group_no_profile():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    profile_repo.get_by_group_id.return_value = None
    membership_repo.count_members.return_value = 0
    membership_repo.get_owner_id.return_value = None

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=5)

    assert result["intro"] == ""
    assert result["avatarId"] is None
    assert result["owner"] is None


# ---------------------------------------------------------------------------
# update_group
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_group_as_owner():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    group = _group(id=5, name="Old Name")
    profile = _profile(group_id=5, intro="Old intro")

    repo.get_by_id.return_value = group
    repo.exists_by_name.return_value = False
    membership_repo.get_member_role.return_value = "OWNER"
    profile_repo.get_by_group_id.return_value = profile
    membership_repo.count_members.return_value = 3
    membership_repo.get_owner_id.return_value = 1
    user_profile_repo.get_profile_by_user_id.return_value = _user_profile()

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.update_group(group_id=5, user_id=1, name="New Name", intro="New intro")

    repo.update_group.assert_awaited_once_with(group, name="New Name")
    profile_repo.update_profile.assert_awaited_once_with(profile, intro="New intro", avatar_id=None)
    assert result["is_owner"] is True
    assert result["is_member"] is True


@pytest.mark.anyio
async def test_update_group_as_admin():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    group = _group(id=5)
    repo.get_by_id.return_value = group
    membership_repo.get_member_role.return_value = "ADMIN"
    profile_repo.get_by_group_id.return_value = _profile(group_id=5)
    membership_repo.count_members.return_value = 2
    membership_repo.get_owner_id.return_value = 10
    user_profile_repo.get_profile_by_user_id.return_value = _user_profile()

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.update_group(group_id=5, user_id=2, intro="Updated")

    repo.update_group.assert_awaited_once()
    assert result["is_member"] is True
    assert result["is_owner"] is False


@pytest.mark.anyio
async def test_update_group_not_found():
    repo = AsyncMock()
    repo.get_by_id.return_value = None

    svc = _make_service(repo=repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.update_group(group_id=999, user_id=1, name="X")


@pytest.mark.anyio
async def test_update_group_not_owner_or_admin():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "MEMBER"

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError, match="Only owners and admins"):
        await svc.update_group(group_id=5, user_id=99, name="X")


@pytest.mark.anyio
async def test_update_group_non_member_forbidden():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = None

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError):
        await svc.update_group(group_id=5, user_id=99, name="X")


@pytest.mark.anyio
async def test_update_group_duplicate_name():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "OWNER"
    repo.exists_by_name.return_value = True

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ConflictError, match="Group name already exists"):
        await svc.update_group(group_id=5, user_id=1, name="Duplicate")


@pytest.mark.anyio
async def test_update_group_no_profile():
    """When the group has no profile row, update_profile should not be called."""
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "OWNER"
    profile_repo.get_by_group_id.return_value = None
    membership_repo.count_members.return_value = 1
    membership_repo.get_owner_id.return_value = 1
    user_profile_repo.get_profile_by_user_id.return_value = _user_profile()

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.update_group(group_id=5, user_id=1, intro="Something")

    profile_repo.update_profile.assert_not_awaited()
    assert result["intro"] == ""


# ---------------------------------------------------------------------------
# delete_group
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_group_success():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    group = _group(id=5)
    repo.get_by_id.return_value = group
    membership_repo.get_member_role.return_value = "OWNER"

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    await svc.delete_group(group_id=5, user_id=1)

    repo.soft_delete.assert_awaited_once_with(group)


@pytest.mark.anyio
async def test_delete_group_not_found():
    repo = AsyncMock()
    repo.get_by_id.return_value = None

    svc = _make_service(repo=repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.delete_group(group_id=999, user_id=1)


@pytest.mark.anyio
async def test_delete_group_not_owner():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "ADMIN"

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError, match="Only the owner can delete"):
        await svc.delete_group(group_id=5, user_id=2)


@pytest.mark.anyio
async def test_delete_group_member_forbidden():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "MEMBER"

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError):
        await svc.delete_group(group_id=5, user_id=3)


@pytest.mark.anyio
async def test_delete_group_non_member_forbidden():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = None

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError):
        await svc.delete_group(group_id=5, user_id=4)


# ---------------------------------------------------------------------------
# join_group
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_join_group_success():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.is_member.return_value = False
    membership_repo.count_members.return_value = 4

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    result = await svc.join_group(group_id=5, user_id=10)

    membership_repo.add_member.assert_awaited_once_with(group_id=5, member_id=10, role="MEMBER")
    assert result["memberCount"] == 4


@pytest.mark.anyio
async def test_join_group_not_found():
    repo = AsyncMock()
    repo.get_by_id.return_value = None

    svc = _make_service(repo=repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.join_group(group_id=999, user_id=10)


@pytest.mark.anyio
async def test_join_group_already_member():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.is_member.return_value = True

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ConflictError, match="Already a member"):
        await svc.join_group(group_id=5, user_id=10)


# ---------------------------------------------------------------------------
# leave_group
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_leave_group_success():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "MEMBER"
    membership_repo.count_members.return_value = 2

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    result = await svc.leave_group(group_id=5, user_id=10)

    membership_repo.remove_member.assert_awaited_once_with(group_id=5, member_id=10)
    assert result["memberCount"] == 2


@pytest.mark.anyio
async def test_leave_group_admin_can_leave():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "ADMIN"
    membership_repo.count_members.return_value = 3

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    result = await svc.leave_group(group_id=5, user_id=10)

    membership_repo.remove_member.assert_awaited_once()
    assert result["memberCount"] == 3


@pytest.mark.anyio
async def test_leave_group_not_found():
    repo = AsyncMock()
    repo.get_by_id.return_value = None

    svc = _make_service(repo=repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.leave_group(group_id=999, user_id=10)


@pytest.mark.anyio
async def test_leave_group_not_a_member():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = None

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ConflictError, match="Not a member"):
        await svc.leave_group(group_id=5, user_id=10)


@pytest.mark.anyio
async def test_leave_group_owner_forbidden():
    repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "OWNER"

    svc = _make_service(repo=repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError, match="Owner cannot leave"):
        await svc.leave_group(group_id=5, user_id=1)


# ---------------------------------------------------------------------------
# list_groups
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_groups_returns_items_and_page():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()

    g1 = _group(id=1, name="A")
    g2 = _group(id=2, name="B")
    repo.search.return_value = ([g1, g2], 5)
    profile_repo.get_profiles_by_group_ids.return_value = {
        1: _profile(group_id=1, intro="A intro"),
        2: _profile(group_id=2, intro="B intro"),
    }
    membership_repo.count_members.side_effect = [3, 7]

    svc = _make_service(repo=repo, profile_repo=profile_repo, membership_repo=membership_repo)

    items, page = await svc.list_groups(keyword="test", page_start=0, page_size=2)

    repo.search.assert_awaited_once_with(
        keyword="test",
        limit=2,
        offset=0,
        user_id=None,
        joined=None,
        managed=None,
    )
    assert len(items) == 2
    assert items[0]["id"] == 1
    assert items[0]["member_count"] == 3
    assert items[1]["id"] == 2
    assert items[1]["member_count"] == 7
    assert page["total"] == 5
    assert page["hasMore"] is True
    assert page["nextStart"] == 2


@pytest.mark.anyio
async def test_list_groups_no_more_pages():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()

    g1 = _group(id=1, name="A")
    repo.search.return_value = ([g1], 1)
    profile_repo.get_profiles_by_group_ids.return_value = {
        1: _profile(group_id=1),
    }
    membership_repo.count_members.return_value = 1

    svc = _make_service(repo=repo, profile_repo=profile_repo, membership_repo=membership_repo)

    items, page = await svc.list_groups(keyword=None, page_start=None, page_size=10)

    assert len(items) == 1
    assert page["hasMore"] is False
    assert page["nextStart"] is None
    assert page["pageStart"] == 0


@pytest.mark.anyio
async def test_list_groups_empty():
    repo = AsyncMock()
    profile_repo = AsyncMock()

    repo.search.return_value = ([], 0)
    profile_repo.get_profiles_by_group_ids.return_value = {}

    svc = _make_service(repo=repo, profile_repo=profile_repo)

    items, page = await svc.list_groups(keyword="nonexistent", page_start=0, page_size=10)

    assert items == []
    assert page["total"] == 0
    assert page["hasMore"] is False
    assert page["nextStart"] is None


@pytest.mark.anyio
async def test_list_groups_with_user_filters():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()

    repo.search.return_value = ([], 0)
    profile_repo.get_profiles_by_group_ids.return_value = {}

    svc = _make_service(repo=repo, profile_repo=profile_repo, membership_repo=membership_repo)

    await svc.list_groups(
        keyword=None,
        page_start=0,
        page_size=10,
        user_id=5,
        joined=True,
        managed=False,
    )

    repo.search.assert_awaited_once_with(
        keyword=None,
        limit=10,
        offset=0,
        user_id=5,
        joined=True,
        managed=False,
    )


@pytest.mark.anyio
async def test_list_groups_profile_missing_for_some():
    """Groups without a profile should still appear with empty intro / None avatarId."""
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()

    g1 = _group(id=1, name="With Profile")
    g2 = _group(id=2, name="No Profile")
    repo.search.return_value = ([g1, g2], 2)
    profile_repo.get_profiles_by_group_ids.return_value = {
        1: _profile(group_id=1, intro="Has one"),
    }
    membership_repo.count_members.side_effect = [1, 1]

    svc = _make_service(repo=repo, profile_repo=profile_repo, membership_repo=membership_repo)

    items, _ = await svc.list_groups(keyword=None, page_start=0, page_size=10)

    assert items[0]["intro"] == "Has one"
    assert items[1]["intro"] == ""
    assert items[1]["avatarId"] is None


# ---------------------------------------------------------------------------
# list_members
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_members_success():
    repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)

    m1 = _membership(member_id=10, role="OWNER")
    m2 = _membership(member_id=20, role="MEMBER")
    membership_repo.list_members_cursor.return_value = ([m1, m2], None, 30)

    user_profile_repo.get_profiles_by_user_ids.return_value = {
        10: _user_profile(nickname="Owner", avatar_id=1),
        20: _user_profile(nickname="Member", avatar_id=2),
    }

    svc = _make_service(
        repo=repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    members, page = await svc.list_members(group_id=5, page_start=None, page_size=10)

    assert len(members) == 2
    assert members[0]["id"] == 10
    assert members[0]["nickname"] == "Owner"
    assert members[0]["role"] == "OWNER"
    assert members[1]["id"] == 20
    assert members[1]["nickname"] == "Member"
    assert page["pageSize"] == 2
    assert page["hasMore"] is True
    assert page["nextStart"] == 30


@pytest.mark.anyio
async def test_list_members_group_not_found():
    repo = AsyncMock()
    repo.get_by_id.return_value = None

    svc = _make_service(repo=repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.list_members(group_id=999, page_start=None, page_size=10)


@pytest.mark.anyio
async def test_list_members_zero_page_size():
    repo = AsyncMock()
    repo.get_by_id.return_value = _group(id=5)

    svc = _make_service(repo=repo)

    members, page = await svc.list_members(group_id=5, page_start=None, page_size=0)

    assert members == []
    assert page["pageSize"] == 0
    assert page["hasMore"] is False


@pytest.mark.anyio
async def test_list_members_no_profile_for_member():
    """Members without a user profile should show empty nickname / None avatarId."""
    repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)

    m1 = _membership(member_id=10, role="MEMBER")
    membership_repo.list_members_cursor.return_value = ([m1], None, None)
    user_profile_repo.get_profiles_by_user_ids.return_value = {}

    svc = _make_service(
        repo=repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    members, page = await svc.list_members(group_id=5, page_start=None, page_size=10)

    assert members[0]["nickname"] == ""
    assert members[0]["avatarId"] is None
    assert members[0]["intro"] == ""
    assert page["hasMore"] is False


# ---------------------------------------------------------------------------
# _build_owner_dto (tested indirectly via get_group)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_owner_dto_no_owner():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    profile_repo.get_by_group_id.return_value = _profile(group_id=5)
    membership_repo.count_members.return_value = 0
    membership_repo.get_owner_id.return_value = None

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=5)

    assert result["owner"] is None
    user_profile_repo.get_profile_by_user_id.assert_not_awaited()


@pytest.mark.anyio
async def test_build_owner_dto_owner_no_user_profile():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    profile_repo.get_by_group_id.return_value = _profile(group_id=5)
    membership_repo.count_members.return_value = 1
    membership_repo.get_owner_id.return_value = 42
    user_profile_repo.get_profile_by_user_id.return_value = None

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=5)

    assert result["owner"] == {"id": 42}


# ---------------------------------------------------------------------------
# _group_to_dto (tested indirectly via get_group)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_group_to_dto_timestamps():
    """Timestamps should be converted to epoch milliseconds."""
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    ts = datetime(2026, 6, 15, 10, 30, 0, tzinfo=UTC)
    group = _group(id=1, created_at=ts, updated_at=ts)

    repo.get_by_id.return_value = group
    profile_repo.get_by_group_id.return_value = _profile(group_id=1)
    membership_repo.count_members.return_value = 0
    membership_repo.get_owner_id.return_value = None

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=1)

    expected_ms = int(ts.timestamp() * 1000)
    assert result["created_at"] == expected_ms
    assert result["updated_at"] == expected_ms


@pytest.mark.anyio
async def test_group_to_dto_null_timestamps():
    """Null timestamps should produce 0."""
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    group = _group(id=1, created_at=None, updated_at=None)

    repo.get_by_id.return_value = group
    profile_repo.get_by_group_id.return_value = _profile(group_id=1)
    membership_repo.count_members.return_value = 0
    membership_repo.get_owner_id.return_value = None

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=1)

    assert result["created_at"] == 0
    assert result["updated_at"] == 0


@pytest.mark.anyio
async def test_group_to_dto_is_public_always_true():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=1)
    profile_repo.get_by_group_id.return_value = _profile(group_id=1)
    membership_repo.count_members.return_value = 0
    membership_repo.get_owner_id.return_value = None

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.get_group(group_id=1)

    assert result["is_public"] is True


# ---------------------------------------------------------------------------
# list_members — additional edge cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_members_with_prev_page():
    repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)

    m1 = _membership(member_id=30, role="MEMBER")
    # prev_id=10 means there is a previous page, next_id=None means no next page
    membership_repo.list_members_cursor.return_value = ([m1], 10, None)
    user_profile_repo.get_profiles_by_user_ids.return_value = {
        30: _user_profile(nickname="Charlie"),
    }

    svc = _make_service(
        repo=repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    members, page = await svc.list_members(group_id=5, page_start=30, page_size=10)

    assert len(members) == 1
    assert page["hasPrev"] is True
    assert page["prevStart"] == 10
    assert page["hasMore"] is False
    assert page["nextStart"] == 0


@pytest.mark.anyio
async def test_list_members_empty_result():
    repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    repo.get_by_id.return_value = _group(id=5)
    membership_repo.list_members_cursor.return_value = ([], None, None)
    user_profile_repo.get_profiles_by_user_ids.return_value = {}

    svc = _make_service(
        repo=repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    members, page = await svc.list_members(group_id=5, page_start=None, page_size=10)

    assert members == []
    assert page["pageStart"] == 0
    assert page["pageSize"] == 0
    assert page["hasPrev"] is False
    assert page["hasMore"] is False


@pytest.mark.anyio
async def test_list_members_negative_page_size():
    repo = AsyncMock()
    repo.get_by_id.return_value = _group(id=5)

    svc = _make_service(repo=repo)

    members, page = await svc.list_members(group_id=5, page_start=None, page_size=-1)

    assert members == []
    assert page["pageSize"] == 0


# ---------------------------------------------------------------------------
# update_group — name=None skips duplicate check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_group_no_name_skips_duplicate_check():
    repo = AsyncMock()
    profile_repo = AsyncMock()
    membership_repo = AsyncMock()
    user_profile_repo = AsyncMock()

    group = _group(id=5)
    repo.get_by_id.return_value = group
    membership_repo.get_member_role.return_value = "OWNER"
    profile_repo.get_by_group_id.return_value = _profile(group_id=5)
    membership_repo.count_members.return_value = 1
    membership_repo.get_owner_id.return_value = 1
    user_profile_repo.get_profile_by_user_id.return_value = _user_profile()

    svc = _make_service(
        repo=repo,
        profile_repo=profile_repo,
        membership_repo=membership_repo,
        user_profile_repo=user_profile_repo,
    )

    result = await svc.update_group(group_id=5, user_id=1, intro="New intro only")

    # exists_by_name should NOT have been called since name was not provided
    repo.exists_by_name.assert_not_awaited()
    repo.update_group.assert_awaited_once_with(group, name=None)
    assert result["is_owner"] is True


# ---------------------------------------------------------------------------
# _date_to_ms helper
# ---------------------------------------------------------------------------


def test_date_to_ms_none():
    assert _date_to_ms(None) == 0


def test_date_to_ms_datetime():
    dt = datetime(2026, 6, 15, 10, 30, 0, tzinfo=UTC)
    expected = int(dt.timestamp() * 1000)
    assert _date_to_ms(dt) == expected


def test_date_to_ms_date_object():
    d = date(2026, 6, 15)
    # Should convert date to datetime at midnight UTC
    from datetime import datetime as dt

    expected = int(dt.combine(d, dt.min.time(), tzinfo=UTC).timestamp() * 1000)
    assert _date_to_ms(d) == expected


# ---------------------------------------------------------------------------
# _target_to_dto helper
# ---------------------------------------------------------------------------


def _target(
    *,
    id: int = 1,
    group_id: int = 5,
    name: str = "Target A",
    intro: str = "Do stuff",
    started_at=None,
    ended_at=None,
    attendance_frequency: str = "DAILY",
    created_at=None,
    **kw,
):
    defaults = {
        "id": id,
        "group_id": group_id,
        "name": name,
        "intro": intro,
        "started_at": started_at or date(2026, 1, 1),
        "ended_at": ended_at or date(2026, 6, 1),
        "attendance_frequency": attendance_frequency,
        "created_at": created_at or NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_target_to_dto():
    t = _target(id=10, group_id=5, name="Goal", intro="Desc")
    dto = _target_to_dto(t)

    assert dto["id"] == 10
    assert dto["groupId"] == 5
    assert dto["name"] == "Goal"
    assert dto["intro"] == "Desc"
    assert dto["attendanceFrequency"] == "DAILY"
    assert isinstance(dto["startedAt"], int)
    assert isinstance(dto["endedAt"], int)
    assert isinstance(dto["createdAt"], int)
    assert dto["startedAt"] > 0
    assert dto["endedAt"] > 0
    assert dto["createdAt"] > 0


def test_target_to_dto_none_created_at():
    t = _target(created_at=None)
    # Override created_at to None explicitly
    t.created_at = None
    dto = _target_to_dto(t)

    assert dto["createdAt"] == 0


def test_target_to_dto_none_dates():
    t = _target()
    t.started_at = None
    t.ended_at = None
    dto = _target_to_dto(t)

    assert dto["startedAt"] == 0
    assert dto["endedAt"] == 0


# ---------------------------------------------------------------------------
# GroupTargetService helpers
# ---------------------------------------------------------------------------


def _make_target_service(group_repo=None, target_repo=None, membership_repo=None):
    return GroupTargetService(
        group_repo=group_repo or AsyncMock(),
        target_repo=target_repo or AsyncMock(),
        membership_repo=membership_repo or AsyncMock(),
    )


# ---------------------------------------------------------------------------
# GroupTargetService.list_targets
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_targets_success():
    group_repo = AsyncMock()
    target_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    t1 = _target(id=1)
    t2 = _target(id=2)
    target_repo.list_by_group.return_value = ([t1, t2], 5)

    svc = _make_target_service(group_repo=group_repo, target_repo=target_repo)

    items, page = await svc.list_targets(group_id=5, page_start=0, page_size=2)

    target_repo.list_by_group.assert_awaited_once_with(group_id=5, limit=2, offset=0)
    assert len(items) == 2
    assert page["total"] == 5
    assert page["hasMore"] is True
    assert page["nextStart"] == 2


@pytest.mark.anyio
async def test_list_targets_no_more():
    group_repo = AsyncMock()
    target_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    target_repo.list_by_group.return_value = ([_target(id=1)], 1)

    svc = _make_target_service(group_repo=group_repo, target_repo=target_repo)

    items, page = await svc.list_targets(group_id=5, page_start=None, page_size=10)

    assert len(items) == 1
    assert page["hasMore"] is False
    assert page["nextStart"] is None
    assert page["pageStart"] == 0


@pytest.mark.anyio
async def test_list_targets_empty():
    group_repo = AsyncMock()
    target_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    target_repo.list_by_group.return_value = ([], 0)

    svc = _make_target_service(group_repo=group_repo, target_repo=target_repo)

    items, page = await svc.list_targets(group_id=5, page_start=0, page_size=10)

    assert items == []
    assert page["total"] == 0
    assert page["hasMore"] is False
    assert page["nextStart"] is None


@pytest.mark.anyio
async def test_list_targets_group_not_found():
    group_repo = AsyncMock()
    group_repo.get_by_id.return_value = None

    svc = _make_target_service(group_repo=group_repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.list_targets(group_id=999, page_start=0, page_size=10)


# ---------------------------------------------------------------------------
# GroupTargetService.get_target
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_target_success():
    target_repo = AsyncMock()
    t = _target(id=10, group_id=5)
    target_repo.get_by_id.return_value = t

    svc = _make_target_service(target_repo=target_repo)

    result = await svc.get_target(group_id=5, target_id=10)

    assert result["id"] == 10
    assert result["groupId"] == 5


@pytest.mark.anyio
async def test_get_target_not_found():
    target_repo = AsyncMock()
    target_repo.get_by_id.return_value = None

    svc = _make_target_service(target_repo=target_repo)

    with pytest.raises(NotFoundError, match="Target not found"):
        await svc.get_target(group_id=5, target_id=999)


@pytest.mark.anyio
async def test_get_target_wrong_group():
    target_repo = AsyncMock()
    t = _target(id=10, group_id=99)  # belongs to group 99, not 5
    target_repo.get_by_id.return_value = t

    svc = _make_target_service(target_repo=target_repo)

    with pytest.raises(NotFoundError, match="Target not found"):
        await svc.get_target(group_id=5, target_id=10)


# ---------------------------------------------------------------------------
# GroupTargetService.create_target
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_target_success():
    group_repo = AsyncMock()
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "OWNER"
    created_target = _target(id=50)
    target_repo.create.return_value = created_target

    svc = _make_target_service(
        group_repo=group_repo, target_repo=target_repo, membership_repo=membership_repo
    )

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 6, 1, tzinfo=UTC)
    result = await svc.create_target(
        group_id=5,
        user_id=1,
        name="New Target",
        intro="Do it",
        started_at=start,
        ended_at=end,
        attendance_frequency="DAILY",
    )

    target_repo.create.assert_awaited_once_with(
        group_id=5,
        name="New Target",
        intro="Do it",
        started_at=start,
        ended_at=end,
        attendance_frequency="DAILY",
    )
    assert result == {"id": 50}


@pytest.mark.anyio
async def test_create_target_as_admin():
    group_repo = AsyncMock()
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "ADMIN"
    target_repo.create.return_value = _target(id=51)

    svc = _make_target_service(
        group_repo=group_repo, target_repo=target_repo, membership_repo=membership_repo
    )

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 6, 1, tzinfo=UTC)
    result = await svc.create_target(
        group_id=5,
        user_id=2,
        name="Admin Target",
        intro="Go",
        started_at=start,
        ended_at=end,
        attendance_frequency="WEEKLY",
    )

    assert result == {"id": 51}


@pytest.mark.anyio
async def test_create_target_group_not_found():
    group_repo = AsyncMock()
    group_repo.get_by_id.return_value = None

    svc = _make_target_service(group_repo=group_repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.create_target(
            group_id=999,
            user_id=1,
            name="X",
            intro="Y",
            started_at=NOW,
            ended_at=NOW,
            attendance_frequency="DAILY",
        )


@pytest.mark.anyio
async def test_create_target_forbidden_member():
    group_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "MEMBER"

    svc = _make_target_service(group_repo=group_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError, match="Only owners and admins can create targets"):
        await svc.create_target(
            group_id=5,
            user_id=10,
            name="X",
            intro="Y",
            started_at=NOW,
            ended_at=NOW,
            attendance_frequency="DAILY",
        )


@pytest.mark.anyio
async def test_create_target_forbidden_non_member():
    group_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = None

    svc = _make_target_service(group_repo=group_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError):
        await svc.create_target(
            group_id=5,
            user_id=10,
            name="X",
            intro="Y",
            started_at=NOW,
            ended_at=NOW,
            attendance_frequency="DAILY",
        )


# ---------------------------------------------------------------------------
# GroupTargetService.update_target
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_target_success():
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    t = _target(id=10, group_id=5)
    target_repo.get_by_id.return_value = t
    membership_repo.get_member_role.return_value = "OWNER"

    svc = _make_target_service(target_repo=target_repo, membership_repo=membership_repo)

    result = await svc.update_target(
        group_id=5,
        target_id=10,
        user_id=1,
        name="Updated",
        intro="New desc",
    )

    target_repo.update.assert_awaited_once_with(
        t,
        name="Updated",
        intro="New desc",
        started_at=None,
        ended_at=None,
        attendance_frequency=None,
    )
    assert result["id"] == 10
    assert result["name"] == "Target A"  # still the old object, but update was called


@pytest.mark.anyio
async def test_update_target_not_found():
    target_repo = AsyncMock()
    target_repo.get_by_id.return_value = None

    svc = _make_target_service(target_repo=target_repo)

    with pytest.raises(NotFoundError, match="Target not found"):
        await svc.update_target(group_id=5, target_id=999, user_id=1, name="X")


@pytest.mark.anyio
async def test_update_target_wrong_group():
    target_repo = AsyncMock()
    t = _target(id=10, group_id=99)
    target_repo.get_by_id.return_value = t

    svc = _make_target_service(target_repo=target_repo)

    with pytest.raises(NotFoundError, match="Target not found"):
        await svc.update_target(group_id=5, target_id=10, user_id=1, name="X")


@pytest.mark.anyio
async def test_update_target_forbidden_member():
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    target_repo.get_by_id.return_value = _target(id=10, group_id=5)
    membership_repo.get_member_role.return_value = "MEMBER"

    svc = _make_target_service(target_repo=target_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError, match="Only owners and admins can update targets"):
        await svc.update_target(group_id=5, target_id=10, user_id=10, name="X")


@pytest.mark.anyio
async def test_update_target_forbidden_non_member():
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    target_repo.get_by_id.return_value = _target(id=10, group_id=5)
    membership_repo.get_member_role.return_value = None

    svc = _make_target_service(target_repo=target_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError):
        await svc.update_target(group_id=5, target_id=10, user_id=10, name="X")


@pytest.mark.anyio
async def test_update_target_as_admin():
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    t = _target(id=10, group_id=5)
    target_repo.get_by_id.return_value = t
    membership_repo.get_member_role.return_value = "ADMIN"

    svc = _make_target_service(target_repo=target_repo, membership_repo=membership_repo)

    result = await svc.update_target(group_id=5, target_id=10, user_id=2, intro="Updated by admin")

    target_repo.update.assert_awaited_once()
    assert result["id"] == 10


# ---------------------------------------------------------------------------
# GroupTargetService.delete_target
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_target_success():
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    t = _target(id=10, group_id=5)
    target_repo.get_by_id.return_value = t
    membership_repo.get_member_role.return_value = "OWNER"

    svc = _make_target_service(target_repo=target_repo, membership_repo=membership_repo)

    await svc.delete_target(group_id=5, target_id=10, user_id=1)

    target_repo.soft_delete.assert_awaited_once_with(t)


@pytest.mark.anyio
async def test_delete_target_not_found():
    target_repo = AsyncMock()
    target_repo.get_by_id.return_value = None

    svc = _make_target_service(target_repo=target_repo)

    with pytest.raises(NotFoundError, match="Target not found"):
        await svc.delete_target(group_id=5, target_id=999, user_id=1)


@pytest.mark.anyio
async def test_delete_target_wrong_group():
    target_repo = AsyncMock()
    t = _target(id=10, group_id=99)
    target_repo.get_by_id.return_value = t

    svc = _make_target_service(target_repo=target_repo)

    with pytest.raises(NotFoundError, match="Target not found"):
        await svc.delete_target(group_id=5, target_id=10, user_id=1)


@pytest.mark.anyio
async def test_delete_target_forbidden_member():
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    target_repo.get_by_id.return_value = _target(id=10, group_id=5)
    membership_repo.get_member_role.return_value = "MEMBER"

    svc = _make_target_service(target_repo=target_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError, match="Only owners and admins can delete targets"):
        await svc.delete_target(group_id=5, target_id=10, user_id=10)


@pytest.mark.anyio
async def test_delete_target_forbidden_non_member():
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    target_repo.get_by_id.return_value = _target(id=10, group_id=5)
    membership_repo.get_member_role.return_value = None

    svc = _make_target_service(target_repo=target_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError):
        await svc.delete_target(group_id=5, target_id=10, user_id=10)


@pytest.mark.anyio
async def test_delete_target_as_admin():
    target_repo = AsyncMock()
    membership_repo = AsyncMock()

    t = _target(id=10, group_id=5)
    target_repo.get_by_id.return_value = t
    membership_repo.get_member_role.return_value = "ADMIN"

    svc = _make_target_service(target_repo=target_repo, membership_repo=membership_repo)

    await svc.delete_target(group_id=5, target_id=10, user_id=2)

    target_repo.soft_delete.assert_awaited_once_with(t)


# ---------------------------------------------------------------------------
# GroupQuestionService helpers
# ---------------------------------------------------------------------------


def _make_question_service(group_repo=None, question_repo=None, membership_repo=None):
    return GroupQuestionService(
        group_repo=group_repo or AsyncMock(),
        question_repo=question_repo or AsyncMock(),
        membership_repo=membership_repo or AsyncMock(),
    )


# ---------------------------------------------------------------------------
# GroupQuestionService.list_questions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_questions_success():
    group_repo = AsyncMock()
    question_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    question_repo.list_by_group.return_value = ([101, 102, 103], 10)

    svc = _make_question_service(group_repo=group_repo, question_repo=question_repo)

    question_ids, page = await svc.list_questions(group_id=5, page_start=0, page_size=3)

    question_repo.list_by_group.assert_awaited_once_with(group_id=5, limit=3, offset=0)
    assert question_ids == [101, 102, 103]
    assert page["total"] == 10
    assert page["hasMore"] is True
    assert page["nextStart"] == 3


@pytest.mark.anyio
async def test_list_questions_no_more():
    group_repo = AsyncMock()
    question_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    question_repo.list_by_group.return_value = ([101], 1)

    svc = _make_question_service(group_repo=group_repo, question_repo=question_repo)

    question_ids, page = await svc.list_questions(group_id=5, page_start=None, page_size=10)

    assert question_ids == [101]
    assert page["hasMore"] is False
    assert page["nextStart"] is None
    assert page["pageStart"] == 0


@pytest.mark.anyio
async def test_list_questions_empty():
    group_repo = AsyncMock()
    question_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    question_repo.list_by_group.return_value = ([], 0)

    svc = _make_question_service(group_repo=group_repo, question_repo=question_repo)

    question_ids, page = await svc.list_questions(group_id=5, page_start=0, page_size=10)

    assert question_ids == []
    assert page["total"] == 0
    assert page["hasMore"] is False
    assert page["nextStart"] is None


@pytest.mark.anyio
async def test_list_questions_group_not_found():
    group_repo = AsyncMock()
    group_repo.get_by_id.return_value = None

    svc = _make_question_service(group_repo=group_repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.list_questions(group_id=999, page_start=0, page_size=10)


# ---------------------------------------------------------------------------
# GroupQuestionService.add_question
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_question_success():
    group_repo = AsyncMock()
    question_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "OWNER"

    svc = _make_question_service(
        group_repo=group_repo, question_repo=question_repo, membership_repo=membership_repo
    )

    result = await svc.add_question(group_id=5, question_id=42, user_id=1)

    question_repo.add_question.assert_awaited_once_with(group_id=5, question_id=42)
    assert result == {"questionId": 42}


@pytest.mark.anyio
async def test_add_question_as_admin():
    group_repo = AsyncMock()
    question_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "ADMIN"

    svc = _make_question_service(
        group_repo=group_repo, question_repo=question_repo, membership_repo=membership_repo
    )

    result = await svc.add_question(group_id=5, question_id=43, user_id=2)

    assert result == {"questionId": 43}


@pytest.mark.anyio
async def test_add_question_group_not_found():
    group_repo = AsyncMock()
    group_repo.get_by_id.return_value = None

    svc = _make_question_service(group_repo=group_repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.add_question(group_id=999, question_id=42, user_id=1)


@pytest.mark.anyio
async def test_add_question_forbidden_member():
    group_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "MEMBER"

    svc = _make_question_service(group_repo=group_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError, match="Only owners and admins can add questions"):
        await svc.add_question(group_id=5, question_id=42, user_id=10)


@pytest.mark.anyio
async def test_add_question_forbidden_non_member():
    group_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = None

    svc = _make_question_service(group_repo=group_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError):
        await svc.add_question(group_id=5, question_id=42, user_id=10)


# ---------------------------------------------------------------------------
# GroupQuestionService.remove_question
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_remove_question_success():
    group_repo = AsyncMock()
    question_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "OWNER"

    svc = _make_question_service(
        group_repo=group_repo, question_repo=question_repo, membership_repo=membership_repo
    )

    await svc.remove_question(group_id=5, question_id=42, user_id=1)

    question_repo.remove_question.assert_awaited_once_with(group_id=5, question_id=42)


@pytest.mark.anyio
async def test_remove_question_as_admin():
    group_repo = AsyncMock()
    question_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "ADMIN"

    svc = _make_question_service(
        group_repo=group_repo, question_repo=question_repo, membership_repo=membership_repo
    )

    await svc.remove_question(group_id=5, question_id=43, user_id=2)

    question_repo.remove_question.assert_awaited_once_with(group_id=5, question_id=43)


@pytest.mark.anyio
async def test_remove_question_group_not_found():
    group_repo = AsyncMock()
    group_repo.get_by_id.return_value = None

    svc = _make_question_service(group_repo=group_repo)

    with pytest.raises(NotFoundError, match="Group not found"):
        await svc.remove_question(group_id=999, question_id=42, user_id=1)


@pytest.mark.anyio
async def test_remove_question_forbidden_member():
    group_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = "MEMBER"

    svc = _make_question_service(group_repo=group_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError, match="Only owners and admins can remove questions"):
        await svc.remove_question(group_id=5, question_id=42, user_id=10)


@pytest.mark.anyio
async def test_remove_question_forbidden_non_member():
    group_repo = AsyncMock()
    membership_repo = AsyncMock()

    group_repo.get_by_id.return_value = _group(id=5)
    membership_repo.get_member_role.return_value = None

    svc = _make_question_service(group_repo=group_repo, membership_repo=membership_repo)

    with pytest.raises(ForbiddenError):
        await svc.remove_question(group_id=5, question_id=42, user_id=10)
