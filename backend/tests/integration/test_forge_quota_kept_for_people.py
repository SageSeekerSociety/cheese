"""Background polling leaves part of the forge quota for people.

A GitHub App installation has one hourly quota for everything the platform
does with a repository. The pollers stop while little of it is left, so a
burst of polling cannot use up what a person delivering a card needs.
"""

import uuid

import pytest

from app.domain.project.forge import ForgeRateLimitedError
from tests.delivery import delivery_task, delivery_task_id
from tests.integration.test_accept_pr import (
    _cards,
    _FakeTokens,
    _make_project,
    _make_topic,
    _poll,
    _ready_card,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.machine_work import machine_commits

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def test_polling_waits_while_the_quota_is_low_and_delivery_does_not(client, app_world):
    fake = app_world["fake"]
    _FakeTokens.quota_left = 900  # of 5000: under the share kept for people

    # A person files a card: that goes through.
    _pid, tid, _cid, number, _head = _ready_card(client, app_world)
    fake.merge_externally(number)
    fake.status_calls.clear()

    _poll(client)

    assert fake.status_calls == []
    assert _cards(client, tid)[0]["status"] == "pending"

    _FakeTokens.quota_left = 4000
    _poll(client)

    assert fake.status_calls
    assert _cards(client, tid)[0]["status"] == "accepted"


def test_a_sweep_that_meets_a_spent_quota_leaves_the_rest_of_that_project_alone(
    client, app_world, monkeypatch
):
    """Every later task of the project would meet the same refusal; asking
    GitHub once per task only lengthens the outage."""
    from app.domain.review import pr_publish

    monkeypatch.setattr(pr_publish, "enabled", lambda: True)
    pid = _make_project(client)
    for _ in range(3):
        tid = _make_topic(client, pid)
        delivery_task(client, tid, commit=False)
        machine_commits(
            uuid.UUID(pid), delivery_task_id(client, tid), {"a.txt": "work\n"}
        )
    asked: list[str] = []

    async def refused(project_id, session, branch, **_):
        asked.append(branch)
        raise ForgeRateLimitedError("代码仓库的 API 额度暂时用完了")

    monkeypatch.setattr(pr_publish, "branch_head", refused)

    counts = client.portal.call(pr_publish.sweep_draft_prs, client.test_request_factory)

    assert len(asked) == 1
    assert counts["opened"] == 0
    assert app_world["opened"] == []
