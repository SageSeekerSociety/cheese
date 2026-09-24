"""Direct forge merges settle task PRs even when no accept card was filed."""

import pytest

from app.domain.review import pr_poll
from app.domain.room_task.models import Task, TaskStatus
from tests.delivery import delivery_task
from tests.integration.test_accept_pr import (
    _chat_service,
    _make_project,
    _make_topic,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


@pytest.mark.parametrize("manually_closed", [False, True])
def test_direct_merge_settles_uncarded_task_once(client, app_world, manually_closed):
    project_id = _make_project(client)
    topic_id = _make_topic(client, project_id)
    task = delivery_task(client, topic_id)
    number = 1542
    head = app_world["fake"].seed_pr(number, head=task.branch_name)

    async def record_pr():
        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.pr_number = number
            row.pr_url = f"https://github.com/acme/widgets/pull/{number}"
            if manually_closed:
                row.status = TaskStatus.closed
            await session.commit()

    client.portal.call(record_pr)
    app_world["fake"].merge_externally(number)

    first = client.portal.call(pr_poll.poll_uncarded_task_prs, _chat_service())
    second = client.portal.call(pr_poll.poll_uncarded_task_prs, _chat_service())
    assert first["tasks_merged"] == 1 and first["errors"] == []
    assert second["tasks_merged"] == 0

    async def read_result():
        async with client.test_factory() as session:
            return await session.get(Task, task.id)

    row = client.portal.call(read_result)
    assert row.status == TaskStatus.closed
    assert row.accepted_at is not None
    assert row.accepted_by is None
    assert row.delivered_head == head
