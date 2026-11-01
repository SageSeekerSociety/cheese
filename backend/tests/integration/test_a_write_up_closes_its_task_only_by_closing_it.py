"""Accepting a task's last delivery lets its write-up finish before it closes.

The last step landing tells the task's AI teammate to bring the task document
up to date, say what came of the task, and close it. The task closes when the
teammate closes it (`cheese_task(action=close)`): the instruction being handed over is
not that, and nor is the session stopping afterwards, since neither says the
write-up was done. A close that came with the instruction stopped the write-up
it was waiting for, which the room then read as work that did not finish (dev,
2026-10-12 on).
"""

import uuid

import pytest

from app.api.deps import get_chat_service
from app.domain.delivery.agent import dispatch_pending
from app.main import app
from tests.conftest import wait_work_idle
from tests.integration.conftest import session_auth_headers, task_agent_headers
from tests.integration.test_a_last_step_lands_while_work_goes_on import _last_step_up
from tests.integration.test_accept_pr import _accept
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.integration.test_closing_a_task_stops_its_work import _interrupts, _until
from tests.landing import teammate_seat

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _status(client, task_id: str) -> str:
    return client.get(
        f"/topics/{task_id}/task", headers=session_auth_headers("alice")
    ).json()["data"]["status"]


def test_the_task_closes_when_its_teammate_closes_it_not_while_it_writes(
    client, app_world, stub_hooks
):
    task_id, card, _ = _last_step_up(client, app_world)
    task = uuid.UUID(str(task_id))

    # The session takes the write-up and is still writing.
    def writing(topic_id, prompt, reply, *, agent=None):
        del reply
        stub_hooks.starts(topic_id, agent=agent)
        stub_hooks.acknowledges(topic_id, prompt, agent=agent)

    stub_hooks.emit_turn = writing

    accepted = _accept(client, card)
    assert accepted.status_code == 200, accepted.text
    chat = app.dependency_overrides[get_chat_service]()
    client.portal.call(
        lambda: dispatch_pending(
            chat.session_factory, chat=chat, runner=chat.work_runner
        )
    )
    _until(
        lambda: (
            any(key[0] == task for key in stub_hooks.sessions)
            and any(
                m.get("type") == "user" for m in stub_hooks._session_for(task).written
            )
        ),
        "the write-up never reached the task's teammate",
    )
    # The instruction's attempt is over: the session owns the turn now.
    wait_work_idle()

    assert _status(client, task_id) == "open", "the task closed mid write-up"
    assert _interrupts(stub_hooks, task) == [], "the write-up was cut off"

    # The session stopping says nothing about whether it wrote the task up.
    stub_hooks.stops(task, "任务文档已更新，总结如上。")
    wait_work_idle()
    assert _status(client, task_id) == "open", "the task closed on its own"

    # Its teammate closes it from its own session.
    project_id, seat = teammate_seat(client, task_id)
    closed = client.post(
        f"/topics/{task_id}/close",
        json={"conclusion": "做完了"},
        headers=task_agent_headers(project_id, task_id, seat),
    )
    assert closed.status_code == 200, closed.text
    assert _status(client, task_id) == "closed"
    assert _interrupts(stub_hooks, task) == []
