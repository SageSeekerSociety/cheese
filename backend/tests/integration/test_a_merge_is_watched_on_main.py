"""After a task's PR merges, the platform watches the merge on the default branch.

The rules, as stated before the code was written:

- A check that fails on the merge commit, and passed on the commit before it,
  is the task's to fix: the task reopens, its AI teammate is told which checks
  failed, and its owner hears.
- A check that was already failing before the merge is not the task's: nothing
  is said and the task stays closed.
- Checks that pass say nothing.
- A deployment that includes the merge is said in the task: where it went, or
  that it failed. A failed one is told to the owner and reopens nothing.
- A repository that records no deployments the platform may read says nothing.
- What has not finished when the watch runs out is never reported.
- A task that delivers in steps has a landing per merge: two merges whose checks
  fail in the same sweep reopen the task once, and say what each look saw.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.domain.review import landing_forge, landing_watch
from app.domain.review.landing_forge import (
    BatchCommits,
    Check,
    CommitChange,
    Deployment,
    ForgeUnavailable,
)
from app.domain.review.landing_models import TaskLanding
from tests.integration.test_a_task_delivers_in_steps import (
    _accepted_step,
    _told,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.landing import summary_turn_ends, time_passes, watch

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]

MERGED = "merge-sha-1"
BEFORE = "parent-sha-0"
SECOND = "merge-sha-2"


class _Forge:
    """The repository as the watch reads it: checks per commit, deployments,
    and — when a test fills them in — the batched main gate's reads."""

    def __init__(self):
        self.ran: dict[str, list[Check]] = {}
        self.deployed: Deployment | None = None
        self.records_deployments = True
        #: The newest finished batch gate at or after a commit: sha -> (head, checks).
        self.covered: dict[str, tuple[str, list[Check]]] = {}
        #: The commit `main-green` names, when the repository batches.
        self.green: str | None = None
        #: (anchor, head) -> the commits of that batch.
        self.batches: dict[tuple[str, str], BatchCommits] = {}
        #: sha -> what that commit changed.
        self.changes: dict[str, CommitChange] = {}
        #: head -> the test files the red jobs name.
        self.failing: dict[str, list[str]] = {}
        #: (commit, descendant) pairs the forge says are in that order.
        self.ahead: set[tuple[str, str]] = set()
        #: PR number -> the commit its merge put on main.
        self.merges: dict[int, str] = {}
        #: sha -> its first parent, for tests that walk a batch back.
        self.parents: dict[str, str | None] = {}

    async def merge_sha(self, pr_number):
        return self.merges.get(pr_number, MERGED)

    async def checks(self, sha):
        return self.ran.get(sha, [])

    async def parent(self, sha):
        if sha in self.parents:
            return self.parents[sha]
        return BEFORE if sha == MERGED else None

    async def deployment(self, sha, since):
        if not self.records_deployments:
            raise ForgeUnavailable("no deployments")
        return self.deployed

    async def covering_run(self, sha, *, anchor=None):
        return self.covered.get(sha)

    async def main_green(self):
        return self.green

    async def commits_between(self, base, head):
        return self.batches.get((base, head))

    async def commit_change(self, sha):
        return self.changes.get(sha)

    async def failed_tests(self, head):
        return self.failing.get(head, [])

    async def is_ancestor(self, commit, descendant):
        return commit == descendant or (commit, descendant) in self.ahead


def _ran(name: str, conclusion: str | None) -> Check:
    return Check(
        name=name,
        status="completed" if conclusion else "in_progress",
        conclusion=conclusion,
        url=f"https://ci.example/{name}",
        suite=1,
    )


@pytest.fixture
def forge(monkeypatch):
    forge = _Forge()

    async def reads_for(_project_id, _session):
        return forge

    async def always(_quota):
        return True

    monkeypatch.setattr(landing_forge, "reads_for", reads_for)
    monkeypatch.setattr(landing_watch, "quota_serves_background", always)
    return forge


def _completed_task(client, app_world) -> str:
    task_id, _ = _accepted_step(client, app_world, completes_task=True)
    summary_turn_ends(client, task_id)
    assert _task(client, task_id)["status"] == "closed"
    return task_id


def _another_merge_lands(client, task_id: str) -> None:
    """The task's next step merged too: the second landing the watch makes for a
    task that delivers in steps, still being watched when the sweep runs."""

    async def insert():
        async with client.test_factory() as session:
            task = uuid.UUID(str(task_id))
            first = await session.scalar(
                select(TaskLanding).where(TaskLanding.task_id == task)
            )
            assert first is not None
            now = datetime.now(UTC)
            session.add(
                TaskLanding(
                    task_id=task,
                    pr_number=first.pr_number + 1,
                    merge_sha=SECOND,
                    landed_at=now,
                    watch_until=now + timedelta(hours=2),
                )
            )
            await session.commit()

    client.portal.call(insert)


def _task(client, task_id) -> dict:
    return client.get(f"/topics/{task_id}/task").json()["data"]


def _said(client, task_id) -> list[str]:
    blocks = client.get(f"/topics/{task_id}/blocks").json()["data"]["data"]
    return [b["content"] for b in blocks if b.get("kind") == "event"]


