from types import SimpleNamespace

import pytest

from app.domain.space.analytics_service import SpaceAnalyticsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(id: int, space_id: int, category_id: int, approved: int, creator_id: int):
    return SimpleNamespace(
        id=id,
        space_id=space_id,
        category_id=category_id,
        approved=approved,
        name=f"Task {id}",
        intro="demo",
        creator_id=creator_id,
    )


def _membership(
    task_id: int,
    member_id: int,
    approved: int,
    space_id: int,
    completion_status="NOT_SUBMITTED",
):
    return SimpleNamespace(
        task_id=task_id,
        member_id=member_id,
        approved=approved,
        completion_status=completion_status,
        space_id=space_id,
    )


class DummyTaskRepo:
    def __init__(self, tasks):
        self._tasks = tasks

    async def list_tasks(self, **kwargs):
        space_id = kwargs["space_id"]
        result = [t for t in self._tasks if t.space_id == space_id]
        if kwargs.get("category_id") is not None:
            result = [t for t in result if t.category_id == kwargs["category_id"]]
        if kwargs.get("approved") is not None:
            result = [t for t in result if t.approved == kwargs["approved"]]
        if kwargs.get("owner_id") is not None:
            result = [t for t in result if t.creator_id == kwargs["owner_id"]]
        limit = kwargs.get("limit", len(result))
        offset = kwargs.get("offset", 0)
        return result[offset : offset + limit]


class DummyMembershipRepo:
    def __init__(self, memberships):
        self._memberships = memberships

    async def list_memberships_for_space(self, space_id: int):
        return [
            m for m in self._memberships if getattr(m, "space_id", space_id) == space_id
        ]


# ---------------------------------------------------------------------------
# get_publishers_participation -- profile/user name resolution
# ---------------------------------------------------------------------------


class TestPublishersParticipationNameResolution:
    @pytest.mark.anyio
    async def test_name_from_profile_nickname(self):
        """Line 111, 125-126: profile_repo is present and profile has a nickname."""
        tasks = [_task(1, 5, 1, 0, 10)]
        memberships = []
        profile_repo = SimpleNamespace(
            get_profiles_by_user_ids=lambda ids: {
                10: SimpleNamespace(nickname="Prof Nick")
            },
        )
        # Make async

        async def get_profiles(ids):
            return {10: SimpleNamespace(nickname="Prof Nick")}

        async def get_users(ids):
            return {10: SimpleNamespace(username="user10")}

        profile_repo = SimpleNamespace(get_profiles_by_user_ids=get_profiles)
        user_repo = SimpleNamespace(get_by_ids=get_users)

        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo(tasks),
            membership_repo=DummyMembershipRepo(memberships),
            user_repo=user_repo,
            profile_repo=profile_repo,
        )

        result = await service.get_publishers_participation(space_id=5)
        assert len(result) == 1
        assert result[0]["publisherName"] == "Prof Nick"

    @pytest.mark.anyio
    async def test_name_from_user_when_profile_has_no_nickname(self):
        """Line 113, 127-128: profile exists but nickname is None, fallback to username."""  # noqa: E501
        tasks = [_task(1, 5, 1, 0, 10)]
        memberships = []

        async def get_profiles(ids):
            return {10: SimpleNamespace(nickname=None)}

        async def get_users(ids):
            return {10: SimpleNamespace(username="user10")}

        profile_repo = SimpleNamespace(get_profiles_by_user_ids=get_profiles)
        user_repo = SimpleNamespace(get_by_ids=get_users)

        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo(tasks),
            membership_repo=DummyMembershipRepo(memberships),
            user_repo=user_repo,
            profile_repo=profile_repo,
        )

        result = await service.get_publishers_participation(space_id=5)
        assert result[0]["publisherName"] == "user10"

    @pytest.mark.anyio
    async def test_name_fallback_to_user_id(self):
        """Line 126, 128, 130: no profile, no user -> fallback to 'User {id}'."""
        tasks = [_task(1, 5, 1, 0, 10)]
        memberships = []

        async def get_profiles(ids):
            return {}

        async def get_users(ids):
            return {}

        profile_repo = SimpleNamespace(get_profiles_by_user_ids=get_profiles)
        user_repo = SimpleNamespace(get_by_ids=get_users)

        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo(tasks),
            membership_repo=DummyMembershipRepo(memberships),
            user_repo=user_repo,
            profile_repo=profile_repo,
        )

        result = await service.get_publishers_participation(space_id=5)
        assert result[0]["publisherName"] == "User 10"

    @pytest.mark.anyio
    async def test_name_fallback_when_repos_are_none(self):
        """Line 111, 113: profile_repo and user_repo are None."""
        tasks = [_task(1, 5, 1, 0, 10)]
        memberships = []

        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo(tasks),
            membership_repo=DummyMembershipRepo(memberships),
            user_repo=None,
            profile_repo=None,
        )

        result = await service.get_publishers_participation(space_id=5)
        assert result[0]["publisherName"] == "User 10"


# ---------------------------------------------------------------------------
# export_participants -- task with no members
# ---------------------------------------------------------------------------


class TestExportParticipants:
    @pytest.mark.anyio
    async def test_task_with_no_members_produces_empty_row(self):
        """Line 155: task has no members, generates row with empty participantId."""
        tasks = [_task(1, 5, 1, 0, 10)]
        memberships = []

        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo(tasks),
            membership_repo=DummyMembershipRepo(memberships),
        )

        csv = await service.export_participants(space_id=5)
        lines = csv.split("\n")
        assert len(lines) == 2
        assert lines[1] == "1,Task 1,,0"

    @pytest.mark.anyio
    async def test_task_with_members(self):
        """Line 166: normal row with member."""
        tasks = [_task(1, 5, 1, 0, 10)]
        memberships = [_membership(1, 99, 0, 5)]

        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo(tasks),
            membership_repo=DummyMembershipRepo(memberships),
        )

        csv = await service.export_participants(space_id=5)
        lines = csv.split("\n")
        # header + 1 member row
        assert len(lines) == 2
        assert "99" in lines[1]
        assert "APPROVED" in lines[1]

    @pytest.mark.anyio
    async def test_csv_escape_with_special_chars(self):
        """Line 166: task name with special CSV characters."""
        t = SimpleNamespace(
            id=1,
            space_id=5,
            category_id=1,
            approved=0,
            name='Has "quotes" and\nnewline',
            intro="demo",
            creator_id=10,
        )
        memberships = [_membership(1, 99, 0, 5)]

        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo([t]),
            membership_repo=DummyMembershipRepo(memberships),
        )

        csv = await service.export_participants(space_id=5)
        # The name should be escaped
        assert '"Has ""quotes"" and\nnewline"' in csv


# ---------------------------------------------------------------------------
# _status_label -- unknown value
# ---------------------------------------------------------------------------


class TestStatusLabel:
    def test_unknown_value(self):
        """Line 189: approved value not in the reverse map."""
        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo([]),
            membership_repo=DummyMembershipRepo([]),
        )
        assert service._status_label(999) == "UNKNOWN"

    def test_none_value(self):
        """Line 189: approved value is None."""
        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo([]),
            membership_repo=DummyMembershipRepo([]),
        )
        assert service._status_label(None) == "UNKNOWN"

    def test_known_value(self):
        service = SpaceAnalyticsService(
            task_repo=DummyTaskRepo([]),
            membership_repo=DummyMembershipRepo([]),
        )
        assert service._status_label(0) == "APPROVED"
        assert service._status_label(1) == "DISAPPROVED"
        assert service._status_label(2) == "NONE"
