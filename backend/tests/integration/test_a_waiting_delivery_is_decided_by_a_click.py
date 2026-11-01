"""A delivery waiting on a person is decided by that person, with a click.

Closing a task voids the delivery still waiting on it — its card revoked, its
pull request closed. In one task the reviewer typed 「采纳」 in the
conversation instead of pressing accept on the card; the task's teammate asked
「你自己合还是我推？」 and seven seconds later closed the task itself, voiding
the delivery nobody had decided.

So the teammate cannot close a task whose delivery waits on a person. It asks,
in the conversation, with the buttons that decide it — 「接受 PR #n」 and
「关闭任务」 — and the decision is made when a person clicks one. A button does
what the card's and the task's own buttons do: the same request, refused to
the same people. Once the delivery is decided, however it was decided, the
question stops waiting and says how it went.
"""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.models import Block
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


def _ask(client, room_id: str, task_id, offer: list[str]) -> dict:
    r = client.post(
        f"/topics/{task_id}/accept-card/ask",
        json={
            "question": "PR 还在等你决定。要我关掉这个任务吗？关掉后这份交付会作废。",
            "offer": offer,
        },
        headers=delivery_headers(client, room_id),
    )
    assert r.status_code == 200, r.text
    (question,) = r.json()["data"]["blocks"]
    return question


def _meta(client, block_id: str) -> dict:
    async def read() -> dict:
        async with client.test_factory() as session:
            block = await session.get(Block, uuid.UUID(block_id))
            assert block is not None
            return dict(block.meta or {})

    return client.portal.call(read)


def _action(question: dict, kind: str) -> dict:
    return next(
        option["action"]
        for option in question["meta"]["options"]
        if option["action"]["kind"] == kind
    )


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


def test_the_teammate_asks_with_the_buttons_that_decide_it(client):
    _project_id, room_id, task_id, card_id = _waiting(client)
    pr = _card(client, room_id)["pr_number"]

    question = _ask(client, room_id, task_id, ["accept", "close"])

    options = question["meta"]["options"]
    assert [option["text"] for option in options] == [f"接受 PR #{pr}", "关闭任务"]
    assert [option["action"]["kind"] for option in options] == ["accept", "close"]
    assert {option["action"]["card"] for option in options} == {card_id}
    # Asked of whoever can make each decision: the reviewer accepts, the task's
    # owner closes.
    assert question["meta"]["asked"] == ["bob", "alice"]


def test_the_close_button_closes_the_task_for_someone_who_works_it(client):
    _project_id, room_id, task_id, _card_id = _waiting(client)
    question = _ask(client, room_id, task_id, ["accept", "close"])
    close = _action(question, "close")

    # bob reviews the delivery but does not work the task: the task's own
    # 关闭任务 refuses him, and so does the button.
    by_bob = client.post(
        f"/topics/{close['task']}/close", json={}, headers=session_auth_headers("bob")
    )
    assert by_bob.status_code == 403, by_bob.text
    assert _task_status(client, task_id) == "open"

    by_alice = client.post(
        f"/topics/{close['task']}/close",
        json={},
        headers=session_auth_headers("alice"),
    )

    assert by_alice.status_code == 200, by_alice.text
    assert _task_status(client, task_id) == "closed"
    card = _card(client, room_id)
    assert card["status"] == "revoked"
    assert card["decided_by"] == "alice"
    last = _meta(client, question["id"])["answer_log"][-1]
    assert (last["kind"], last["option"], last["by"]) == (
        "decided",
        "关闭任务",
        "alice",
    )


def test_the_accept_button_accepts_for_the_reviewer(client):
    _project_id, room_id, task_id, card_id = _waiting(client)
    pr = _card(client, room_id)["pr_number"]
    question = _ask(client, room_id, task_id, ["accept"])
    accept = _action(question, "accept")
    body = {
        "head_sha": accept["head_sha"],
        "deliverable_revision": accept["deliverable_revision"],
    }

    # alice owns the task but is not its reviewer: the card's accept refuses
    # her, and so does the button, and the question keeps waiting.
    by_alice = client.post(
        f"/accept-cards/{accept['card']}/accept",
        json={**body, "decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert by_alice.status_code == 403, by_alice.text
    assert _card(client, room_id)["status"] == "pending"
    assert _meta(client, question["id"])["answer_log"] == []

    by_bob = client.post(
        f"/accept-cards/{accept['card']}/accept",
        json={**body, "decided_by": "bob"},
        headers=session_auth_headers("bob"),
    )

    assert by_bob.status_code == 200, by_bob.text
    assert by_bob.json()["data"]["id"] == card_id
    assert by_bob.json()["data"]["status"] == "accepted"
    last = _meta(client, question["id"])["answer_log"][-1]
    assert (last["kind"], last["option"], last["by"]) == (
        "decided",
        f"接受 PR #{pr}",
        "bob",
    )


def test_a_return_on_the_card_closes_the_question_without_a_button(client):
    _project_id, room_id, task_id, card_id = _waiting(client)
    question = _ask(client, room_id, task_id, ["accept", "close"])

    returned = client.post(
        f"/accept-cards/{card_id}/reject",
        json={"decided_by": "bob", "note": "标题要改"},
        headers=session_auth_headers("bob"),
    )

    assert returned.status_code == 200, returned.text
    last = _meta(client, question["id"])["answer_log"][-1]
    assert (last["kind"], last["option"]) == ("decided", None)


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
