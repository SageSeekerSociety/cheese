"""A task does not close on its AI teammate while it is still working on more.

A task delivered what its card called the last step. Its owner then asked for
more, and a few seconds later accepted that card. The task went into its
write-up window, the write-up never got a turn because the teammate was busy
doing what it had just been asked, and half an hour later the task closed and
stopped that work half done; it had nowhere left to be delivered (dev,
2026-10-12).

What decides is what is running: work begun after the card went up is not in
the delivery, so the delivery counts as a step and the task goes on. A task
whose teammate is idle closes as it always did, and one whose teammate is
still in the turn that delivered closes once that turn is over.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from app.domain.agent.models import AgentTurn
from app.domain.delivery.models import Delivery
from app.domain.room_task.closing import CLOSES_TASK
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.test_accept_pr import (
    _accept,
    _give_card_a_pr,
    _make_project,
    _make_topic,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.landing import summary_turn_ends, time_passes, watch

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _last_step_up(client, app_world) -> tuple[str, str, dict]:
    """A task whose card says its delivery is the last step, checks green."""
    fake = app_world["fake"]
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    r = client.post(
        f"/topics/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "change_subject": "perf(pages): measure how long pages take to open",
            "reviewer_handle": "alice",
            "completes_task": True,
        },
    )
    assert r.status_code == 200, r.text
    card = r.json()["data"]["id"]
    head = _give_card_a_pr(client, app_world, tid, card, 7)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[7] = "merge-sha-1"
    task_id = delivery_task_id(client, tid)
    return task_id, card, client.get(f"/topics/{task_id}/task").json()["data"]


def _turn_starts(client, task_id: str, *, at: datetime | None = None) -> uuid.UUID:
    """The task's AI teammate starts a turn in it, and is still in it."""
    turn = uuid.uuid4()
    started = at or datetime.now(UTC)

    async def start():
        async with client.test_factory() as session:
            session.add(
                AgentTurn(
                    id=turn,
                    conversation_id=uuid.UUID(str(task_id)),
                    continuation_id=turn,
                    author="alice",
                    content="接着把慢的几处改掉",
                    started_at=started,
                    delivered_at=started,
                )
            )
            await session.commit()

    client.portal.call(start)
    return turn


def _turn_ends(client, turn: uuid.UUID) -> None:
    async def stop():
        async with client.test_factory() as session:
            await session.execute(
                update(AgentTurn)
                .where(AgentTurn.id == turn)
                .values(stopped_at=datetime.now(UTC))
            )
            await session.commit()

    client.portal.call(stop)


def _card_went_up(client, card: str) -> datetime:
    async def read():
        async with client.test_factory() as session:
            return await session.scalar(
                text("SELECT created_at FROM accept_cards WHERE id = :id"),
                {"id": uuid.UUID(str(card))},
            )

    return client.portal.call(read)


def _instructions(client, task_id: str) -> list[Delivery]:
    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Delivery)
                    .where(Delivery.conversation_id == uuid.UUID(str(task_id)))
                    .order_by(Delivery.recorded_at)
                )
            )

    return client.portal.call(read)


def _task(client, task_id: str) -> dict:
    return client.get(f"/topics/{task_id}/task").json()["data"]


def _goes_on(client, task_id: str, before: dict) -> None:
    task = _task(client, task_id)
    assert task["status"] == "open"
    assert task["presentation"]["phrase"] != "accepted"
    # What the teammate is doing is delivered as the next step, on a branch
    # of its own cut from the latest code.
    assert task["branch_name"] != before["branch_name"]
    assert task["pr_number"] is None


def test_a_last_step_accepted_while_its_teammate_works_on_more_keeps_the_task_open(
    client, app_world
):
    task_id, card, before = _last_step_up(client, app_world)
    # Its owner asks for more after the card went up; the teammate gets to it.
    _turn_starts(client, task_id)

    r = _accept(client, card)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"

    _goes_on(client, task_id, before)
    told = [i.payload["content"] for i in _instructions(client, task_id)]
    assert not any(i.payload.get(CLOSES_TASK) for i in _instructions(client, task_id))
    assert any("没有关闭" in said and "cheese_accept_request" in said for said in told)

    # Nothing closes it behind the teammate's back, however long it works.
    time_passes(client, task_id, minutes=50)
    watch(client)
    assert _task(client, task_id)["status"] == "open"


def test_the_turn_that_delivered_does_not_keep_the_task_open(client, app_world):
    """The turn the card went up from may still be finishing when the card is
    accepted; that is the delivery's own work, and the task still closes —
    once that turn is over, not by stopping it."""
    task_id, card, _ = _last_step_up(client, app_world)
    delivering = _turn_starts(
        client, task_id, at=_card_went_up(client, card) - timedelta(minutes=1)
    )

    r = _accept(client, card)
    assert r.status_code == 200, r.text
    assert _task(client, task_id)["presentation"]["phrase"] == "accepted"

    time_passes(client, task_id, minutes=50)
    watch(client)
    assert _task(client, task_id)["status"] == "open"

    _turn_ends(client, delivering)
    watch(client)
    task = _task(client, task_id)
    assert task["status"] == "closed"
    assert task["presentation"]["phrase"] == "accepted"


def test_work_begun_while_the_task_is_written_up_keeps_it_open(client, app_world):
    task_id, card, before = _last_step_up(client, app_world)
    r = _accept(client, card)
    assert r.status_code == 200, r.text
    assert _task(client, task_id)["presentation"]["phrase"] == "accepted"

    # Someone asks for more in the window, and the teammate starts on it.
    _turn_starts(client, task_id)
    watch(client)

    _goes_on(client, task_id, before)
    # The write-up it was told to do is not sent any more: the task is not
    # finishing.
    summary_turn_ends(client, task_id)
    assert _task(client, task_id)["status"] == "open"
    [write_up] = [
        i for i in _instructions(client, task_id) if i.payload.get(CLOSES_TASK)
    ]
    assert write_up.state == "failed"
    assert write_up.sent_at is None


def test_an_idle_teammate_lets_the_task_close_as_before(client, app_world):
    task_id, card, _ = _last_step_up(client, app_world)
    earlier = _turn_starts(client, task_id)
    _turn_ends(client, earlier)

    r = _accept(client, card)
    assert r.status_code == 200, r.text

    summary_turn_ends(client, task_id)
    task = _task(client, task_id)
    assert task["status"] == "closed"
    assert task["presentation"]["phrase"] == "accepted"
