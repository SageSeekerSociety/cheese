"""Creating a 2.0 project from a 赛题.

"在赛题里创建项目" only means something if the project remembers which 赛题 it
came from — otherwise it is indistinguishable from one made anywhere else.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.domain.task.models import TaskStage
from tests.conftest import seed_task_with_protocol, start_task_project
from tests.integration.conftest import post_project

OWNER = "owner"


def _create(client, **body):
    return post_project(client, json={"name": "赛题项目", **body}, owner=OWNER)


def _started(client, task_id: int, handle: str = OWNER) -> str:
    return start_task_project(client, task_id, handle=handle)


def test_a_project_created_from_a_task_remembers_it(client):
    task_id = seed_task_with_protocol(client)
    project_id = _started(client, task_id)
    body = client.get(f"/projects/{project_id}").json()["data"]
    assert body["external_task_id"] == task_id


def test_a_project_created_from_the_rail_belongs_to_no_task(client):
    r = _create(client)
    assert r.status_code == 200
    assert r.json()["data"]["external_task_id"] is None


def test_a_task_lists_the_projects_made_from_it(client):
    task_id, other = seed_task_with_protocol(client), seed_task_with_protocol(client)
    made = [_started(client, task_id, handle) for handle in (OWNER, "second-taker")]
    _started(client, other)

    body = client.get(f"/projects/by-task/{task_id}").json()["data"]
    assert body["total"] == 2
    assert {p["id"] for p in body["data"]} == set(made)
    # A 赛题 nobody has started shows nothing, rather than everything.
    assert client.get("/projects/by-task/12345").json()["data"]["total"] == 0


def test_naming_a_task_when_making_a_project_is_refused(client):
    """开始做这道题才会用它建项目：项目带着题目的访问权和资源包，不能凭一个题号
    在新建项目时拿到。带着题号来的（部署前打开的旧页面）被拒，什么也不建。"""
    task_id = seed_task_with_protocol(client)

    r = _create(client, external_task_id=task_id)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["i18n"]["key"] == "projectFromTaskByStarting"
    assert client.get(f"/projects/by-task/{task_id}").json()["data"]["total"] == 0


def _add_stages(client, task_id: int, names: list[str]) -> None:
    async def _seed() -> None:
        async with client.test_factory() as session:
            due = datetime.now(UTC) + timedelta(days=7)
            for index, name in enumerate(names):
                session.add(
                    TaskStage(task_id=task_id, index=index, name=name, due_at=due)
                )
            await session.commit()

    asyncio.run(_seed())


def test_a_project_from_a_staged_task_shows_its_stages(client):
    """The overview's timeline: each stage with its due date and where the
    project's claim stands on it."""
    task_id = seed_task_with_protocol(client)
    _add_stages(client, task_id, ["需求分析", "实现"])
    project_id = _started(client, task_id)

    stages = client.get(f"/projects/{project_id}").json()["data"]["challenge_stages"]
    assert [s["name"] for s in stages] == ["需求分析", "实现"]
    assert all(s["due_at"] for s in stages)
    assert {s["status"] for s in stages} == {"NOT_SUBMITTED"}


def test_a_project_from_an_unstaged_task_shows_no_stages(client):
    project_id = _started(client, seed_task_with_protocol(client))

    body = client.get(f"/projects/{project_id}").json()["data"]
    assert body["challenge_stages"] == []
