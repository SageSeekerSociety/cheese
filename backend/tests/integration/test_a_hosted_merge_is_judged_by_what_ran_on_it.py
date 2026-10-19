"""A hosted repository's merge is judged by the checks that ran on the merge
commit, whatever its branch requires of a pull request.

The rules, as stated before the code was written:

- The branch's required checks gate the pull request, and the forge enforced
  them before the merge. A check that runs only on pull requests never reports
  on the merge commit, so the watch does not wait for it there.
- Checks that ran on the merge commit and passed mean the merge passed, said
  on the first look, from that commit's checks alone.
- A merge commit on which nothing runs has no post-merge signal: once the
  forge has had a few minutes to start its runs, the watch says so instead of
  waiting out the two hours.

Both forges, on a repository that is not the platform's own: GitHub reports
check runs and commit statuses; Forgejo reports commit statuses whose
contexts carry the event that ran them (`… (push)`, `… (pull_request)`).
"""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.domain.review import landing_forge, landing_watch
from app.domain.review.forgejo_pr import ForgejoPRClient
from app.domain.review.landing_models import TaskLanding
from tests.integration.test_a_merge_is_watched_on_main import _completed_task
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.landing import time_passes, watch

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]

#: The merge commit the accepted task's PR put on the branch (`app_world`).
MERGE = "merge-sha-1"
PARENT = "parent-sha-0"


class _Tokens:
    async def installation_token(self):
        return "read-token", None

    async def write_token(self):
        return "write-token", None

    async def granted_permissions(self):
        return {"checks": "read"}


class _GitHub:
    """api.github.test/repos/acme/rocket: `build` is required of pull requests
    and runs only on them; `deploy` runs on every push to `main`."""

    def __init__(self):
        self.runs: dict[str, list[dict]] = {}
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.asked.append(f"{request.method} {path}")
        if path == "/graphql":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "defaultBranchRef": {
                                "name": "main",
                                "branchProtectionRule": {
                                    "requiredStatusChecks": [
                                        {
                                            "context": "build",
                                            "app": {"databaseId": 15368},
                                        }
                                    ]
                                },
                            }
                        }
                    }
                },
            )
        path = path.removeprefix("/repos/acme/rocket")
        if path == "/rules/branches/main":
            return httpx.Response(200, json=[])
        if path == "/commits":
            return httpx.Response(200, json=[{"sha": MERGE}, {"sha": PARENT}])
        if path.endswith("/check-runs"):
            return httpx.Response(
                200, json={"check_runs": self.runs.get(path.split("/")[2], [])}
            )
        if path.endswith("/status"):
            return httpx.Response(200, json={"statuses": []})
        if path.startswith("/compare/"):
            return httpx.Response(200, json={"status": "identical", "commits": []})
        if path.startswith("/commits/"):
            return httpx.Response(200, json={"parents": [{"sha": PARENT}]})
        return httpx.Response(404)


class _Forgejo:
    """forge.test/api/v1/repos/acme/rocket: branch protection requires
    `CI / build (pull_request)`; the push to `main` reports `CI / build (push)`."""

    def __init__(self):
        self.statuses: dict[str, list[dict]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1/repos/acme/rocket")
        if path == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/branches/main":
            return httpx.Response(
                200,
                json={
                    "enable_status_check": True,
                    "status_check_contexts": ["CI / build (pull_request)"],
                },
            )
        if path.endswith("/statuses"):
            sha = path.split("/")[2]
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(
                200, json=self.statuses.get(sha, []) if page == 1 else []
            )
        if path.startswith("/pulls/"):
            return httpx.Response(
                200,
                json={
                    "number": int(path.split("/")[2]),
                    "title": "feat: rocket",
                    "state": "closed",
                    "merged": True,
                    "merge_commit_sha": MERGE,
                    "merged_at": "2026-10-19T00:00:00Z",
                    "head": {"sha": "head-sha", "ref": "task"},
                    "base": {"ref": "main"},
                },
            )
        return httpx.Response(404)


def _serve(monkeypatch, reads) -> None:
    async def reads_for(_project_id, _session):
        return reads

    async def always(_quota):
        return True

    monkeypatch.setattr(landing_forge, "reads_for", reads_for)
    monkeypatch.setattr(landing_watch, "quota_serves_background", always)


@pytest.fixture
def github(monkeypatch):
    forge = _GitHub()

    async def pr_status(_number):
        return SimpleNamespace(merge_commit_sha=MERGE)

    _serve(
        monkeypatch,
        landing_forge.GitHubLandingReads(
            SimpleNamespace(pr_status=pr_status),
            _Tokens(),
            "https://api.github.test",
            "acme",
            "rocket",
            transport=httpx.MockTransport(forge),
            branch="main",
        ),
    )
    return forge


@pytest.fixture
def forgejo(monkeypatch):
    forge = _Forgejo()
    transport = httpx.MockTransport(forge)
    _serve(
        monkeypatch,
        landing_forge.ForgejoLandingReads(
            ForgejoPRClient(
                "acme",
                "rocket",
                _Tokens(),
                api_base="https://forge.test/api/v1",
                transport=transport,
            ),
            _Tokens(),
            "https://forge.test/api/v1",
            "acme",
            "rocket",
        ),
    )
    return forge


def _checks(client, task_id) -> str:
    async def read():
        async with client.test_factory() as session:
            landing = await session.scalar(
                select(TaskLanding).where(
                    TaskLanding.task_id == uuid.UUID(str(task_id))
                )
            )
            return landing.checks.value

    return client.portal.call(read)


def _deploy_run(conclusion: str) -> dict:
    return {
        "name": "deploy",
        "app": {"id": 15368},
        "status": "completed",
        "conclusion": conclusion,
        "html_url": "https://github.test/acme/rocket/runs/1",
    }


def test_github_a_merge_whose_push_checks_passed_passes_on_the_first_look(
    client, app_world, github
):
    task_id = _completed_task(client, app_world)
    github.runs[MERGE] = [_deploy_run("success")]
    github.asked.clear()

    watch(client)

    assert _checks(client, task_id) == "passed"
    # One look reads the merge commit's own checks, and nothing else.
    assert sorted(github.asked) == [
        f"GET /repos/acme/rocket/commits/{MERGE}/check-runs",
        f"GET /repos/acme/rocket/commits/{MERGE}/status",
    ]


def test_github_a_merge_nothing_ran_on_has_no_post_merge_signal(
    client, app_world, github
):
    task_id = _completed_task(client, app_world)

    watch(client)
    # The forge may not have started its runs yet.
    assert _checks(client, task_id) == "watching"

    time_passes(client, task_id, minutes=10)
    watch(client)

    assert _checks(client, task_id) == "unchecked"


def test_forgejo_a_push_status_is_judged_whatever_the_pull_request_requires(
    client, app_world, forgejo
):
    task_id = _completed_task(client, app_world)
    forgejo.statuses[MERGE] = [
        {
            "id": 1,
            "context": "CI / build (push)",
            "status": "success",
            "target_url": "https://forge.test/acme/rocket/actions/runs/1",
        }
    ]

    watch(client)

    assert _checks(client, task_id) == "passed"


def test_forgejo_a_merge_nothing_ran_on_has_no_post_merge_signal(
    client, app_world, forgejo
):
    task_id = _completed_task(client, app_world)
    time_passes(client, task_id, minutes=10)

    watch(client)

    assert _checks(client, task_id) == "unchecked"
