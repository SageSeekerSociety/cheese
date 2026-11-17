"""A task whose last step merged waits for the deploy that puts it live.

#5277 merged at 19:49 on 2026-11-16 and its task closed on the spot, while
main's Required CI went red and dev stayed on the release before it: the task
read as done and live for hours when nothing of it had shipped. In a project
whose deploy reports back, the merged task now stays open, reads as waiting
for its deploy, shows the red run holding that back, and is written up only
once a deploy containing the merge is live.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.domain.agent_instance.adapters import task_teammate
from app.domain.delivery.agent import record_task_instruction
from app.domain.review import deploy_wait
from tests.integration.test_a_task_delivers_in_steps import (
    _accepted_step,
    _told,
)
from tests.integration.test_a_task_delivers_in_steps import (
    app_world as _app_world_fixture,
)
from tests.integration.test_accept_pr import REPO
from tests.landing import written_up

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]

URL = f"https://github.com/{REPO}"
RED_RUN = f"{URL}/actions/runs/101"


@pytest.fixture
def reports_back(monkeypatch):
    """The test project's repository is one whose deploy reports here, and a
    deploy of it reported an hour ago."""
    monkeypatch.setattr(settings, "task_deploy_wait_repositories", [REPO])
    monkeypatch.setattr(settings, "platform_repository", REPO)


def _run(client, statement: str, **params) -> list:
    async def run():
        async with client.test_factory() as db:
            result = await db.execute(text(statement), params)
            rows = list(result.all()) if result.returns_rows else []
            await db.commit()
            return rows

    return client.portal.call(run)


def _reported(client, *, ago: timedelta = timedelta(hours=1)) -> None:
    _run(
        client,
        "INSERT INTO deploy_reports (id, repo, sha, reported_at) "
        "VALUES (:id, :repo, 'c908273', :at)",
        id=uuid.uuid4(),
        repo=REPO.lower(),
        at=datetime.now(UTC) - ago,
    )


def _ship(client, commits: list[dict]) -> list[uuid.UUID]:
    return client.portal.call(
        lambda: deploy_wait.ship(
            client.test_factory,
            commits,
            repository_url=URL,
            sha="d00d",
            record_instruction=record_task_instruction,
            task_teammate=task_teammate,
        )
    )


def _ci_run(client, run_id: int, conclusion: str, *, ago: timedelta) -> None:
    now = datetime.now(UTC)
    _run(
        client,
        "INSERT INTO ci_workflow_runs (id, repo, run_id, workflow_id, "
        "workflow_name, workflow_path, event, head_branch, head_sha, "
        "run_attempt, status, conclusion, created_at, html_url, ingested_at) "
        "VALUES (:id, :repo, :run, 1, 'Required CI', "
        "'.github/workflows/required-ci.yml', 'push', 'main', :sha, 1, "
        "'completed', :conclusion, :at, :url, :now)",
        id=uuid.uuid4(),
        repo=REPO,
        run=run_id,
        sha=f"sha{run_id}",
        conclusion=conclusion,
        at=now - ago,
        url=f"{URL}/actions/runs/{run_id}",
        now=now,
    )


def _task(client, task_id: str) -> dict:
    return client.get(f"/topics/{task_id}/task").json()["data"]


def test_the_merged_last_step_waits_for_its_deploy_and_stays_open(
    client, app_world, reports_back
):
    _reported(client)
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    task = _task(client, task_id)
    assert task["status"] == "open"
    assert task["deploying_since"] is not None
    assert task["presentation"] == {"column": "delivering", "phrase": "deploying"}
    # Nobody is told to write it up yet: nothing of it is live.
    assert "最后一次交付" not in _told(client, task_id)


def test_the_deploy_that_contains_the_merge_has_it_written_up(
    client, app_world, reports_back
):
    _reported(client)
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    # A deploy of something else does not release it.
    assert _ship(client, [{"sha": "a1", "message": "fix: other (#6)"}]) == []
    assert _task(client, task_id)["presentation"]["phrase"] == "deploying"

    released = _ship(
        client,
        [{"sha": "a2", "message": "feat(form): trim the sign-up form (#7)"}],
    )

    assert released == [uuid.UUID(str(task_id))]
    task = _task(client, task_id)
    assert task["deploying_since"] is None
    assert task["presentation"]["phrase"] == "accepted"
    assert "部署也已上线" in _told(client, task_id)
    # The same deploy reported twice changes nothing.
    assert _ship(client, [{"sha": "a2", "message": "feat: x (#7)"}]) == []


def test_a_deploy_of_later_commits_releases_a_merge_whose_report_was_missed(
    client, app_world, reports_back
):
    _reported(client)
    task_id, _ = _accepted_step(client, app_world, completes_task=True)
    later = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()

    released = _ship(
        client, [{"sha": "b1", "message": "fix: later (#9)", "merged_at": later}]
    )

    assert released == [uuid.UUID(str(task_id))]


def test_a_red_main_shows_the_run_that_holds_the_deploy_back(
    client, app_world, reports_back
):
    _reported(client)
    _ci_run(client, 100, "success", ago=timedelta(hours=3))
    task_id, _ = _accepted_step(client, app_world, completes_task=True)
    _ci_run(client, 101, "failure", ago=timedelta(seconds=-30))

    client.portal.call(lambda: deploy_wait.mark_blocked(client.test_factory))

    task = _task(client, task_id)
    assert task["status"] == "open"
    assert task["deploy_blocked_url"] == RED_RUN
    assert task["presentation"] == {
        "column": "delivering",
        "phrase": "deploy_blocked",
    }

    # Main goes green again: it is waiting for the deploy, not held back.
    _ci_run(client, 102, "success", ago=timedelta(seconds=-60))
    client.portal.call(lambda: deploy_wait.mark_blocked(client.test_factory))
    task = _task(client, task_id)
    assert task["deploy_blocked_url"] is None
    assert task["presentation"]["phrase"] == "deploying"


def test_without_a_recent_deploy_report_the_task_is_written_up_on_merge(
    client, app_world, reports_back
):
    """An instance no deploy reports to leaves nothing waiting for one."""
    _reported(client, ago=timedelta(days=30))
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    task = _task(client, task_id)
    assert task["deploying_since"] is None
    assert task["presentation"]["phrase"] == "accepted"
    assert "最后一次交付已经采纳并合并" in _told(client, task_id)


def test_a_project_on_another_repository_is_written_up_on_merge(client, app_world):
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    task = _task(client, task_id)
    assert task["deploying_since"] is None
    assert task["presentation"]["phrase"] == "accepted"


def test_closing_a_waiting_task_by_hand_ends_the_wait(client, app_world, reports_back):
    _reported(client)
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    _run(
        client,
        "UPDATE tasks SET deploy_blocked_url = :url WHERE id = :id",
        url=RED_RUN,
        id=uuid.UUID(str(task_id)),
    )
    written_up(client, task_id)
    task = _task(client, task_id)
    assert task["status"] == "closed"
    assert task["deploying_since"] is None
    assert task["deploy_blocked_url"] is None
