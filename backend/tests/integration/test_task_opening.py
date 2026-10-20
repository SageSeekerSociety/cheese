"""A task made from a discussion — one of several laid out in a 支线 — starts
with its AI teammate setting to work from it. While the task's document is
still empty, whoever opens the task is told where that first instruction is,
and when it failed they can have it tried again.

The rules, stated before the code:

- right after the task is made, its first instruction is under way;
- once the first instruction gave up, the task says it failed, and someone
  working the task can try again, which gives the same instruction again;
- only someone working the task tries again, and only after a failure;
- written to the session and not read yet, it is under way; read, it is under
  way while the session that read it is at work; it failed when the work it
  was read into failed, or when that session stopped having said nothing;
- a task created empty has no such first instruction;
- an instruction that has not started half an hour after it was given gives up
  instead of trying for ever;
- a page already showing the task hears when the instruction gives up, and
  reads it failed without a reload.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from tests.ask_fixtures import wait_turn_idle
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)
from tests.integration.test_a_task_change_reaches_every_page_showing_it import (
    _drain,
    _told,
)


def _task_from_message(client):
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, p["id"], "bob")
    r = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    )
    channel = r.json()["data"]["id"]
    root = post_message(client, channel, "alice", {"content": "预览有两个问题"})
    thread = client.post(
        f"/blocks/{root['id']}/thread", headers=session_auth_headers("alice")
    ).json()["data"]["id"]
    said = post_message(client, thread, "alice", {"content": "微信里打不开预览"})
    r = client.post(
        f"/blocks/{said['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return channel, r.json()["data"]["id"]


def _opening(client, task):
    r = client.get(f"/topics/{task}/task", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]["opening"]


def _openings(client, task):
    from app.domain.delivery.models import Delivery

    async def _rows():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Delivery)
                    .where(
                        Delivery.conversation_id == uuid.UUID(task),
                        Delivery.payload["purpose"].astext == "opening",
                    )
                    .order_by(Delivery.recorded_at)
                )
            )

    return asyncio.run(_rows())


def _give_up(client, task):
    from app.domain.delivery.models import Delivery

    async def _fail():
        async with client.test_factory() as session:
            await session.execute(
                update(Delivery)
                .where(Delivery.conversation_id == uuid.UUID(task))
                .values(state="failed", last_error="gave up")
            )
            await session.commit()

    asyncio.run(_fail())


def test_a_new_task_is_being_drafted(client):
    _, task = _task_from_message(client)

    assert _opening(client, task) in ("drafting", "waiting")


def test_a_failed_opening_is_said_and_can_be_tried_again(client):
    _, task = _task_from_message(client)
    _give_up(client, task)
    assert _opening(client, task) == "failed"

    r = client.post(f"/topics/{task}/opening", headers=session_auth_headers("alice"))

    assert r.status_code == 200, r.text
    assert _opening(client, task) != "failed"
    first, again = _openings(client, task)
    assert again.payload["content"] == first.payload["content"]


def test_only_after_a_failure_and_only_by_someone_working_the_task(client):
    _, task = _task_from_message(client)
    early = client.post(
        f"/topics/{task}/opening", headers=session_auth_headers("alice")
    )
    assert early.status_code == 409
    _give_up(client, task)

    outsider = client.post(
        f"/topics/{task}/opening", headers=session_auth_headers("bob")
    )

    assert outsider.status_code == 403
    assert len(_openings(client, task)) == 1


def test_a_task_created_empty_has_no_first_turn(client):
    channel, _ = _task_from_message(client)
    r = client.post(
        f"/topics/{channel}/tasks",
        json={"title": "整理周报"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    assert _opening(client, r.json()["data"]["id"]) is None


def _age(client, task):
    """The first instruction has been tried and has not started for half an hour."""
    from app.domain.delivery.models import Delivery

    async def _aged():
        async with client.test_factory() as session:
            await session.execute(
                update(Delivery)
                .where(Delivery.conversation_id == uuid.UUID(task))
                .values(
                    state="pending",
                    attempts=3,
                    sent_at=None,
                    lease_until=None,
                    retry_at=None,
                    recorded_at=datetime.now(UTC) - timedelta(minutes=31),
                )
            )
            await session.commit()

    asyncio.run(_aged())


def _sweep(client):
    """The ledger's sweep, which runs on a clock, in the app's own loop."""
    from app.api.deps import get_chat_service, get_work_runner
    from app.domain.delivery.timer import deliver_due

    chat = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(
        lambda: deliver_due(chat.session_factory, chat=chat, runner=get_work_runner())
    )


def test_an_instruction_not_started_in_half_an_hour_gives_up(client):
    _, task = _task_from_message(client)
    _age(client, task)

    _sweep(client)

    assert _opening(client, task) == "failed"


def test_a_page_showing_the_task_hears_its_first_instruction_give_up(client):
    channel, task = _task_from_message(client)
    # Whatever the first attempt set going is over: what the page hears next is
    # the sweep's alone.
    wait_work_idle()
    wait_turn_idle(client, task)
    _age(client, task)

    with room_socket(client, task, "alice") as page:
        _drain(page)
        _sweep(client)

        assert _told(page, channel)
    assert _opening(client, task) == "failed"


def _opening_handed_over(client, task: str, *, read: bool, failed: bool = False):
    """The first instruction was written to the task's session; ``read``: the
    session read it (its echo settles the row as ``received``); ``failed``: the
    work it was read into then failed."""
    from app.domain.delivery.models import Delivery, NativeInput
    from app.domain.room_task.models import Task

    async def _hand_over():
        async with client.test_factory() as session:
            row = await session.scalar(
                select(Delivery).where(
                    Delivery.conversation_id == uuid.UUID(task),
                    Delivery.payload["purpose"].astext == "opening",
                )
            )
            attempt = uuid.uuid4()
            now = datetime.now(UTC)
            row.state = "received" if read else "uncertain"
            row.attempt_id = attempt
            row.attempts = 1
            task_row = await session.get(Task, uuid.UUID(task))
            session.add(
                NativeInput(
                    project_id=task_row.project_id,
                    conversation_id=uuid.UUID(task),
                    recipient_handle=row.recipient_handle,
                    harness="claude-code",
                    native_session_id="sess-opening",
                    input_id=uuid.uuid4(),
                    work_id=attempt,
                    delivery_id=row.id,
                    attempt_id=attempt,
                    registered_at=now,
                    accepted_at=now,
                    echoed_at=now if read else None,
                    failed_at=now if failed else None,
                    failure="error" if failed else None,
                )
            )
            await session.commit()

    asyncio.run(_hand_over())


def _session_declares(client, task: str, status: str):
    from app.domain.agent import session_state

    key = session_state.SeatKey(uuid.UUID(task), "cheese", "claude-code")

    async def _declare():
        await session_state.register(client.test_factory, key)
        async with client.test_factory() as session:
            await session_state.declare(session, key, status, datetime.now(UTC))
            await session.commit()

    asyncio.run(_declare())


def test_an_opening_written_and_not_yet_read_is_under_way(client):
    _, task = _task_from_message(client)

    _opening_handed_over(client, task, read=False)

    assert _opening(client, task) == "drafting"


def test_a_read_opening_is_under_way_until_its_session_stops_having_said_nothing(
    client,
):
    _, task = _task_from_message(client)

    _opening_handed_over(client, task, read=True)
    _session_declares(client, task, "running")
    assert _opening(client, task) == "drafting"

    _session_declares(client, task, "idle")
    assert _opening(client, task) == "failed"


def test_an_opening_read_into_work_that_failed_is_failed(client):
    _, task = _task_from_message(client)

    _opening_handed_over(client, task, read=True, failed=True)

    assert _opening(client, task) == "failed"
