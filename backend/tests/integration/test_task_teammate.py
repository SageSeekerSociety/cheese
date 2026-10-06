"""Which AI teammate works a task.

The rules, stated before the code:

- a task made from a teammate's proposal is worked by that teammate;
- a message addressed to a teammate, turned into a task, is worked by the
  teammate it was addressed to;
- either way the task's first instruction goes to that teammate;
- a task is given only a teammate its project has.

On dev (2026-10-06) a person asked 芝士Opus in a 支线, accepted the task it
proposed, and the task was worked by the project's default 芝士 instead.
"""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    post_message,
    post_project,
    session_auth_headers,
)


def _channel_with_teammate(client) -> tuple[str, str, str]:
    """A project, a channel, and a second AI teammate seated in it."""
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    channel = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "可观测性"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    made = client.post(f"/projects/{project['id']}/agents", json={"handle": "opus"})
    assert made.status_code == 200, made.text
    seat = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{channel}/members",
        json={"handle": seat, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    return project["id"], channel, seat


def _worked_by(client, task: str) -> str | None:
    r = client.get(f"/topics/{task}/task", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]["agent_handle"]


def _instructed(client, task: str) -> list[str]:
    """Whom the platform has handed the task's instructions to."""

    async def read():
        async with client.test_factory() as db:
            rows = await db.execute(
                text(
                    "SELECT recipient_handle FROM deliveries "
                    "WHERE conversation_id = :task ORDER BY recorded_at"
                ),
                {"task": task},
            )
            return [handle for (handle,) in rows]

    return client.portal.call(read)


def test_a_task_a_teammate_proposed_is_worked_by_that_teammate(client):
    project, channel, seat = _channel_with_teammate(client)
    proposed = client.post(
        f"/topics/{channel}/task-proposals",
        json={"title": "堵住公开的调试接口", "summary": "调试接口要求登录"},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=project, topic_id=channel, agent_handle=seat
            )
        },
    )
    assert proposed.status_code == 200, proposed.text

    accepted = client.post(
        f"/topics/{channel}/task-proposals/{proposed.json()['data']['id']}/accept",
        headers=session_auth_headers("alice"),
    )

    assert accepted.status_code == 200, accepted.text
    task = accepted.json()["data"]["id"]
    assert _worked_by(client, task) == seat
    assert _instructed(client, task) == [seat]


def test_a_message_asking_a_teammate_turned_into_a_task_keeps_that_teammate(client):
    _project, channel, seat = _channel_with_teammate(client)
    asked = post_message(
        client, channel, "alice", {"content": f"<@{seat}> 看一下管理后台"}
    )

    upgraded = client.post(
        f"/blocks/{asked['id']}/upgrade", headers=session_auth_headers("alice")
    )

    assert upgraded.status_code == 200, upgraded.text
    task = upgraded.json()["data"]["id"]
    assert _worked_by(client, task) == seat
    assert _instructed(client, task) == [seat]


def test_a_task_is_given_only_a_teammate_its_project_has(client):
    _project, channel, seat = _channel_with_teammate(client)
    task = client.post(
        f"/topics/{channel}/tasks",
        json={"title": "整理日志"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]

    refused = client.patch(
        f"/topics/{task}/task",
        json={"agent_handle": "cheese-000000000000"},
        headers=session_auth_headers("alice"),
    )
    given = client.patch(
        f"/topics/{task}/task",
        json={"agent_handle": seat},
        headers=session_auth_headers("alice"),
    )

    assert refused.status_code == 422, refused.text
    assert given.status_code == 200, given.text
    assert _worked_by(client, task) == seat


_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_a_tasks_teammate_is_named_by_its_seat.py"
    )
)


def _migrate(client) -> None:
    spec = importlib.util.spec_from_file_location("_mig_task_seat", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            module.name_by_seat()

    async def run() -> None:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(apply)
            await s.commit()

    client.portal.call(run)


def test_a_task_from_before_seats_keeps_its_teammate(client):
    """Tasks carried over from rooms named their teammate by its handle; one
    naming a teammate the project no longer has goes back to its channel's."""
    _project, channel, seat = _channel_with_teammate(client)
    tasks = {
        stored: client.post(
            f"/topics/{channel}/tasks",
            json={"title": stored},
            headers=session_auth_headers("alice"),
        ).json()["data"]["id"]
        for stored in ("opus", seat, "kimi")
    }

    async def store() -> None:
        async with client.test_factory() as s:
            for stored, task in tasks.items():
                await s.execute(
                    text("UPDATE tasks SET agent_handle = :h WHERE id = :t"),
                    {"h": stored, "t": task},
                )
            await s.commit()

    client.portal.call(store)

    _migrate(client)

    assert _worked_by(client, tasks["opus"]) == seat
    assert _worked_by(client, tasks[seat]) == seat
    assert _worked_by(client, tasks["kimi"]) is None
