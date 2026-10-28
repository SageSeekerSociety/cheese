"""A room's task list says whether each task's AI teammate has a session running.

The orchestrator's patrol reads it to tell a seat in the middle of a long command
from one that stopped. It comes from what the session protocol declares, not from
the board: a teammate can have reported 「在做」 and be gone, or have said nothing
and be at work.
"""

import uuid

from tests.integration.conftest import open_task, post_project, room_agent_seat


def test_the_room_task_list_says_which_tasks_have_a_session_running(client):
    from app.domain.agent.harness.driven.lifecycle import RUNNING
    from app.domain.agent.session_state import SeatKey, declare, register

    project = post_project(client, {"name": "Sessions"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    seat = room_agent_seat(client, room)
    working = open_task(client, room, "在跑的会话", owner="alice")
    quiet = open_task(client, room, "没有会话", owner="alice")
    key = SeatKey(uuid.UUID(working["id"]), seat, "claude-code")

    async def run_it() -> None:
        await register(client.test_factory, key)
        async with client.test_factory() as session:
            await declare(session, key, RUNNING)
            await session.commit()

    client.portal.call(run_it)

    listed = client.get(f"/topics/{room}/tasks", params={"limit": 0})
    assert listed.status_code == 200, listed.text
    rows = {row["id"]: row for row in listed.json()["data"]["data"]}
    assert rows[working["id"]]["session_running"] is True
    assert rows[quiet["id"]]["session_running"] is False


def test_a_task_page_says_whether_its_session_is_running(client):
    """The task's own page moves its 「在做」 only while a session runs in it, so
    the page says so too — and a task whose teammate is not at work says no."""
    from app.domain.agent.harness.driven.lifecycle import RUNNING
    from app.domain.agent.session_state import SeatKey, declare, register
    from tests.integration.conftest import session_auth_headers

    project = post_project(client, {"name": "Sessions"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    seat = room_agent_seat(client, room)
    working = open_task(client, room, "在跑的会话", owner="alice")
    quiet = open_task(client, room, "没有会话", owner="alice")
    key = SeatKey(uuid.UUID(working["id"]), seat, "claude-code")

    async def run_it() -> None:
        await register(client.test_factory, key)
        async with client.test_factory() as session:
            await declare(session, key, RUNNING)
            await session.commit()

    client.portal.call(run_it)

    def page(task_id: str) -> dict:
        r = client.get(f"/topics/{task_id}/task", headers=session_auth_headers("alice"))
        assert r.status_code == 200, r.text
        return r.json()["data"]

    assert page(working["id"])["session_running"] is True
    assert page(quiet["id"])["session_running"] is False
