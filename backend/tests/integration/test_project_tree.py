"""Project expansion + topic tree (spec §4, §6; evals A1/A2/A4)."""

import asyncio
import uuid

from app.domain.block.models import AuthorType, Block, BlockKind
from tests.conftest import wait_work_idle as _wait_work_idle


def _project(client, **kw) -> dict:
    body = {"name": "P", **kw}
    return client.post("/projects", json=body).json()["data"]


def test_project_create_autocreates_root_topic(client):
    p = _project(client, owner_handle="user-1", ai_mode="autonomous")
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
                topic_id=uuid.UUID(topic_id),
                kind=BlockKind.message,
                author_type=AuthorType.human,
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


def test_upgrade_block_to_topic(client):
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "讨论"}
    ).json()["data"]
    block_id = _insert_block(
        client, p["id"], topic["id"], "我们要不要单独做一个数据清洗的模块"
    )

    r = client.post(f"/blocks/{block_id}/upgrade", json={"created_by": "user-1"})
    assert r.status_code == 200
    _wait_work_idle()  # kickoff runs in the background; don't race its writes
    new_topic = r.json()["data"]
    # Work inside the room, not a room of its own: upgrading a message in a room
    # dispatches a thread, and a thread names its room rather than a parent in a
    # tree — work does not nest, so there is no tree left to be in.
    assert new_topic["room_id"] == topic["id"]

    # The upgraded block IS the task statement, kept verbatim on the card —
    # the same place a dispatched brief goes.
    listed = client.get(f"/topics/{topic['id']}/tasks").json()["data"]["data"]
    card = next(t for t in listed if t["id"] == new_topic["id"])
    assert "我们要不要单独做一个数据清洗的模块" in card["brief"]

    # The ROOM is what runs a turn: a thread is a 分身 in the room's session and
    # has no session of its own, so the thread starts with nothing said in it.
    msgs = _card_messages(client, topic["id"], new_topic["id"])
    assert not msgs, f"这条活自己跑了一轮——它没有会话，这是在起容器：{msgs}"
    room_msgs = _messages(client, topic["id"])
    assert room_msgs and all(b["author_type"] == "human" for b in room_msgs)
    activity = client.get(f"/topics/{topic['id']}/blocks").json()["data"]["data"]
    assert any(
        b["author_type"] == "ai" and (b.get("meta") or {}).get("progress")
        for b in activity
    )

    # Re-upgrading the same block is idempotent: it returns the topic already
    # created (so a double-click just navigates), not an error — and it does
    # NOT wake the room a second time.
    r2 = client.post(f"/blocks/{block_id}/upgrade", json={})
    assert r2.status_code == 200
    assert r2.json()["data"]["id"] == new_topic["id"]
    _wait_work_idle()
    assert len(_messages(client, topic["id"])) == len(room_msgs)


def _messages(client, room_id: str) -> list[dict]:
    blocks = client.get(f"/topics/{room_id}/blocks").json()["data"]["data"]
    return [b for b in blocks if b["kind"] == "message"]


def _card_messages(client, room_id: str, task_id: str) -> list[dict]:
    """一张卡上说过的话 —— 经过它所在的房间读，卡不是地点。"""
    r = client.get(f"/topics/{room_id}/tasks/{task_id}")
    assert r.status_code == 200, r.text
    return [b for b in r.json()["data"]["blocks"] if b["kind"] == "message"]


def _record_screens(stub_hooks) -> list[str]:
    """每一次「起一块屏幕」的 topic id。起屏幕就是起容器，这是唯一看得见它的地方。"""
    seen: list[str] = []
    original = stub_hooks.ensure_ready

    async def _spy(**kw):
        seen.append(str(kw.get("topic_id")))
        return await original(**kw)

    stub_hooks.ensure_ready = _spy
    return seen


