"""Where a channel's main line opened a task, whether the task is done is the
main line's call, not the task's own teammate's.

The rules, as a person would state them:

- When a delivery of such a task is adopted and lands, the task stays open and
  the main line is told once: which task, who adopted it, that it is still
  open. The task's own teammate is not asked to judge whether it is done.
- When the main line itself adopts on the reviewer's word and the merge lands
  in that same call, it reads this in the call's reply and is not woken for it
  as well.
- The task's own teammate cannot close such a task; it hands work in for
  review, or reports to the main line that it is done. A task nobody
  coordinates is closed by its teammate as before.
- The main line closes it with a conclusion: the task closes, its own
  conversation records how it ended, and the main line says the result itself
  — the platform records no end line where the task came from, and the main
  line is not woken about an end it caused itself. A person closing it is
  recorded there as before; the main line is not woken for it, and reads it
  in its next input.
- When the main line adopts on the reviewer's word, the platform records no
  adoption line where the task came from: the main line says it. A person's
  own click is recorded there as before.
- In a project whose deploy reports back, the main line's close after a merge
  waits until that merge is live, then the task closes with the conclusion the
  main line gave, its teammate is not asked to write it up, and the main line
  is told the task ended to say so; the platform records no end line where the
  task came from.
- A task closed after its merge landed can be reopened by the main line and
  worked on again.
- A teammate that finished with nothing to hand in reports so, and the main
  line hears it.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.domain.agent.models import AgentTurn
from app.domain.delivery.models import Delivery
from app.domain.room_task.closing import CLOSES_TASK
from app.domain.room_task.models import Task
from tests.delivery import delivery_task_id
from tests.integration.conftest import (
    mint_scoped_token,
    post_message,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)
from tests.integration.test_a_delivery_is_told_where_the_task_came_from import (
    _file,
    _happened,
    _told,
)
from tests.integration.test_a_merged_task_waits_for_its_deploy import (
    _reported,
    _ship,
)
from tests.integration.test_accept_pr import (
    REPO,
    _accept,
    _give_card_a_pr,
    _make_project,
    _make_topic,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.integration.test_the_teammate_that_opened_a_task_hears_how_it_goes import (
    _next_input_of_the_main_line,
    _opened,
    _told_opener,
)
from tests.landing import teammate_seat

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


@pytest.fixture
def reports_back(monkeypatch):
    """The test project's repository is one whose deploy reports here."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "task_deploy_wait_repositories", [REPO])
    monkeypatch.setattr(settings, "platform_repository", REPO)


def _project_of(client, task_id: uuid.UUID) -> str:
    async def read():
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            assert task is not None
            return str(task.project_id)

    return client.portal.call(read)


def _as_main_line(
    client, room: str, task_id: uuid.UUID, method: str, path: str, body=None
):
    seat = room_agent_seat(client, room)
    token = mint_scoped_token(
        project_id=_project_of(client, task_id), topic_id=room, agent_handle=seat
    )
    bearer = client.headers.pop("Authorization", None)
    try:
        return client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer


