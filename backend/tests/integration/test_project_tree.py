"""Project expansion + topic tree (spec §4, §6; evals A1/A2/A4)."""

import asyncio
import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.models import AuthorType, Block, BlockKind
from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import open_task, post_project, session_auth_headers
from tests.support.living_doc import document_of


def _project(client, owner: str = "owner", **kw) -> dict:
    return post_project(client, json={"name": "P", **kw}, owner=owner).json()["data"]


def test_project_create_autocreates_root_topic(client):
    p = _project(client, owner="user-1", ai_mode="autonomous")
    assert p["ai_mode"] == "autonomous"
    assert p["owner_handle"] == "user-1"
    assert p["root_topic_id"] is not None

    # The root topic exists and is of kind "root".
    topics = client.get(f"/topics?project_id={p['id']}").json()["data"]["data"]
    roots = [t for t in topics if t["kind"] == "root"]
    assert len(roots) == 1
    assert roots[0]["id"] == p["root_topic_id"]


def _insert_block(client, project_id, topic_id, content) -> str:
    holder: dict[str, str] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:
            block = Block(
                project_id=uuid.UUID(project_id),
                conversation_id=uuid.UUID(topic_id),
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="user-1",
                content=content,
                refs=[],
            )
            session.add(block)
            await session.flush()
            holder["id"] = str(block.id)
            await session.commit()

    asyncio.run(_seed())
    return holder["id"]


