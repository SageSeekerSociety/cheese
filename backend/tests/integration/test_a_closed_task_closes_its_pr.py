"""A task that ends without landing closes its PR on GitHub and keeps its branch.

The platform opens a task's PR at the task's first commit, so a task its owner
puts down would otherwise leave an open PR nobody is going to merge. Closing the
task closes that PR; the branch, and the work on it, stays where it is. A PR
that already merged is not touched.
"""

import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import update

from app.core import background
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


@pytest.fixture
def handed_off(monkeypatch):
    """The work a close hands to the background, held for the test to run.

    ``close_pr_after_commit`` spawns it on the way out of the commit, so nothing
    the response returns says whether it ran, or when. This file used to wait on
    the clock for that — a 15 s guess at how long the work takes, and a 0.3 s
    guess in the two cases that expect no write at all. Under a loaded shard the
    guess is lost with nothing to show for it: in ``backend / integration (3/5)``
    of run 38247759620 the PR was never written within the deadline, on both the
    case and its one retry (two 15.38 s calls), and the app logged no failure.

    Holding the work makes the wait exact, and lets a case assert that work was,
    or was not, handed off at all — which the 0.3 s sleep could not. That the
    hand-off actually runs is ``tests/unit/test_background_spawn.py``.
    """
    pending: list[tuple[object, str | None]] = []

    def capture(coro, *, name: str | None = None) -> bool:
        pending.append((coro, name))
        return True

    monkeypatch.setattr(background, "spawn", capture)
    return pending


def _run_handed_off(
    client, pending: list[tuple[object, str | None]], *, expect: str | None = None
) -> list[str | None]:
    """Run, in the application's own loop, what the close handed to the background.

    Returns what was handed off, by name, so a case can also say what it was not.
    """
    names = [name for _, name in pending]
    if expect is not None:
        assert expect in names, names

    async def run() -> None:
        while pending:
            coro, _ = pending.pop(0)
            await asyncio.wait_for(coro, 30)

    client.portal.call(run)
    return names


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


_CLOSE_WORK = "close a closed task's PR"


def test_closing_a_task_closes_its_open_pr_and_keeps_its_branch(
    client, github, handed_off
):
    gh = github(77)
    task = _task_with_pr(client, 77)

    _close(client, task)
    _run_handed_off(client, handed_off, expect=_CLOSE_WORK)

    assert gh.writes() == [
        ("PATCH", "/repos/acme/widgets/pulls/77", {"state": "closed"})
    ]
    assert gh.prs[77]["state"] == "closed"
    # Nothing but the PR was asked for: the branch is not deleted.
    assert all("/git/refs" not in path for _, path, _ in gh.calls)


def test_a_merged_pr_is_left_alone(client, github, handed_off):
    gh = github(78, state="closed", merged=True)
    task = _task_with_pr(client, 78)

    _close(client, task)
    _run_handed_off(client, handed_off, expect=_CLOSE_WORK)

    # It looked at the PR to find that out, and then wrote nothing.
    assert gh.calls
    assert gh.writes() == []


def test_a_task_without_a_pr_asks_github_nothing(client, github, handed_off):
    gh = github(79)
    room = post_project(client, {"name": "PRs"}, owner="alice").json()["data"][
        "root_topic_id"
    ]
    task = open_task(client, room, "还没提交过", owner="alice")

    _close(client, task)
    names = _run_handed_off(client, handed_off)

    # No PR to close, so the close handed nothing off — nothing was left running
    # past the response that could ask GitHub anything.
    assert _CLOSE_WORK not in names, names
    assert gh.calls == []
