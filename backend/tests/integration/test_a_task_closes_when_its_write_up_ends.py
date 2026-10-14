"""Accepting a task's last delivery lets its write-up finish before it closes.

The last step landing gives the task's AI teammate one turn to bring the task
document up to date and say what came of the task; the task closes when that
turn ends. The instruction for it is over as soon as the session takes the
turn, long before the turn is: the task used to close at that moment, and the
close stopped the write-up it was waiting for, which the room then read as a
turn that did not finish (dev, 2026-10-12 on).
"""

import uuid

import pytest

from app.api.deps import get_chat_service
from app.domain.delivery.agent import dispatch_pending
from app.main import app
from tests.conftest import wait_work_idle
from tests.integration.conftest import session_auth_headers
from tests.integration.test_a_last_step_lands_while_work_goes_on import _last_step_up
from tests.integration.test_accept_pr import _accept
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.integration.test_closing_a_task_stops_its_work import _interrupts, _until

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _status(client, task_id: str) -> str:
    return client.get(
        f"/topics/{task_id}/task", headers=session_auth_headers("alice")
    ).json()["data"]["status"]


def test_the_task_closes_after_its_write_up_ends_not_while_it_runs(
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

    stub_hooks.stops(task, "任务文档已更新，总结如上。")

    _until(
        lambda: _status(client, task_id) == "closed",
        "the task stayed open after its write-up ended",
    )
    assert _interrupts(stub_hooks, task) == []