def test_upgrading_a_message_wakes_the_room_to_raise_the_worker(client, stub_hooks):
    """讨论升级出来的是房间里的一条活，而活没有自己的会话可以叫醒。

    这条路和「结论卡打回」是同一颗雷的两个引信：朝一条活的 id 开轮次，平台就得为它
    起一整个容器 —— 正是「一条活 = 房间会话里的一个分身」拆掉的东西。所以轮次落在
    房间，提示词里带着房间起分身所需要的一切：活的 id、简报原文、起名和认领怎么做。
    """
    p = _project(client)
    room = client.post("/topics", json={"project_id": p["id"], "title": "讨论"}).json()[
        "data"
    ]
    block_id = _insert_block(client, p["id"], room["id"], "把导入这段单独拆出来做")

    screens = _record_screens(stub_hooks)
    r = client.post(f"/blocks/{block_id}/upgrade", json={"created_by": "user-1"})
    assert r.status_code == 200
    thread = r.json()["data"]
    _wait_work_idle()

    assert thread["id"] not in screens, "为一条活起了屏幕——这是在复活容器"
    assert screens == [room["id"]], f"叫醒的不是房间：{screens}"
    prompt = stub_hooks.last_prompt or ""
    assert thread["id"] in prompt, "不给 task id，房间没法 bind，也没法给它起名字"
    assert "把导入这段单独拆出来做" in prompt, "简报原文没带过去，分身就没东西可读"
    assert "bind" in prompt, "不说 bind，这条活在界面上永远是「没人做」"


def test_a_room_names_its_own_thread(client):
    """升级出来的活是没有标题的，而唯一能给它起名字的是房间。

    房间自己的地址是 `/{room}/tasks/{task}/title`：这一轮的 token 是按房间签的，
    直接拿活的 id 当地址会被判成跨话题。
    """
    p = _project(client)
    room = client.post("/topics", json={"project_id": p["id"], "title": "讨论"}).json()[
        "data"
    ]
    block_id = _insert_block(client, p["id"], room["id"], "把导入这段单独拆出来做")
    thread = client.post(
        f"/blocks/{block_id}/upgrade", json={"created_by": "user-1"}
    ).json()["data"]
    _wait_work_idle()
    assert thread["title"] == "新话题"

    r = client.post(
        f"/topics/{room['id']}/tasks/{thread['id']}/title", json={"title": "拆导入"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["title"] == "拆导入"

    tasks = client.get(f"/topics/{room['id']}/tasks").json()["data"]["data"]
    assert [t["title"] for t in tasks if t["id"] == thread["id"]] == ["拆导入"]
    assert client.get(f"/topics/{room['id']}").json()["data"]["title"] == "讨论", (
        "给一条活起名字改掉了整个房间的名字"
    )

    # 人改名走的是同一条路 —— 活没有第二个地址，所以人和芝士说的是同一句话。
    renamed = client.post(
        f"/topics/{room['id']}/tasks/{thread['id']}/title", json={"title": "导入"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["title"] == "导入"
    assert client.get(f"/topics/{room['id']}").json()["data"]["title"] == "讨论"

    # 拿活的 id 当房间的地址走不通：那个 id 名下没有地点。
    assert (
        client.post(
            f"/topics/{thread['id']}/tasks/{thread['id']}/title",
            json={"title": "自己来"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/topics/{room['id']}/tasks/{uuid.uuid4()}/title", json={"title": "谁"}
        ).status_code
        == 404
    )


def test_upgrade_doc_node_to_subtopic(client):
    # 自上而下拆解 (eval A2): a paragraph in the room's doc is upgraded into a
    # thread of work, and the node stays in place as a live-ref (its
    # upgraded_to_task_id points at the new thread).
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "推荐系统"}
    ).json()["data"]
    # A doc with a 拆解 section; each line becomes a doc node.
    client.put(
        f"/topics/{topic['id']}/doc",
        json={
            "content": "## 拆解\n\n数据清洗\n\n特征工程\n\n模型训练",
            "author": "user-1",
            "expected_version": 0,
        },
    )
    nodes = client.get(f"/topics/{topic['id']}/docs").json()["data"]["data"]
    target = next(n for n in nodes if n["content"] == "特征工程")
    assert target["upgraded_to_topic_id"] is None

    r = client.post(f"/blocks/{target['id']}/upgrade", json={"created_by": "user-1"})
    assert r.status_code == 200
    _wait_work_idle()
    sub = r.json()["data"]
    assert sub["room_id"] == topic["id"]

    # The doc node is now a live-ref to the subtopic, in place.
    nodes2 = client.get(f"/topics/{topic['id']}/docs").json()["data"]["data"]
    ref = next(n for n in nodes2 if n["id"] == target["id"])
    # The link points at the THREAD now. Two columns rather than one holding
    # either kind of id: both are real foreign keys, and a single untyped column
    # would be a pointer the database cannot check into a table it cannot name.
    assert ref["upgraded_to_task_id"] == sub["id"]
    assert ref["upgraded_to_topic_id"] is None
    assert ref["content"] == "特征工程"  # text unchanged; only the link is added


def test_archived_topic_is_frozen(client):
    # 归档后工作面冻结: no split, no doc edit on an archived topic.
    #
    # 归档现在只有一条入口 —— 人点的那一下 (#442 decision 1)。这个测试以前借
    # 「采纳即归档」拿到归档状态；采纳不再归档之后，它显式走归档端点，测的东西
    # 反而更贴题了。
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "交付物"}
    ).json()["data"]
    r = client.post(f"/topics/{topic['id']}/archive", json={"by": "alice"})
    assert r.status_code == 200
    got = client.get(f"/topics/{topic['id']}").json()["data"]
    assert got["status"] == "archived"

    # Splitting a frozen topic is rejected.
    r = client.post(f"/topics/{topic['id']}/split", json={"title": "续作"})
    assert r.status_code == 422
    # Editing the frozen topic's doc is rejected.
    r = client.put(
        f"/topics/{topic['id']}/doc",
        json={"content": "改一下", "author": "alice", "expected_version": 0},
    )
    assert r.status_code == 422


def test_upgrade_on_archived_topic_rejected(client):
    # Consistent with split/edit_doc: a frozen topic accepts no new work (§6.3).
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "交付"}
    ).json()["data"]
    block_id = _insert_block(client, p["id"], topic["id"], "某条结论")
    assert (
        client.post(f"/topics/{topic['id']}/archive", json={"by": "alice"}).status_code
        == 200
    )
    r = client.post(f"/blocks/{block_id}/upgrade", json={"created_by": "alice"})
    assert r.status_code == 422


