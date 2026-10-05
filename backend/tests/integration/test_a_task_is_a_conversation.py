"""A task is a conversation of its own, addressed by its own id.

Everything said, written and kept in a task is reached as `/topics/{task}/…`,
the same routes a room's conversation uses; the task itself is
`/topics/{task}/task`. It has no roster and no archive of its own: who may see
it is whoever may see its room.
"""

import uuid
from datetime import UTC, datetime

from app.domain.project.models import Project
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic, TopicKind
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    a_team,
    open_task,
    post_project,
    session_auth_headers,
)


def _project(client) -> str:
    return post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]


def _room(client, project_id: str, title: str = "运维") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _card(client, room_id: str, title: str = "接口分页") -> dict:
    return open_task(client, room_id, title, start=False)


def test_a_tasks_id_addresses_its_conversation(client):
    """读和写都走同一个地址：任务自己的 id。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    card = _card(client, room_id)
    alice = session_auth_headers("alice")

    assert client.get(f"/topics/{card['id']}/blocks", headers=alice).status_code == 200
    assert (
        client.get(f"/topics/{card['id']}/document", headers=alice).status_code == 200
    )
    renamed = client.post(
        f"/topics/{card['id']}/title", json={"title": "换个名"}, headers=alice
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["title"] == "换个名"
    # The task's record is its own route; the room's record is not a task's.
    assert client.get(f"/topics/{card['id']}", headers=alice).status_code == 404


def test_a_task_reads_with_its_board_line_and_conversation(client):
    """卡本身、它的看板那一格、它的对话，一条请求全给。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    card = _card(client, room_id, title="接口分页")

    r = client.get(f"/topics/{card['id']}/task")
    assert r.status_code == 200, r.text
    got = r.json()["data"]
    assert got["id"] == card["id"]
    assert got["title"] == "接口分页"
    # 状态词是后端算的那一句，和它在看板上显示的是同一句。
    assert got["presentation"]["phrase"]
    assert got["blocks"] == []


def test_saying_something_on_a_card_lands_on_the_card(client):
    """负责人在任务里说的话落在这条活的时间线上，不在房间主线上 —— 看这条活的
    人下周打开还看得见。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    card = _card(client, room_id)

    r = client.post(
        f"/topics/{card['id']}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "这条先别动 routes"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()

    said = client.get(f"/topics/{card['id']}/task").json()["data"]["blocks"]
    assert "这条先别动 routes" in [b["content"] for b in said]
    # 房间主线上没有这句话。
    room_line = client.get(f"/topics/{room_id}/blocks").json()["data"]["data"]
    assert all("这条先别动 routes" not in b["content"] for b in room_line)


def test_file_claim_routes_are_retired(client):
    project_id = _project(client)
    room_id = _room(client, project_id)
    task = _card(client, room_id)
    for suffix in ("claim", f"tasks/{task['id']}/claim"):
        response = client.post(f"/topics/{room_id}/{suffix}", json={"paths": ["a.py"]})
        assert response.status_code == 404


def test_a_card_that_kept_its_old_topic_id_still_renders(client):
    """存量：一条活的 id 曾经是一个 `topics` 行的 id（migration c4a7e91b2d05 保号
    搬家，那些 `topics` 行同一次被删掉）。

    这一批不做转换迁移——它们就是历史卡。要保证的只有一件事：**不炸**。它照常出现
    在房间的清单里、照常读得出来。
    """
    ids: dict[str, uuid.UUID] = {}
    inherited_id = uuid.uuid4()

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(team_id=await a_team(s), name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="老房间", kind=TopicKind.topic)
            s.add(room)
            await s.flush()
            await s.flush()
            s.add(
                Task(
                    id=inherited_id,
                    project_id=project.id,
                    room_id=room.id,
                    title="从前是个话题",
                    created_at=datetime(2026, 7, 1, tzinfo=UTC),
                )
            )
            await s.commit()
            ids["room"] = room.id

    client.portal.call(_seed)
    room_id = ids["room"]

    listed = client.get(f"/topics/{room_id}/tasks").json()["data"]["data"]
    assert [t["id"] for t in listed] == [str(inherited_id)]
    assert listed[0]["presentation"]["phrase"]

    one = client.get(f"/topics/{inherited_id}/task")
    assert one.status_code == 200, one.text
    assert one.json()["data"]["title"] == "从前是个话题"

    assert client.get(f"/topics/{inherited_id}").status_code == 404
