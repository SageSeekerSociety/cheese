"""A card waiting on review is its reviewer's to decide, not the teammate's.

Closing a task voids its undecided cards and closes its pull request. In a task
whose reviewer typed 「采纳」 in the conversation but never pressed accept on
the card, the task's AI teammate closed the task itself, writing 「已交付并采纳」:
the close revoked the card in the teammate's name and closed the PR, and the
delivered work was thrown away by the one party that had no say in it.

So the task's own session cannot close a task while a card on it waits for a
person's decision; the close is refused and says why. A person closing their
own task is that person's decision, and still voids the card and closes the PR.
"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import update

from app.core import background
from app.domain.review import task_pr
from app.domain.review.github_pr import GitHubPRClient
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)

_PR = 1
_CLOSE_PR = "close a closed task's PR"


class _Tokens:
    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"


class _GitHub:
    """One open PR, as GitHub's REST API answers about it."""

    def __init__(self) -> None:
        self.pr = {"number": _PR, "state": "open", "merged": False}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            self.pr.update(json.loads(request.content))
        return httpx.Response(200, json=self.pr)


@pytest.fixture
def github(monkeypatch) -> _GitHub:
    fake = _GitHub()

    async def proposal_client(_project_id, _session):
        return GitHubPRClient(
            "acme",
            "widgets",
            _Tokens(),  # type: ignore[arg-type]
            transport=httpx.MockTransport(fake.handle),
        )

    monkeypatch.setattr(task_pr, "proposal_client", proposal_client)
    return fake


@pytest.fixture
def handed_off(monkeypatch) -> list:
    """What a close hands to the background once it commits, held so the test
    runs it (see `test_a_closed_task_closes_its_pr.handed_off`)."""
    pending: list = []

    def capture(coro, *, name: str | None = None) -> bool:
        pending.append((coro, name))
        return True

    monkeypatch.setattr(background, "spawn", capture)
    return pending


def _run(client, pending: list) -> None:
    async def run() -> None:
        while pending:
            coro, _name = pending.pop(0)
            await coro

    client.portal.call(run)


def _task_in_review(client) -> tuple[str, str, str, str]:
    """A task with an open PR and a card on it waiting for bob to decide."""
    project = post_project(client, {"name": "等审"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    seat = room_agent_seat(client, room)
    task = open_task(client, room, "修登录页", owner="alice")

    async def plant() -> str:
        async with client.test_factory() as session:
            await session.execute(
                update(Task)
                .where(Task.id == uuid.UUID(task["id"]))
                .values(
                    pr_number=_PR,
                    pr_url=f"https://github.com/acme/widgets/pull/{_PR}",
                )
            )
            card = AcceptCard(
                topic_id=uuid.UUID(room),
                task_id=uuid.UUID(task["id"]),
                reviewer_handle="bob",
                status=AcceptStatus.pending,
                pr_number=_PR,
                pr_url=f"https://github.com/acme/widgets/pull/{_PR}",
            )
            session.add(card)
            await session.commit()
            return str(card.id)

    return project["id"], task["id"], seat, client.portal.call(plant)


def _card(client, card_id: str) -> AcceptCard:
    async def read() -> AcceptCard:
        async with client.test_factory() as session:
            return await session.get(AcceptCard, uuid.UUID(card_id))

    return client.portal.call(read)


def _status(client, task_id: str) -> str:
    r = client.get(f"/topics/{task_id}/task", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]["status"]


def test_the_teammate_cannot_close_a_task_whose_card_awaits_review(
    client, github, handed_off
):
    project_id, task_id, seat, card_id = _task_in_review(client)

    refused = client.post(
        f"/topics/{task_id}/close",
        json={"conclusion": "已交付并采纳"},
        headers=task_agent_headers(project_id, task_id, seat),
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskCloseWhileInReview"
    assert _status(client, task_id) == "open"
    card = _card(client, card_id)
    assert card.status is AcceptStatus.pending
    assert card.decided_by is None
    _run(client, handed_off)
    assert github.pr["state"] == "open"


def test_a_person_closing_their_task_still_voids_the_card_and_closes_the_pr(
    client, github, handed_off
):
    _project_id, task_id, _seat, card_id = _task_in_review(client)

    closed = client.post(
        f"/topics/{task_id}/close", json={}, headers=session_auth_headers("alice")
    )

    assert closed.status_code == 200, closed.text
    assert _status(client, task_id) == "closed"
    card = _card(client, card_id)
    assert card.status is AcceptStatus.revoked
    assert card.decided_by == "alice"
    assert _CLOSE_PR in [name for _coro, name in handed_off]
    _run(client, handed_off)
    assert github.pr["state"] == "closed"
