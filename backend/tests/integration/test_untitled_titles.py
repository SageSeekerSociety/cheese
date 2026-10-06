"""An unnamed task is flagged, so each screen names it in its reader's
language instead of showing the stored placeholder.

A message upgraded into a task opens it unnamed; a title given later, by
anyone, ends that.

The migration that adds the flag to tasks is run as ``alembic upgrade`` runs
it, on rows written before it: a never-renamed upgraded task becomes
``placeholder`` and everything else ``human``.
"""

import asyncio
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.conftest import wait_work_idle
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_project_tree import _insert_block

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "f9be82f700e2_task_title_source.py"
)


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "讨论"}
    ).json()["data"]
    return project["id"], room["id"]


def _upgraded_task(client, project_id: str, room_id: str) -> dict:
    block_id = _insert_block(client, project_id, room_id, "把导入这段单独拆出来做")
    task = client.post(
        f"/blocks/{block_id}/upgrade", headers=session_auth_headers("alice")
    ).json()["data"]
    wait_work_idle()
    return task


def _listed(client, room_id: str, task_id: str) -> dict:
    tasks = client.get(f"/topics/{room_id}/tasks").json()["data"]["data"]
    return next(t for t in tasks if t["id"] == task_id)


def test_an_upgraded_task_is_unnamed_until_it_is_given_a_title(client):
    project_id, room_id = _room(client)
    task = _upgraded_task(client, project_id, room_id)

    assert task["title_source"] == "placeholder"
    assert _listed(client, room_id, task["id"])["title_source"] == "placeholder"

    renamed = client.post(
        f"/topics/{task['id']}/title",
        json={"title": "拆导入"},
        headers=session_auth_headers("alice"),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["title_source"] == "human"
    listed = _listed(client, room_id, task["id"])
    assert (listed["title"], listed["title_source"]) == ("拆导入", "human")


def test_a_task_named_by_a_person_who_typed_the_placeholder_is_still_named(client):
    project_id, room_id = _room(client)
    task = _upgraded_task(client, project_id, room_id)

    renamed = client.post(
        f"/topics/{task['id']}/title",
        json={"title": "新任务"},
        headers=session_auth_headers("alice"),
    )

    assert renamed.json()["data"]["title"] == "新任务"
    assert renamed.json()["data"]["title_source"] == "human"


def _upgrade(client) -> None:
    spec = importlib.util.spec_from_file_location("_mig_task_title", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def _apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

    async def _run() -> None:
        async with client.test_factory() as s:
            # Before it, an unnamed task was stored under the room placeholder.
            await s.execute(
                text(
                    "UPDATE tasks SET title = '新话题'"
                    " WHERE title_source = 'placeholder'"
                )
            )
            await s.execute(text("ALTER TABLE tasks DROP COLUMN title_source"))
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    asyncio.run(_run())


def test_the_migration_marks_tasks_that_were_never_named(client):
    project_id, room_id = _room(client)
    untouched = _upgraded_task(client, project_id, room_id)
    named = _upgraded_task(client, project_id, room_id)
    client.post(
        f"/topics/{named['id']}/title",
        json={"title": "拆"},
        headers=session_auth_headers("alice"),
    )

    _upgrade(client)

    assert _listed(client, room_id, untouched["id"])["title_source"] == "placeholder"
    assert _listed(client, room_id, named["id"])["title_source"] == "human"
