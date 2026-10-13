"""A task does not close on its AI teammate while it is still working on more.

A task delivered what its card called the last step. Its owner then asked for
more, and a few seconds later accepted that card. The task went into its
write-up window, the write-up never got a turn because the teammate was busy
doing what it had just been asked, and half an hour later the task closed and
stopped that work half done; it had nowhere left to be delivered (dev,
2026-10-12).

What decides is what the task's own teammate was asked for: work begun after
the card went up is not in the delivery, nor is a message to it still waiting
for its turn, so the delivery counts as a step and the task goes on. A turn
the platform started in the task — another seat woken there, a fix it was
told to make — is not such a request. A task whose teammate is idle closes as
it always did, and one whose teammate is still in the turn that delivered
closes once that turn is over. A turn gone silent does not hold a task, and
nothing holds it past the last deadline: the work is stopped, and the room
hears what was not delivered.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from app.core.errors import UpstreamUnavailableError
from app.domain.agent.models import AgentTurn
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.delivery.models import Delivery
from app.domain.project import forge
from app.domain.room_task.closing import CLOSES_TASK
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_auth_headers
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


def _last_step_up(client, app_world, pr: int = 7) -> tuple[str, str, dict]:
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
    head = _give_card_a_pr(client, app_world, tid, card, pr)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[pr] = f"merge-sha-{pr}"
    task_id = delivery_task_id(client, tid)
    return task_id, card, client.get(f"/topics/{task_id}/task").json()["data"]


#: The seat the task's AI teammate takes its turns in: the project's 芝士.
TEAMMATE = "cheese"


def _turn_starts(
    client,
    task_id: str,
    *,
    at: datetime | None = None,
    by: str = TEAMMATE,
    author: str = "alice",
    content: str = "接着把慢的几处改掉",
) -> uuid.UUID:
    """A turn starts in the task — by default its AI teammate's, on its
    owner's word — and is still going."""
    turn = uuid.uuid4()
    started = at or datetime.now(UTC)

    async def start():
        async with client.test_factory() as session:
            session.add(
                AgentTurn(
                    id=turn,
                    conversation_id=uuid.UUID(str(task_id)),
                    continuation_id=turn,
                    author=author,
                    agent_handle=by,
                    content=content,
                    started_at=started,
                    delivered_at=started,
                )
            )
            await session.commit()

    client.portal.call(start)
    return turn


def _turn_says_something(client, task: dict, turn: uuid.UUID) -> None:
    """The running turn writes a line in the task now."""

    async def say():
        async with client.test_factory() as session:
            session.add(
                Block(
                    project_id=uuid.UUID(task["project_id"]),
                    conversation_id=uuid.UUID(task["id"]),
                    author=TEAMMATE,
                    author_type=AuthorType.participant,
                    kind=BlockKind.message,
                    content="还在改",
                    turn_id=turn,
                )
            )
            await session.commit()

    client.portal.call(say)


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


@pytest.mark.parametrize(
    ("seat", "why"),
    [
        ("dispatcher", "another seat woken in the task by a platform notice"),
        (TEAMMATE, "a fix the platform told the teammate to make"),
    ],
)
def test_a_turn_the_platform_started_is_not_more_work(client, app_world, seat, why):
    task_id, card, before = _last_step_up(client, app_world)
    platform_turn = _turn_starts(
        client, task_id, by=seat, author="system", content=f"平台提示：{why}"
    )

    r = _accept(client, card)
    assert r.status_code == 200, r.text

    task = _task(client, task_id)
    assert task["presentation"]["phrase"] == "accepted", why
    assert task["branch_name"] == before["branch_name"]
    assert any(i.payload.get(CLOSES_TASK) for i in _instructions(client, task_id))
    watch(client)
    assert _task(client, task_id)["presentation"]["phrase"] == "accepted"

    # The task closes once that turn is over, not by stopping it.
    _turn_ends(client, platform_turn)
    summary_turn_ends(client, task_id)
    task = _task(client, task_id)
    assert task["status"] == "closed"
    assert task["presentation"]["phrase"] == "accepted"


