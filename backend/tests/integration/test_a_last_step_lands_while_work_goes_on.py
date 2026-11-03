"""A task does not close on its AI teammate while it is still working on more.

A task delivered what its card called the last step. Its owner then asked for
more, and a few seconds later accepted that card. The task went into its
write-up window, the write-up was never taken up because the teammate was busy
doing what it had just been asked, and half an hour later the task closed and
stopped that work half done; it had nowhere left to be delivered (dev,
2026-10-12).

What decides is what the task's own teammate was asked for, by facts the
platform can check: its session reading a message sent after the card went up
and still being at work (its runner says so), or a message to it still
waiting to be read. Either is work the delivery does not contain, so the
delivery counts as a step and the task goes on. An instruction the platform
gave in the task — another seat woken there, a fix it was told to make — is
not such a request. A task closes when its teammate, having written it up,
closes it; reading the write-up instruction closes nothing. Without that, the
deadline closes it once its teammate's session is not at work on something
else, and nothing holds it past the last deadline: the work is stopped, and the
room hears what was not delivered.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.core.errors import UpstreamUnavailableError
from app.domain.agent import session_state
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.event_author import AuthorType
from app.domain.project import forge
from app.domain.room_task.closing import CLOSES_TASK
from app.domain.run_record.models import RunRecord
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
from tests.landing import (
    teammate_seat,
    time_passes,
    watch,
    write_up_read,
    written_up,
)

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


def _session(client, task_id: str, status: str, *, seat: str | None = None) -> None:
    """What the runner of ``seat``'s session in the task declares it doing;
    by default the task's own teammate's, under the seat it acts as."""
    seat = seat or teammate_seat(client, task_id)[1]
    key = session_state.SeatKey(uuid.UUID(str(task_id)), seat, "claude-code")

    async def declare():
        await session_state.register(client.test_factory, key)
        async with client.test_factory() as session:
            await session_state.declare(session, key, status)
            await session.commit()

    client.portal.call(declare)


def _message(
    client,
    task: dict,
    *,
    read: bool,
    at: datetime | None = None,
    content: str = "顺便把首页也测一下",
    by_platform: bool = False,
    seat: str | None = None,
) -> None:
    """A message to the teammate in the task — its owner's, or a platform
    instruction. ``read``: ``seat``'s session (the teammate's by default) read
    it now — its echo, which stamps it consumed; else it waits in the queue."""
    seat = seat or teammate_seat(client, task["id"])[1]

    async def write():
        async with client.test_factory() as session:
            block = Block(
                project_id=uuid.UUID(task["project_id"]),
                conversation_id=uuid.UUID(task["id"]),
                author="system" if by_platform else "alice",
                author_type=AuthorType.platform
                if by_platform
                else AuthorType.participant,
                kind=BlockKind.message,
                content=content,
                created_at=at or datetime.now(UTC),
                meta={
                    "consumed_turn": str(uuid.uuid4()) if read else None,
                    "agent_recipient": {"handle": TEAMMATE, "mentioned": True},
                },
            )
            session.add(block)
            await session.flush()
            if read:
                now = datetime.now(UTC)
                session.add(
                    NativeInput(
                        project_id=uuid.UUID(task["project_id"]),
                        conversation_id=uuid.UUID(task["id"]),
                        recipient_handle=seat,
                        harness="claude-code",
                        native_session_id="sess-last-step",
                        input_id=uuid.uuid4(),
                        work_id=uuid.uuid4(),
                        block_ids=[str(block.id)],
                        registered_at=now,
                        accepted_at=now,
                        echoed_at=now,
                    )
                )
            await session.commit()

    client.portal.call(write)


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
    # Its owner asks for more after the card went up; the teammate's session
    # reads it and is at work on it.
    _session(client, task_id, "running")
    _message(client, _task(client, task_id), read=True)

    r = _accept(client, card)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"

    _goes_on(client, task_id, before)
    told = [i.payload["content"] for i in _instructions(client, task_id)]
    assert not any(i.payload.get(CLOSES_TASK) for i in _instructions(client, task_id))
    assert any(
        "没有关闭" in said and "cheese_card(action=request)" in said for said in told
    )

    # Nothing closes it behind the teammate's back, however long it works.
    time_passes(client, task_id, minutes=50)
    watch(client)
    assert _task(client, task_id)["status"] == "open"


def test_the_work_that_delivered_does_not_keep_the_task_open(client, app_world):
    """The work the card went up from may still be going when the card is
    accepted; that is the delivery's own work, and the task still closes —
    once its session stops, not by stopping it."""
    task_id, card, _ = _last_step_up(client, app_world)
    _message(
        client,
        _task(client, task_id),
        read=True,
        at=_card_went_up(client, card) - timedelta(minutes=1),
    )
    _session(client, task_id, "running")

    r = _accept(client, card)
    assert r.status_code == 200, r.text
    assert _task(client, task_id)["presentation"]["phrase"] == "accepted"

    time_passes(client, task_id, minutes=50)
    watch(client)
    assert _task(client, task_id)["status"] == "open"

    _session(client, task_id, "idle")
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
    _session(client, task_id, "running")
    _message(client, _task(client, task_id), read=True)
    watch(client)

    _goes_on(client, task_id, before)
    # The write-up it was told to do is not sent any more: the task is not
    # finishing.
    write_up_read(client, task_id)
    assert _task(client, task_id)["status"] == "open"
    [write_up] = [
        i for i in _instructions(client, task_id) if i.payload.get(CLOSES_TASK)
    ]
    assert write_up.state == "failed"
    assert write_up.sent_at is None


def test_the_task_closes_when_its_teammate_closes_it_after_the_write_up(
    client, app_world
):
    task_id, card, _ = _last_step_up(client, app_world)
    r = _accept(client, card)
    assert r.status_code == 200, r.text

    written_up(client, task_id)
    task = _task(client, task_id)
    assert task["status"] == "closed"
    assert task["presentation"]["phrase"] == "accepted"


def test_reading_the_write_up_does_not_close_the_task(client, app_world):
    """The session read the instruction and went quiet: the platform cannot
    tell from that whether it wrote the task up, so the task stays open until
    its teammate closes it, or the deadline does."""
    task_id, card, _ = _last_step_up(client, app_world)
    r = _accept(client, card)
    assert r.status_code == 200, r.text

    write_up_read(client, task_id)
    _session(client, task_id, "running")
    _session(client, task_id, "idle")
    watch(client)
    task = _task(client, task_id)
    assert task["status"] == "open"
    assert task["presentation"]["phrase"] == "accepted"

    time_passes(client, task_id, minutes=50)
    watch(client)
    assert _task(client, task_id)["status"] == "closed"


def test_a_session_at_work_on_the_write_up_does_not_hold_the_deadline(
    client, app_world
):
    """Once the session has read the write-up, its being at work is the
    write-up, which the deadline does not wait for."""
    task_id, card, _ = _last_step_up(client, app_world)
    r = _accept(client, card)
    assert r.status_code == 200, r.text

    write_up_read(client, task_id)
    _session(client, task_id, "running")
    time_passes(client, task_id, minutes=50)
    watch(client)
    assert _task(client, task_id)["status"] == "closed"


@pytest.mark.parametrize(
    ("seat", "why"),
    [
        ("dispatcher", "another seat woken in the task by a platform notice"),
        (None, "a fix the platform told the teammate to make"),
    ],
)
def test_work_the_platform_asked_for_is_not_more_work(client, app_world, seat, why):
    task_id, card, before = _last_step_up(client, app_world)
    _session(client, task_id, "running", seat=seat)
    _message(
        client,
        _task(client, task_id),
        read=True,
        by_platform=True,
        content=why,
        seat=seat,
    )

    r = _accept(client, card)
    assert r.status_code == 200, r.text

    task = _task(client, task_id)
    assert task["presentation"]["phrase"] == "accepted", why
    assert task["branch_name"] == before["branch_name"]
    assert any(i.payload.get(CLOSES_TASK) for i in _instructions(client, task_id))
    watch(client)
    assert _task(client, task_id)["presentation"]["phrase"] == "accepted"

    # The task closes once its teammate writes it up and closes it.
    _session(client, task_id, "idle", seat=seat)
    written_up(client, task_id)
    task = _task(client, task_id)
    assert task["status"] == "closed"
    assert task["presentation"]["phrase"] == "accepted"


def test_a_message_answered_before_the_write_up_is_not_more_work(client, app_world):
    """Its owner asked something after the card went up, and the teammate
    answered and stopped. That request is done with: when its session then
    takes up the write-up, the task is being written up, not worked on more."""
    task_id, card, _ = _last_step_up(client, app_world)
    _session(client, task_id, "running")
    _message(client, _task(client, task_id), read=True)
    _session(client, task_id, "idle")

    r = _accept(client, card)
    assert r.status_code == 200, r.text
    write_up_read(client, task_id)
    _session(client, task_id, "running")
    watch(client)

    task = _task(client, task_id)
    assert task["status"] == "open"
    assert task["presentation"]["phrase"] == "accepted"


def test_another_seats_work_holds_the_deadline_after_the_teammate_wrote_up(
    client, app_world
):
    """The teammate read its write-up; another seat woken in the task is still
    at work. That seat's work is not the write-up: the deadline waits for it,
    and past the last deadline stops it with the room told."""
    task_id, card, _ = _last_step_up(client, app_world)
    r = _accept(client, card)
    assert r.status_code == 200, r.text
    write_up_read(client, task_id)
    _session(client, task_id, "running", seat="dispatcher")

    time_passes(client, task_id, minutes=50)
    watch(client)
    assert _task(client, task_id)["status"] == "open"

    time_passes(client, task_id, minutes=150)
    watch(client)
    assert _task(client, task_id)["status"] == "closed"


@pytest.mark.parametrize("read", [True, False], ids=["being-worked-on", "queued"])
def test_a_message_sent_while_the_task_is_written_up_is_not_dropped(
    client, app_world, read
):
    """Its owner asks for more while the last step is being written up: its
    session is at work on the message, or the message waits to be read. The
    write-up being read must not close the task before the message is
    answered: the task goes on instead."""
    task_id, card, before = _last_step_up(client, app_world)
    r = _accept(client, card)
    assert r.status_code == 200, r.text
    assert _task(client, task_id)["presentation"]["phrase"] == "accepted"

    if read:
        _session(client, task_id, "running")
    _message(client, _task(client, task_id), read=read)

    write_up_read(client, task_id)
    assert _task(client, task_id)["status"] == "open"

    watch(client)
    _goes_on(client, task_id, before)
    told = [i.payload["content"] for i in _instructions(client, task_id)]
    assert any("没有关闭" in said for said in told)


def test_work_that_never_ends_is_stopped_at_the_last_deadline(client, app_world):
    task_id, card, _ = _last_step_up(client, app_world)
    _message(
        client,
        _task(client, task_id),
        read=True,
        at=_card_went_up(client, card) - timedelta(minutes=1),
        content="把慢的几处都改掉",
    )
    _session(client, task_id, "running")
    r = _accept(client, card)
    assert r.status_code == 200, r.text
    task = _task(client, task_id)

    # Its session is at work, and the task waits for it.
    time_passes(client, task_id, minutes=50)
    watch(client)
    assert _task(client, task_id)["status"] == "open"

    # But not forever.
    time_passes(client, task_id, minutes=150)
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

    # The stopped work keeps a record of its own, under the seat whose session
    # it was: once the task is closed nothing else says whose work that was.
    async def records():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(RunRecord).where(
                        RunRecord.conversation_id == uuid.UUID(str(task_id))
                    )
                )
            )

    [record] = client.portal.call(records)
    assert record.seat == teammate_seat(client, task_id)[1]
    assert "把慢的几处都改掉" in record.meta["detail"]


def test_a_task_the_forge_cannot_take_on_does_not_hold_up_the_rest(
    client, app_world, monkeypatch
):
    stuck_id, stuck_card, _ = _last_step_up(client, app_world)
    task_id, card, before = _last_step_up(client, app_world, pr=8)
    for each, its_card in ((stuck_id, stuck_card), (task_id, card)):
        r = _accept(client, its_card)
        assert r.status_code == 200, r.text
        _session(client, each, "running")
        _message(client, _task(client, each), read=True)
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
    # The sweep went on past the task it could not take on.
    assert watched["gone_on"] == 1


def test_a_message_holds_a_task_as_long_as_the_scan_would_still_start_it():
    from app.domain.agent.runtime import AgentWorkRunner
    from app.domain.room_task.closing import QUEUED_FOR

    assert timedelta(seconds=AgentWorkRunner.ORPHAN_STALE_S) == QUEUED_FOR
