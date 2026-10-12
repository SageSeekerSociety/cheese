"""A task that ends without landing closes its PR on GitHub and keeps its branch.

The platform opens a task's PR at the task's first commit, so a task its owner
puts down would otherwise leave an open PR nobody is going to merge. Closing the
task closes that PR; the branch, and the work on it, stays where it is. A PR
that already merged is not touched.
"""

import json
import time
import uuid

import httpx
import pytest
from sqlalchemy import update

from app.domain.project import forge
from app.domain.review.github_pr import GitHubPRClient
from app.domain.room_task.models import Task
from tests.integration.conftest import open_task, post_project, session_auth_headers


class _Tokens:
    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"


class _GitHub:
    """One repository's PRs, as GitHub's REST API answers about them."""

    def __init__(self, number: int, *, state: str, merged: bool):
        self.prs = {number: {"number": number, "state": state, "merged": merged}}
        self.calls: list[tuple[str, str, dict | None]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        number = int(request.url.path.rsplit("/", 1)[-1])
        pr = self.prs[number]
        if request.method == "PATCH":
            pr.update(body or {})
        return httpx.Response(200, json=pr)

    def writes(self) -> list[tuple[str, str, dict | None]]:
        return [call for call in self.calls if call[0] != "GET"]


@pytest.fixture
def github(monkeypatch):
    def install(number: int, *, state: str = "open", merged: bool = False):
        fake = _GitHub(number, state=state, merged=merged)

        async def proposal_client(_project_id, _session):
            return GitHubPRClient(
                "acme",
                "widgets",
                _Tokens(),  # type: ignore[arg-type]
                transport=httpx.MockTransport(fake.handle),
            )

        monkeypatch.setattr(forge, "proposal_client", proposal_client)
        return fake

    return install


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _task_with_pr(client, number: int) -> dict:
    room = post_project(client, {"name": "PRs"}, owner="alice").json()["data"][
        "root_topic_id"
    ]
    task = open_task(client, room, "做一件事", owner="alice")

    async def record_pr() -> None:
        async with client.test_request_factory() as session:
            await session.execute(
                update(Task)
                .where(Task.id == uuid.UUID(task["id"]))
                .values(
                    pr_number=number,
                    pr_url=f"https://github.com/acme/widgets/pull/{number}",
                )
            )
            await session.commit()

    client.portal.call(record_pr)
    return task


def _close(client, task: dict) -> None:
    closed = client.post(
        f"/topics/{task['id']}/close",
        json={},
        headers=session_auth_headers("alice"),
    )
    assert closed.status_code == 200, closed.text


def test_closing_a_task_closes_its_open_pr_and_keeps_its_branch(client, github):
    gh = github(77)
    task = _task_with_pr(client, 77)

    _close(client, task)

    _until(lambda: gh.writes(), "closing the task left its PR open")
    assert gh.writes() == [
        ("PATCH", "/repos/acme/widgets/pulls/77", {"state": "closed"})
    ]
    assert gh.prs[77]["state"] == "closed"
    # Nothing but the PR was asked for: the branch is not deleted.
    assert all("/git/refs" not in path for _, path, _ in gh.calls)


def test_a_merged_pr_is_left_alone(client, github):
    gh = github(78, state="closed", merged=True)
    task = _task_with_pr(client, 78)

    _close(client, task)

    _until(lambda: gh.calls, "closing the task never looked at its PR")
    time.sleep(0.3)
    assert gh.writes() == []


def test_a_task_without_a_pr_asks_github_nothing(client, github):
    gh = github(79)
    room = post_project(client, {"name": "PRs"}, owner="alice").json()["data"][
        "root_topic_id"
    ]
    task = open_task(client, room, "还没提交过", owner="alice")

    _close(client, task)

    time.sleep(0.3)
    assert gh.calls == []