def _as_its_teammate(client, task_id: uuid.UUID, method: str, path: str, body=None):
    project_id, seat = teammate_seat(client, str(task_id))
    bearer = client.headers.pop("Authorization", None)
    try:
        return client.request(
            method,
            path,
            json=body,
            headers=task_agent_headers(project_id, task_id, seat),
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer


def _number(client, task_id: uuid.UUID) -> int:
    async def read():
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            assert task is not None and task.number is not None
            return task.number

    return client.portal.call(read)


def _told_the_task(client, task_id: uuid.UUID) -> list[dict]:
    """The instructions kept for the task's own teammate, oldest first."""

    async def read():
        async with client.test_factory() as session:
            return [
                dict(payload)
                for payload in await session.scalars(
                    select(Delivery.payload)
                    .where(Delivery.conversation_id == task_id)
                    .order_by(Delivery.recorded_at)
                )
            ]

    return client.portal.call(read)


def _task(client, task_id: uuid.UUID) -> dict:
    return client.get(
        f"/topics/{task_id}/task", headers=session_auth_headers("alice")
    ).json()["data"]


def _blocks(client, conversation: uuid.UUID) -> list[dict]:
    return client.get(
        f"/topics/{conversation}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]


def _lines_of(client, room: str, task_id: uuid.UUID, event_type: str) -> list[dict]:
    """The platform's lines of ``event_type`` about the task where it came
    from."""
    return [
        line
        for line in _told(client, room, task_id)
        if (line.get("meta") or {}).get("event_type") == event_type
    ]


def _filed_with_a_pr(client, app_world, room: str) -> str:
    fake = app_world["fake"]
    card = _file(client, room)
    head = _give_card_a_pr(client, app_world, room, card, 7)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[7] = "merge-sha-1"
    return card


def _adopted(client, app_world) -> tuple[str, uuid.UUID]:
    """A task the main line opened handed a version in, and its reviewer
    adopted it on the page: the merge landed."""
    room = _make_topic(client, _make_project(client))
    task_id = _opened(client, room)
    card = _filed_with_a_pr(client, app_world, room)
    accepted = _accept(client, card)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "accepted"
    return room, task_id


def _main_line_closes(client, room: str, task_id: uuid.UUID, conclusion: str):
    closed = _as_main_line(
        client,
        room,
        task_id,
        "POST",
        f"/topics/{room}/tasks/{_number(client, task_id)}/close",
        {"conclusion": conclusion},
    )
    assert closed.status_code == 200, closed.text
    return closed.json()["data"]


def test_an_adopted_delivery_is_told_to_the_main_line_and_the_task_stays_open(
    client, app_world
):
    room, task_id = _adopted(client, app_world)

    filed, landed = _told_opener(client, room)
    assert "第 1 版" in filed
    assert "采纳" in landed and "任务还开着" in landed
    assert "cheese_task(action=close)" in landed
    assert _task(client, task_id)["status"] == "open"
    # Its teammate is not asked to judge whether the task is done.
    assert not [
        told
        for told in _told_the_task(client, task_id)
        if "由你判断任务做没做完" in (told.get("content") or "")
    ]


def _say_then_wake(client, room: str, author: str, words: str) -> None:
    """``author`` says ``words`` in the main line, and the main line's turn for
    that message is running."""
    seat = room_agent_seat(client, room)
    said = uuid.UUID(post_message(client, room, author, {"content": words})["id"])

    async def wake():
        async with client.test_factory() as db:
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=uuid.UUID(room),
                    continuation_id=said,
                    agent_handle=seat,
                    author=author,
                    content=words,
                    started_at=datetime.now(UTC),
                )
            )
            await db.commit()

    client.portal.call(wake)


def test_the_main_line_adopting_reads_it_in_its_reply_and_is_not_woken(
    client, app_world
):
    room = _make_topic(client, _make_project(client))
    task_id = _opened(client, room)
    _filed_with_a_pr(client, app_world, room)
    _say_then_wake(client, room, "alice", "可以了 合并吧")

    adopted = _as_main_line(
        client,
        room,
        task_id,
        "POST",
        f"/topics/{room}/tasks/{_number(client, task_id)}/accept",
    )

    assert adopted.status_code == 200, adopted.text
    data = adopted.json()["data"]
    assert data["status"] == "accepted"
    assert "任务还开着" in data["next"]
    # Only the version handed in waits for it: the landing was its reply.
    assert len(_told_opener(client, room)) == 1
    # It says the adoption itself; where the task came from records no line
    # for it, and the task itself shows its delivery adopted.
    assert not _lines_of(client, room, task_id, "task_landed")
    assert _task(client, task_id)["card"]["status"] == "accepted"


def test_a_person_adopting_is_recorded_where_the_task_came_from(client, app_world):
    room, task_id = _adopted(client, app_world)

    [landed] = _lines_of(client, room, task_id, "task_landed")
    assert _happened(landed)
    assert landed["content"].startswith("<@alice>")