def test_upgrade_from_private_chat_lands_under_root(client):
    # 私聊不是话题树父节点 (spec §1): upgrading a private-chat block makes a topic
    # under the project root, not an invisible orphan under the chat.
    p = _project(client, owner_handle="user-1")
    priv = client.get(
        f"/projects/{p['id']}/private-chat", params={"user_handle": "user-1"}
    ).json()["data"]
    block_id = _insert_block(
        client, p["id"], priv["id"], "我们其实该单独做个数据清洗模块"
    )
    topic = client.post(
        f"/blocks/{block_id}/upgrade", json={"created_by": "user-1"}
    ).json()["data"]
    _wait_work_idle()
    assert topic["parent_id"] == p["root_topic_id"]
    assert topic["kind"] == "topic"
    # Privacy: the private chat's doc is never copied into the public topic.
    doc = client.get(f"/topics/{topic['id']}/doc").json()["data"]
    assert doc is not None
    assert "我们其实该单独做个数据清洗模块" in doc["content"]  # source block
    assert "父话题当时还没有实况文档" in doc["content"]


def test_split_records_the_brief_on_the_card_and_starts_nobody(client):
    # 派活带简报: the brief is on the CARD, and the room's own timeline says the
    # work went out. The thread is born with NOBODY on it — the worker is the
    # caller's to spawn in its own session and to bind; a thread that has just
    # been dispatched is legitimately empty and silent, and reading that as a
    # failed dispatch is the mistake this asserts against.
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "推荐系统"}
    ).json()["data"]
    client.put(
        f"/topics/{topic['id']}/doc",
        json={
            "content": "## 目标\n\n给校园二手书平台做推荐",
            "author": "user-1",
            "expected_version": 0,
        },
    )

    sub = client.post(
        f"/topics/{topic['id']}/split",
        json={
            "title": "清洗数据",
            "created_by": "cheese",
            "brief": "把 10 万条借阅日志去重、去空值，产出干净数据集",
        },
    ).json()["data"]
    _wait_work_idle()

    # 简报进卡. Not a document of its own: the worker is a subagent holding the
    # ROOM's token and cannot reach a thread's doc address, so a document there
    # would freeze at dispatch and never be corrected.
    assert sub["brief"] == "把 10 万条借阅日志去重、去空值，产出干净数据集"
    assert sub["conclusion"] is None
    # 活没有文档地址可言 —— 它不是地点。
    assert client.get(f"/topics/{sub['id']}/doc").status_code == 404

    # 那张卡: the ROOM's main line says a piece of work left, and names which.
    room_blocks = client.get(f"/topics/{topic['id']}/blocks").json()["data"]["data"]
    cards = [b for b in room_blocks if (b.get("meta") or {}).get("action") == "split"]
    assert len(cards) == 1
    assert cards[0]["meta"]["task_id"] == sub["id"]
    assert "清洗数据" in cards[0]["content"]

    # 没人做，也没有套话开场白。The platform raises nothing on its own, and it
    # does not write an opening in 芝士's voice either — 语义内容必须由 AI 生成.
    assert sub["subagent_id"] is None
    assert _card_messages(client, topic["id"], sub["id"]) == []