def test_upgrade_block_to_topic(client, stub_hooks):
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "讨论"}
    ).json()["data"]
    block_id = _insert_block(
        client, p["id"], topic["id"], "我们要不要单独做一个数据清洗的模块"
    )

    r = client.post(
        f"/blocks/{block_id}/upgrade", headers=session_auth_headers("owner")
    )
    assert r.status_code == 200
    _wait_work_idle()
    new_topic = r.json()["data"]
    # Work inside the room, not a room of its own: upgrading a message in a room
    # makes a task, and a task names its room rather than a parent in a tree —
    # work does not nest, so there is no tree left to be in.
    assert new_topic["room_id"] == topic["id"]
    # Whoever upgraded the message owns the task it became.
    assert new_topic["owner_handle"] == "owner"

    # The upgraded message IS the task statement: the task's agent is handed it
    # to draft the task's document from.
    assert "我们要不要单独做一个数据清洗的模块" in stub_hooks.told

    # Re-upgrading the same block is idempotent: it returns the task already
    # created (so a double-click just navigates), not an error — and it leaves
    # nothing new in the room behind.
    before = len(client.get(f"/topics/{topic['id']}/blocks").json()["data"]["data"])
    r2 = client.post(
        f"/blocks/{block_id}/upgrade", headers=session_auth_headers("owner")
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["id"] == new_topic["id"]
    _wait_work_idle()
    after = client.get(f"/topics/{topic['id']}/blocks").json()["data"]["data"]
    assert len(after) == before


def test_a_task_is_named_by_its_own_session_or_its_owner(client):
    """升级出来的任务没有标题；给它起名字的是它自己的会话，或者它的负责人。

    地址是房间的 `/{room}/tasks/{task}/title`：任务不是地点，拿任务的 id 当房间
    的地址走不通。
    """
    p = _project(client)
    room = client.post("/topics", json={"project_id": p["id"], "title": "讨论"}).json()[
        "data"
    ]
    block_id = _insert_block(client, p["id"], room["id"], "把导入这段单独拆出来做")
    task = client.post(
        f"/blocks/{block_id}/upgrade", headers=session_auth_headers("owner")
    ).json()["data"]
    _wait_work_idle()
    assert task["title"] == "新任务"
    title = f"/topics/{task['id']}/title"

    # 房间自己的会话不替任务起名 —— 那是任务自己会话的事。
    room_session = {
        "X-Cheese-Token": mint_scoped_token(project_id=p["id"], topic_id=room["id"])
    }
    refused = client.post(title, json={"title": "别人起的"}, headers=room_session)
    assert refused.status_code == 403

    task_session = {
        "X-Cheese-Token": mint_scoped_token(project_id=p["id"], topic_id=task["id"])
    }
    r = client.post(title, json={"title": "拆导入"}, headers=task_session)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["title"] == "拆导入"

    tasks = client.get(f"/topics/{room['id']}/tasks").json()["data"]["data"]
    assert [t["title"] for t in tasks if t["id"] == task["id"]] == ["拆导入"]
    assert client.get(f"/topics/{room['id']}").json()["data"]["title"] == "讨论", (
        "给任务起名字改掉了整个房间的名字"
    )

    # 负责人改名走的是同一条路。
    renamed = client.post(
        title, json={"title": "导入"}, headers=session_auth_headers("owner")
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["title"] == "导入"
    assert client.get(f"/topics/{room['id']}").json()["data"]["title"] == "讨论"

    # 一个谁都不是的 id 名下没有对话。
    assert (
        client.post(
            f"/topics/{uuid.uuid4()}/title",
            json={"title": "谁"},
            headers=session_auth_headers("owner"),
        ).status_code
        == 404
    )


def test_archived_topic_is_frozen(client):
    # 归档后工作面冻结: no new task, no doc edit on an archived topic.
    #
    # 归档现在只有一条入口 —— 人点的那一下 (#442 decision 1)。这个测试以前借
    # 「采纳即归档」拿到归档状态；采纳不再归档之后，它显式走归档端点，测的东西
    # 反而更贴题了。
    p = _project(client, owner="alice")
    topic = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "交付物"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    r = client.post(
        f"/topics/{topic['id']}/archive",
        json={"by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    got = client.get(f"/topics/{topic['id']}").json()["data"]
    assert got["status"] == "archived"

    # Opening a task in a frozen topic is rejected.
    r = client.post(
        f"/topics/{topic['id']}/tasks",
        json={"title": "续作"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
    # Editing the frozen topic's doc is rejected.
    r = client.put(
        f"/documents/{document_of(client, topic['id'])}",
        json={"content": "改一下", "expected_version": 0},
    )
    assert r.status_code == 422


def test_upgrade_on_archived_topic_rejected(client):
    # Consistent with opening a task or editing the doc: a frozen topic accepts
    # no new work (§6.3).
    p = _project(client, owner="alice")
    topic = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "交付"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    block_id = _insert_block(client, p["id"], topic["id"], "某条结论")
    assert (
        client.post(
            f"/topics/{topic['id']}/archive",
            json={"by": "alice"},
            headers=session_auth_headers("alice"),
        ).status_code
        == 200
    )
    r = client.post(
        f"/blocks/{block_id}/upgrade",
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422


def test_a_private_message_does_not_leave_the_chat(client):
    # 私聊里的消息不转成任务，也不转成频道：它留在私聊里。
    p = _project(client, owner="user-1")
    priv = client.get(
        f"/projects/{p['id']}/private-chat", params={"user_handle": "user-1"}
    ).json()["data"]
    block_id = _insert_block(
        client, p["id"], priv["id"], "我们其实该单独做个数据清洗模块"
    )
    before = client.get(f"/topics?project_id={p['id']}").json()["data"]["data"]
    r = client.post(
        f"/blocks/{block_id}/upgrade", headers=session_auth_headers("user-1")
    )
    assert r.status_code == 422, r.text
    after = client.get(f"/topics?project_id={p['id']}").json()["data"]["data"]
    assert len(after) == len(before)
    assert client.get(f"/projects/{p['id']}/tasks").json()["data"]["data"] == []


def test_open_and_conclude_a_task(client):
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "大话题"}
    ).json()["data"]

    sub = open_task(client, topic["id"], "实现数据清洗", owner="owner", start=False)
    # A task in the room, not a room of its own: it names the room it hangs
    # in, and it opens as work that is still going.
    assert sub["room_id"] == topic["id"]
    assert sub["status"] == "open"

    # It shows up in the room's task list — `children` is rooms under rooms,
    # which is exactly what a task is not.
    tasks = client.get(f"/topics/{topic['id']}/tasks").json()["data"]["data"]
    assert any(t["id"] == sub["id"] for t in tasks)

    # Its owner says it is over.
    r = client.post(
        f"/topics/{sub['id']}/close",
        json={"conclusion": "数据清洗完成，去重后剩 8000 条"},
        headers=session_auth_headers("owner"),
    )
    assert r.status_code == 200, r.text
    _wait_work_idle()
    closed = r.json()["data"]
    assert closed["status"] == "closed"
    assert closed["conclusion"] == "数据清洗完成，去重后剩 8000 条"

    # 结论住在任务上, so the room's own living doc is not rewritten behind its
    # back — the room keeps its doc, the way every other place does.
    doc = client.get(f"/documents/{document_of(client, topic['id'])}").json()["data"]
    assert doc is None or "数据清洗完成" not in doc["content"]


def test_concluding_something_that_is_not_a_task_fails(client):
    """房间不是任务,收不了自己。"""
    p = _project(client)
    root_id = _project_root(client, p["id"])
    r = client.post(f"/topics/{root_id}/close", json={"conclusion": "x"})
    assert r.status_code == 404


def _project_root(client, project_id) -> str:
    topics = client.get(f"/topics?project_id={project_id}").json()["data"]["data"]
    return next(t["id"] for t in topics if t["kind"] == "root")