def test_its_own_teammate_cannot_close_a_task_the_main_line_coordinates(
    client, app_world
):
    room, task_id = _adopted(client, app_world)

    refused = _as_its_teammate(
        client, task_id, "POST", f"/topics/{task_id}/close", {"conclusion": "做完了"}
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskCoordinatedClosedByMainLine"
    assert _task(client, task_id)["status"] == "open"


def test_a_task_nobody_coordinates_is_still_closed_by_its_teammate(client, app_world):
    room = _make_topic(client, _make_project(client))
    task_id = _opened(client, room, by_teammate=False)
    card = _filed_with_a_pr(client, app_world, room)
    assert _accept(client, card).status_code == 200

    closed = _as_its_teammate(
        client, task_id, "POST", f"/topics/{task_id}/close", {"conclusion": "做完了"}
    )

    assert closed.status_code == 200, closed.text
    assert _task(client, task_id)["status"] == "closed"


def test_the_main_line_closes_it_and_is_not_woken_about_its_own_close(
    client, app_world
):
    room, task_id = _adopted(client, app_world)
    told = len(_told_opener(client, room))

    _main_line_closes(client, room, task_id, "报名表只留姓名和学号")

    task = _task(client, task_id)
    assert task["status"] == "closed"
    assert task["conclusion"] == "报名表只留姓名和学号"
    # The main line says the result; the platform records no end line there.
    assert not _lines_of(client, room, task_id, "task_ended")
    assert len(_told_opener(client, room)) == told
    # The task's own conversation records how it ended.
    assert any(
        (b.get("meta") or {}).get("action") == "task_closed"
        and "报名表只留姓名和学号" in b["content"]
        for b in _blocks(client, task_id)
    )


def test_a_person_closing_it_is_recorded_and_the_main_line_reads_it_unwoken(
    client, app_world
):
    """A person who closes the task said so by clicking: the platform records
    the end where the task came from, and the main line is not woken to say
    it again — it reads who closed it and how in its next input."""
    room, task_id = _adopted(client, app_world)
    told = len(_told_opener(client, room))

    closed = client.post(
        f"/topics/{task_id}/close",
        json={"conclusion": "改用纸质表"},
        headers=session_auth_headers("alice"),
    )

    assert closed.status_code == 200, closed.text
    [ending] = _lines_of(client, room, task_id, "task_ended")
    assert _happened(ending)
    assert len(_told_opener(client, room)) == told
    [heard] = [
        said
        for said in _next_input_of_the_main_line(client, room)
        if "关闭了任务" in said
    ]
    assert "alice" in heard and "改用纸质表" in heard


def test_a_close_after_a_merge_waits_for_its_deploy_then_ends_with_that_conclusion(
    client, app_world, reports_back
):
    _reported(client)
    room, task_id = _adopted(client, app_world)
    told = len(_told_opener(client, room))

    waiting = _main_line_closes(client, room, task_id, "报名表只留姓名和学号")

    assert waiting["deploying"] is True
    assert _task(client, task_id)["status"] == "open"
    assert _task(client, task_id)["conclusion"] is None

    released = _ship(
        client, [{"sha": "a2", "message": "feat(form): trim the sign-up form (#7)"}]
    )

    assert released == [task_id]
    task = _task(client, task_id)
    assert task["status"] == "closed"
    assert task["conclusion"] == "报名表只留姓名和学号"
    # Its teammate was not asked to write it up.
    assert not [
        told for told in _told_the_task(client, task_id) if told.get(CLOSES_TASK)
    ]
    # The main line did not cause this moment, and hears the end once, to say
    # it; the platform records no end line where the task came from.
    *_, ended = _told_opener(client, room)
    assert len(_told_opener(client, room)) == told + 1
    assert "报名表只留姓名和学号" in ended
    assert not _lines_of(client, room, task_id, "task_ended")
    assert _ship(client, [{"sha": "a2", "message": "feat: x (#7)"}]) == []
    assert len(_told_opener(client, room)) == told + 1


def test_a_task_closed_after_its_merge_is_reopened_and_worked_on_again(
    client, app_world
):
    room, task_id = _adopted(client, app_world)
    _main_line_closes(client, room, task_id, "先这样")

    reopened = _as_main_line(
        client,
        room,
        task_id,
        "POST",
        f"/topics/{room}/tasks/{_number(client, task_id)}/reopen",
    )

    assert reopened.status_code == 200, reopened.text
    assert _task(client, task_id)["status"] == "open"
    told = _as_main_line(
        client,
        room,
        task_id,
        "POST",
        f"/topics/{room}/tasks/{_number(client, task_id)}/tell",
        {"content": "学号改成选填"},
    )
    assert told.status_code == 200, told.text
    assert any(
        "学号改成选填" in (said.get("content") or "")
        for said in _told_the_task(client, task_id)
    )


def test_a_teammate_done_with_nothing_to_hand_in_reports_it_to_the_main_line(
    client, app_world
):
    room = _make_topic(client, _make_project(client))
    task_id = _opened(client, room)
    assert delivery_task_id(client, room) == task_id

    reported = _as_its_teammate(
        client,
        task_id,
        "POST",
        f"/topics/{task_id}/report",
        {"kind": "done", "content": "调研结论已写进实况文档：用学号登录"},
    )

    assert reported.status_code == 200, reported.text
    assert reported.json()["data"]["reported"] == "done"
    [said] = _told_opener(client, room)
    assert "已做完" in said
    assert "调研结论已写进实况文档：用学号登录" in said
    assert "cheese_task(action=close)" in said
