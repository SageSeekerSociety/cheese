from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.domain.groups.services import GroupsService

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
    membership_repo.add_member.assert_awaited_once_with(
        group_id=10, member_id=1, role="OWNER"
    )
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

    result = await svc.create_group(
        user_id=2, name="  Trimmed  ", intro="test", avatar_id=None
    )

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

    result = await svc.update_group(
        group_id=5, user_id=1, name="New Name", intro="New intro"
    )

    repo.update_group.assert_awaited_once_with(group, name="New Name")
    profile_repo.update_profile.assert_awaited_once_with(
        profile, intro="New intro", avatar_id=None
    )
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

    membership_repo.add_member.assert_awaited_once_with(
        group_id=5, member_id=10, role="MEMBER"
    )
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

    membership_repo.remove_member.assert_awaited_once_with(
        group_id=5, member_id=10
    )
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

    svc = _make_service(
        repo=repo, profile_repo=profile_repo, membership_repo=membership_repo
    )

    items, page = await svc.list_groups(
        keyword="test", page_start=0, page_size=2
    )

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

    svc = _make_service(
        repo=repo, profile_repo=profile_repo, membership_repo=membership_repo
    )

    items, page = await svc.list_groups(
        keyword=None, page_start=None, page_size=10
    )

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

    items, page = await svc.list_groups(
        keyword="nonexistent", page_start=0, page_size=10
    )

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

    svc = _make_service(
        repo=repo, profile_repo=profile_repo, membership_repo=membership_repo
    )

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

    svc = _make_service(
        repo=repo, profile_repo=profile_repo, membership_repo=membership_repo
    )

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
    assert page["page_size"] == 2
    assert page["has_more"] is True
    assert page["next_start"] == 30


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
    assert page["page_size"] == 0
    assert page["has_more"] is False


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
    assert page["has_more"] is False


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