def test_split_without_a_brief_leaves_the_brief_empty(client):
    # A human split from the UI carries no brief, and the card says so by being
    # empty rather than by carrying a paragraph explaining that it is empty.
    # The title and the room are the whole statement of the work in that case.
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "大话题"}
    ).json()["data"]
    sub = client.post(f"/topics/{topic['id']}/split", json={"title": "小任务"}).json()[
        "data"
    ]
    _wait_work_idle()
    assert sub["brief"] == ""
    assert client.get(f"/topics/{sub['id']}/doc").status_code == 404


def test_split_and_conclude(client):
    p = _project(client)
    topic = client.post(
        "/topics", json={"project_id": p["id"], "title": "大话题"}
    ).json()["data"]

    # Split a todo into a sub-topic.
    sub = client.post(
        f"/topics/{topic['id']}/split", json={"title": "实现数据清洗"}
    ).json()["data"]
    # A thread in the room, not a room of its own: it names the room it hangs
    # in, and it opens as work that is still going.
    assert sub["room_id"] == topic["id"]
    assert sub["status"] == "open"
    # Let the 分身's auto-kickoff finish before writing more to the shared
    # in-memory DB (otherwise the two interleave on one SQLite connection).
    _wait_work_idle()

    # It shows up in the room's task list — `children` is rooms under rooms,
    # which is exactly what a thread is not.
    tasks = client.get(f"/topics/{topic['id']}/tasks").json()["data"]["data"]
    assert any(t["id"] == sub["id"] for t in tasks)

    # 收卡 is said BY the room about the worker it raised: that worker is a 分身
    # in the room's own session and has no place of its own to file from.
    r = client.post(
        f"/topics/{topic['id']}/tasks/{sub['id']}/conclude",
        json={"conclusion": "数据清洗完成，去重后剩 8000 条"},
    )
    assert r.status_code == 200, r.text
    _wait_work_idle()
    closed = r.json()["data"]
    assert closed["status"] == "closed"
    assert closed["conclusion"] == "数据清洗完成，去重后剩 8000 条"

    # 结论住在卡上, so the room's own living doc is not rewritten behind its back
    # — the room keeps its doc, the way every other place does.
    doc = client.get(f"/topics/{topic['id']}/doc").json()["data"]
    assert doc is None or "数据清洗完成" not in doc["content"]


def test_concluding_something_that_is_not_a_thread_fails(client):
    """房间不是活,收不了自己。"""
    p = _project(client)
    root_id = _project_root(client, p["id"])
    r = client.post(
        f"/topics/{root_id}/tasks/{root_id}/conclude", json={"conclusion": "x"}
    )
    assert r.status_code == 404


def _project_root(client, project_id) -> str:
    topics = client.get(f"/topics?project_id={project_id}").json()["data"]["data"]
    return next(t["id"] for t in topics if t["kind"] == "root")
