from types import SimpleNamespace

import pytest

from app.domain.space.analytics_service import SpaceAnalyticsService


def task(id: int, space_id: int, category_id: int, approved: int, creator_id: int):
    return SimpleNamespace(
        id=id,
        space_id=space_id,
        category_id=category_id,
        approved=approved,
        name=f"Task {id}",
        intro="demo",
        creator_id=creator_id,
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
        return [m for m in self._memberships if getattr(m, "space_id", space_id) == space_id]


def membership(task_id: int, member_id: int, approved: int, space_id: int):
    return SimpleNamespace(
        task_id=task_id,
        member_id=member_id,
        approved=approved,
        completion_status="COMPLETED" if approved == 0 else "NOT_SUBMITTED",
        space_id=space_id,
    )


@pytest.mark.anyio
async def test_space_analytics_distribution_counts():
    repo = DummyTaskRepo([task(1, 7, 1, 0, 10), task(2, 7, 1, 2, 11), task(3, 7, 2, 0, 10)])
    membership_repo = DummyMembershipRepo(
        [membership(1, 99, 0, 7), membership(2, 101, 2, 7), membership(3, 103, 0, 7)]
    )
    service = SpaceAnalyticsService(task_repo=repo, membership_repo=membership_repo)

    data = await service.get_task_analytics(
        space_id=7, category_id=None, task_status=None, publisher_id=None
    )

    status_items = {
        item["label"]: item["count"] for item in data["taskStatusDistribution"]["items"]
    }
    assert status_items["APPROVED"] == 2
    assert status_items["NONE"] == 1


@pytest.mark.anyio
async def test_space_analytics_filters_by_status():
    repo = DummyTaskRepo([task(1, 7, 1, 0, 10), task(2, 7, 1, 1, 11), task(3, 7, 2, 0, 10)])
    membership_repo = DummyMembershipRepo([])
    service = SpaceAnalyticsService(task_repo=repo, membership_repo=membership_repo)

    data = await service.get_task_analytics(
        space_id=7, category_id=None, task_status="APPROVED", publisher_id=None
    )

    status_items = {
        item["label"]: item["count"] for item in data["taskStatusDistribution"]["items"]
    }
    assert status_items.get("APPROVED") == 2
    assert "DISAPPROVED" not in status_items


@pytest.mark.anyio
async def test_space_analytics_rejects_invalid_status():
    repo = DummyTaskRepo([])
    membership_repo = DummyMembershipRepo([])
    service = SpaceAnalyticsService(task_repo=repo, membership_repo=membership_repo)

    from app.core.errors import BadRequestError

    with pytest.raises(BadRequestError):
        await service.get_task_analytics(
            space_id=7, category_id=None, task_status="INVALID", publisher_id=None
        )


@pytest.mark.anyio
async def test_publishers_participation_groups_by_owner():
    repo = DummyTaskRepo([task(1, 5, 1, 0, 10), task(2, 5, 1, 2, 11), task(3, 5, 2, 0, 10)])
    membership_repo = DummyMembershipRepo(
        [membership(1, 201, 0, 5), membership(3, 202, 0, 5), membership(2, 203, 1, 5)]
    )
    service = SpaceAnalyticsService(task_repo=repo, membership_repo=membership_repo)

    participation = await service.get_publishers_participation(space_id=5)
    counts = {row["publisherId"]: row["taskCount"] for row in participation}
    assert counts.get(10) == 2
    assert counts.get(11) == 1


@pytest.mark.anyio
async def test_fetch_all_tasks_paginates():
    """Verify _fetch_all_tasks loops when results exceed page size."""
    all_tasks = [task(i, 1, 1, 0, 10) for i in range(1, 6)]
    repo = DummyTaskRepo(all_tasks)
    membership_repo = DummyMembershipRepo([])
    service = SpaceAnalyticsService(task_repo=repo, membership_repo=membership_repo)
    service._PAGE_SIZE = 2  # force small pages

    fetched = await service._fetch_all_tasks(space_id=1)
    assert len(fetched) == 5


@pytest.mark.anyio
async def test_export_participants_escapes_csv():
    t = SimpleNamespace(
        id=1,
        space_id=1,
        category_id=1,
        approved=0,
        name='Task with, comma and "quotes"',
        intro="demo",
        creator_id=10,
    )
    repo = DummyTaskRepo([t])
    m = membership(1, 99, 0, 1)
    membership_repo = DummyMembershipRepo([m])
    service = SpaceAnalyticsService(task_repo=repo, membership_repo=membership_repo)

    csv = await service.export_participants(space_id=1)
    lines = csv.split("\n")
    assert len(lines) == 2
    # The task name should be quoted
    assert '"Task with, comma and ""quotes"""' in lines[1]
