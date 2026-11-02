"""A delivery waiting on a person is decided by that person, in the interface.

Closing a task voids the delivery still waiting on it: its card revoked, its
pull request closed. In one task the reviewer typed 「采纳」 in the
conversation instead of pressing accept on the card; the task's teammate asked
「你自己合还是我推？」 and seven seconds later closed the task itself, voiding
the delivery nobody had decided.

So the teammate cannot close a task whose delivery waits on a person, and is
told where in the interface the person makes the decision. A person who works
the task closes it as before, and that close still voids the delivery. The
forge proxy refuses the other way round (closing the pull request); it asks
`/sandbox/forge-held` which pull requests wait.
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.room_task.models import Task
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_auth_headers
from tests.integration.test_accept import _make_card, _make_project, _make_topic
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_pr import app_world as app_world

pytestmark = pytest.mark.usefixtures("remote_delivery")


def _waiting(client) -> tuple[str, str, uuid.UUID, str]:
    """A task alice owns, whose delivery (a card on a PR) waits on bob."""
    project_id = _make_project(client)
    room_id = _make_topic(client, project_id)
    card_id = _make_card(client, room_id, "bob")
    return project_id, room_id, delivery_task_id(client, room_id), card_id


def _card(client, room_id: str) -> dict:
    cards = client.get(
        f"/topics/{room_id}/accept-card", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return cards[0]


def _task_status(client, task_id) -> str:
    r = client.get(f"/topics/{task_id}/task", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]["status"]


def test_the_teammate_cannot_close_a_task_whose_delivery_waits(client):
    _project_id, room_id, task_id, _card_id = _waiting(client)

    refused = client.post(
        f"/topics/{task_id}/close",
        json={"conclusion": "已交付并采纳"},
        headers=delivery_headers(client, room_id),
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskCloseWhileDeliveryWaits"
    assert _task_status(client, task_id) == "open"
    card = _card(client, room_id)
    assert card["status"] == "pending"
    assert card["decided_by"] is None


def test_someone_who_works_the_task_still_closes_it_and_voids_the_delivery(client):
    _project_id, room_id, task_id, _card_id = _waiting(client)

    closed = client.post(
        f"/topics/{task_id}/close", json={}, headers=session_auth_headers("alice")
    )

    assert closed.status_code == 200, closed.text
    assert _task_status(client, task_id) == "closed"
    card = _card(client, room_id)
    assert card["status"] == "revoked"
    assert card["decided_by"] == "alice"


def test_the_forge_proxy_is_told_which_pull_requests_wait(client):
    project_id, room_id, task_id, card_id = _waiting(client)
    pr = _card(client, room_id)["pr_number"]

    async def branch() -> str:
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            assert task is not None
            return task.branch_name

    sandbox = {
        "Authorization": f"Bearer {mint_scoped_token(project_id=str(project_id))}"
    }
    held = client.get("/sandbox/forge-held", headers=sandbox)
    assert held.status_code == 200, held.text
    assert held.json()["data"] == {
        "numbers": [pr],
        "branches": [client.portal.call(branch)],
    }

    client.post(
        f"/accept-cards/{card_id}/reject",
        json={"decided_by": "bob", "note": "不要了"},
        headers=session_auth_headers("bob"),
    )
    held = client.get("/sandbox/forge-held", headers=sandbox)
    assert held.json()["data"] == {"numbers": [], "branches": []}