def _owner_asks(client, task: dict, *, taken: bool) -> None:
    """Its owner asks the teammate for more in the task. ``taken``: the
    message has been taken on as a turn, which waits for the session; else
    nothing has taken it yet, and it waits in the queue."""
    if taken:
        asked = client.post(
            f"/topics/{task['id']}/messages",
            json={"request_id": str(uuid.uuid4()), "content": "顺便把首页也测一下"},
            headers=session_auth_headers("alice"),
        )
        assert asked.status_code == 200, asked.text
        return

    async def queue():
        async with client.test_factory() as session:
            session.add(
                Block(
                    project_id=uuid.UUID(task["project_id"]),
                    conversation_id=uuid.UUID(task["id"]),
                    author="alice",
                    author_type=AuthorType.participant,
                    kind=BlockKind.message,
                    content="顺便把首页也测一下",
                    meta={
                        "consumed_turn": None,
                        "agent_recipient": {"handle": TEAMMATE, "mentioned": True},
                    },
                )
            )
            await session.commit()

    client.portal.call(queue)


@pytest.mark.parametrize("taken", [True, False], ids=["turn-waiting", "queued"])
def test_a_message_sent_while_the_task_is_written_up_is_not_dropped(
    client, app_world, taken
):
    """Its owner asks for more while the write-up turn runs; the message waits
    for that turn to end. The write-up ending must not close the task before
    the message gets its turn: the task goes on instead."""
    task_id, card, before = _last_step_up(client, app_world)
    r = _accept(client, card)
    assert r.status_code == 200, r.text
    assert _task(client, task_id)["presentation"]["phrase"] == "accepted"

    _owner_asks(client, _task(client, task_id), taken=taken)

    summary_turn_ends(client, task_id)
    assert _task(client, task_id)["status"] == "open"

    watch(client)
    _goes_on(client, task_id, before)
    told = [i.payload["content"] for i in _instructions(client, task_id)]
    assert any("没有关闭" in said for said in told)


def test_a_turn_gone_silent_does_not_keep_the_task_open(client, app_world):
    """An interval left open by a turn that died without closing it is not
    work: it says nothing past the orphan sweep's limit."""
    task_id, card, _ = _last_step_up(client, app_world)
    _turn_starts(client, task_id, at=datetime.now(UTC) - timedelta(hours=36))

    r = _accept(client, card)
    assert r.status_code == 200, r.text
    assert _task(client, task_id)["presentation"]["phrase"] == "accepted"

    summary_turn_ends(client, task_id)
    assert _task(client, task_id)["status"] == "closed"


def test_work_that_never_ends_is_stopped_at_the_last_deadline(client, app_world):
    task_id, card, _ = _last_step_up(client, app_world)
    delivering = _turn_starts(
        client,
        task_id,
        at=_card_went_up(client, card) - timedelta(minutes=1),
        content="把慢的几处都改掉",
    )
    r = _accept(client, card)
    assert r.status_code == 200, r.text
    task = _task(client, task_id)

    # It keeps talking, so it is work, and the task waits for it.
    time_passes(client, task_id, minutes=50)
    _turn_says_something(client, task, delivering)
    watch(client)
    assert _task(client, task_id)["status"] == "open"

    # But not forever.
    time_passes(client, task_id, minutes=150)
    _turn_says_something(client, task, delivering)
    watch(client)
    assert _task(client, task_id)["status"] == "closed"

    async def notes():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Block).where(
                        Block.conversation_id == uuid.UUID(task["room_id"]),
                        Block.kind == BlockKind.event,
                    )
                )
            )

    [note] = [
        b for b in client.portal.call(notes) if task["title"] in (b.content or "")
    ]
    assert "没有交付" in note.content
    assert "把慢的几处都改掉" in note.meta["detail"]


def test_a_task_the_forge_cannot_take_on_does_not_hold_up_the_rest(
    client, app_world, monkeypatch
):
    stuck_id, stuck_card, _ = _last_step_up(client, app_world)
    task_id, card, before = _last_step_up(client, app_world, pr=8)
    for each, its_card in ((stuck_id, stuck_card), (task_id, card)):
        r = _accept(client, its_card)
        assert r.status_code == 200, r.text
        _turn_starts(client, each)
    stuck_project = uuid.UUID(_task(client, stuck_id)["project_id"])
    reachable = forge.default_branch

    async def default_branch(project_id, session):
        if project_id == stuck_project:
            raise UpstreamUnavailableError("forge down")
        return await reachable(project_id, session)

    monkeypatch.setattr(forge, "default_branch", default_branch)

    watched = watch(client)

    _goes_on(client, task_id, before)
    assert _task(client, stuck_id)["presentation"]["phrase"] == "accepted"
    assert "landings_looked_at" in watched


def test_a_message_holds_a_task_as_long_as_the_scan_would_still_start_it():
    from app.domain.agent.runtime import AgentWorkRunner
    from app.domain.room_task.closing import QUEUED_FOR

    assert timedelta(seconds=AgentWorkRunner.ORPHAN_STALE_S) == QUEUED_FOR