def _owner_heard(client, task_id) -> int:
    """How many times the task's owner was told about its merge going wrong."""
    owner = _task(client, task_id)["owner_handle"]

    async def read():
        async with client.test_factory() as db:
            return await db.scalar(
                text(
                    "SELECT count(*) FROM deliveries "
                    "WHERE recipient_handle = :owner "
                    "AND payload->>'eventType' IN "
                    "('main_checks_failed', 'deploy_failed')"
                ),
                {"owner": owner},
            )

    return client.portal.call(read) or 0


def test_a_check_the_merge_broke_reopens_the_task_for_its_teammate(
    client, app_world, forge
):
    task_id = _completed_task(client, app_world)
    forge.ran[BEFORE] = [_ran("backend", "success"), _ran("frontend", "success")]
    forge.ran[MERGED] = [_ran("backend", "failure"), _ran("frontend", "success")]
    heard = _owner_heard(client, task_id)

    watch(client)

    task = _task(client, task_id)
    assert task["status"] == "open"
    assert task["presentation"]["phrase"] != "accepted"
    told = _told(client, task_id)
    assert "https://ci.example/backend" in told
    assert "https://ci.example/frontend" not in told
    assert any("合并后检查未通过" in line for line in _said(client, task_id))
    assert _owner_heard(client, task_id) > heard


def test_two_merges_of_one_task_that_both_fail_reopen_it_once(client, app_world, forge):
    task_id = _completed_task(client, app_world)
    _another_merge_lands(client, task_id)
    forge.ran[BEFORE] = [_ran("backend", "success")]
    forge.ran[MERGED] = [_ran("backend", "failure")]
    forge.ran[SECOND] = [_ran("e2e", "failure")]
    heard = _owner_heard(client, task_id)

    watch(client)

    said = _said(client, task_id)
    reopened = [line for line in said if "任务已重新打开" in line]
    still_going = [line for line in said if "会在这一步一起修复" in line]
    assert len(reopened) == 1
    assert len(still_going) == 1
    # The earlier merge is the one that reopens the task, as it was when the
    # looks went one merge at a time: which of the two is a new step is not
    # something a tick may decide by which look got there first.
    assert "backend" in reopened[0]
    assert "e2e" in still_going[0]
    assert _task(client, task_id)["status"] == "open"
    # One merge each: a merge that broke a check is told to the owner either way.
    assert _owner_heard(client, task_id) == heard + 2


def test_a_check_that_was_failing_before_the_merge_is_not_the_tasks(
    client, app_world, forge
):
    task_id = _completed_task(client, app_world)
    forge.ran[BEFORE] = [_ran("backend", "failure")]
    forge.ran[MERGED] = [_ran("backend", "failure")]
    told_before = _told(client, task_id)
    heard = _owner_heard(client, task_id)

    watch(client)

    assert _task(client, task_id)["status"] == "closed"
    assert _told(client, task_id) == told_before
    assert _owner_heard(client, task_id) == heard
    assert not any("检查未通过" in line for line in _said(client, task_id))


def test_checks_that_pass_say_nothing(client, app_world, forge):
    task_id = _completed_task(client, app_world)
    forge.ran[MERGED] = [_ran("backend", "success")]
    said_before = _said(client, task_id)

    watch(client)

    assert _task(client, task_id)["status"] == "closed"
    assert _said(client, task_id) == said_before


def test_a_check_still_running_says_nothing_yet(client, app_world, forge):
    task_id = _completed_task(client, app_world)
    forge.ran[MERGED] = [_ran("backend", "failure"), _ran("e2e", None)]

    watch(client)

    assert _task(client, task_id)["status"] == "closed"
    assert not any("检查未通过" in line for line in _said(client, task_id))


def test_where_the_merge_was_deployed_is_said_in_the_task(client, app_world, forge):
    task_id = _completed_task(client, app_world)
    forge.deployed = Deployment(environment="dev", state="success", url="")

    watch(client)
    watch(client)

    said = _said(client, task_id)
    assert said.count("已部署到 dev") == 1
    assert _task(client, task_id)["status"] == "closed"


def test_a_failed_deployment_is_said_and_reopens_nothing(client, app_world, forge):
    task_id = _completed_task(client, app_world)
    forge.deployed = Deployment(environment="dev", state="failure", url="")
    heard = _owner_heard(client, task_id)

    watch(client)

    assert "部署到 dev 失败" in _said(client, task_id)
    assert _owner_heard(client, task_id) > heard
    assert _task(client, task_id)["status"] == "closed"


def test_a_repository_without_deployments_says_nothing_of_them(
    client, app_world, forge
):
    task_id = _completed_task(client, app_world)
    forge.records_deployments = False
    said_before = _said(client, task_id)

    watch(client)

    assert _said(client, task_id) == said_before


def test_what_has_not_finished_when_the_watch_runs_out_is_never_said(
    client, app_world, forge
):
    task_id = _completed_task(client, app_world)
    time_passes(client, task_id, minutes=150)
    forge.ran[BEFORE] = [_ran("backend", "success")]
    forge.ran[MERGED] = [_ran("backend", "failure")]
    forge.deployed = Deployment(environment="dev", state="failure", url="")

    watch(client)

    assert _task(client, task_id)["status"] == "closed"
    assert not any(
        "检查未通过" in line or "部署" in line for line in _said(client, task_id)
    )
