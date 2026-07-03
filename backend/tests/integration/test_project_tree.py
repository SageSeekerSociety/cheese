"""Project expansion + topic tree (spec §4, §6; evals A1/A2/A4)."""

import asyncio
import time
import uuid

from app.api.deps import get_turn_runner
from app.domain.block.models import AuthorType, Block, BlockKind


def _project(client, **kw) -> dict:
    body = {"name": "P", **kw}
    return client.post("/api/projects", json=body).json()["data"]


def _wait_turns_idle() -> None:
    """Wait for background turns (the 分身 kickoff a /split submits) to finish,
    so in-test asserts and later writes don't race the kickoff's DB writes on
    the shared in-memory SQLite connection."""
    runner = get_turn_runner()
    for _ in range(250):
        if runner.active_turns() == 0:
            return
        time.sleep(0.02)


def test_project_create_autocreates_root_topic(client):
    p = _project(client, owner_handle="user-1", ai_mode="autonomous")
    assert p["ai_mode"] == "autonomous"
    assert p["owner_handle"] == "user-1"
    assert p["root_topic_id"] is not None

    # The root topic exists and is of kind "root".
    topics = client.get(f"/api/topics?project_id={p['id']}").json()["data"]["data"]
    roots = [t for t in topics if t["kind"] == "root"]
    assert len(roots) == 1
    assert roots[0]["id"] == p["root_topic_id"]


def test_link_task_and_duplicate(client):
    p = _project(client)
    space = client.post("/api/spaces", json={"name": "S"}).json()["data"]
    tmpl = client.post(
        f"/api/spaces/{space['id']}/templates", json={"name": "T"}
    ).json()["data"]
    task = client.post(
        f"/api/templates/{tmpl['id']}/tasks", json={"title": "题目"}
    ).json()["data"]

    r = client.post(f"/api/projects/{p['id']}/tasks", json={"task_id": task["id"]})
    assert r.status_code == 200
    # Duplicate link rejected.
    r2 = client.post(f"/api/projects/{p['id']}/tasks", json={"task_id": task["id"]})
    assert r2.status_code == 422

    links = client.get(f"/api/projects/{p['id']}/tasks").json()["data"]
    assert links["total"] == 1


def test_unlink_task(client):
    # 退出 Task 协议 (§4): a project can break its link to a task.
    p = _project(client)
    space = client.post("/api/spaces", json={"name": "S"}).json()["data"]
    tmpl = client.post(
        f"/api/spaces/{space['id']}/templates", json={"name": "T"}
    ).json()["data"]
    task = client.post(
        f"/api/templates/{tmpl['id']}/tasks", json={"title": "题目"}
    ).json()["data"]
    client.post(f"/api/projects/{p['id']}/tasks", json={"task_id": task["id"]})

    r = client.delete(f"/api/projects/{p['id']}/tasks/{task['id']}")
    assert r.status_code == 200
    assert client.get(f"/api/projects/{p['id']}/tasks").json()["data"]["total"] == 0
    # Unlinking again is a 404 (nothing to remove).
    r = client.delete(f"/api/projects/{p['id']}/tasks/{task['id']}")
    assert r.status_code == 404


def test_link_task_inherits_template_default_role(client):
    p = _project(client)  # no expert_role
    assert p.get("expert_role") in (None, "")
    space = client.post("/api/spaces", json={"name": "S"}).json()["data"]
    tmpl = client.post(
        f"/api/spaces/{space['id']}/templates",
        json={"name": "T", "default_role": "academic-research"},
    ).json()["data"]
    task = client.post(
        f"/api/templates/{tmpl['id']}/tasks", json={"title": "题目"}
    ).json()["data"]
    client.post(f"/api/projects/{p['id']}/tasks", json={"task_id": task["id"]})

    fresh = client.get(f"/api/projects/{p['id']}").json()["data"]
    assert fresh["expert_role"] == "academic-research"


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
        "/api/topics", json={"project_id": p["id"], "title": "讨论"}
    ).json()["data"]
    block_id = _insert_block(
        client, p["id"], topic["id"], "我们要不要单独做一个数据清洗的模块"
    )

    r = client.post(f"/api/blocks/{block_id}/upgrade", json={"created_by": "user-1"})
    assert r.status_code == 200
    new_topic = r.json()["data"]
    assert new_topic["parent_id"] == topic["id"]
    assert new_topic["kind"] == "subtopic"  # child of a non-root topic

    # The upgraded block IS the task: preset verbatim as the new topic's doc.
    doc = client.get(f"/api/topics/{new_topic['id']}/doc").json()["data"]
    assert doc is not None
    assert "我们要不要单独做一个数据清洗的模块" in doc["content"]

    # Auto-kickoff, same as split: the 分身's own opening is the first message
    # (the canned "我先确认理解" template is gone).
    _wait_turns_idle()
    blocks = client.get(f"/api/topics/{new_topic['id']}/blocks").json()["data"]["data"]
    msgs = [b for b in blocks if b["kind"] == "message"]
    assert msgs and msgs[0]["author_type"] == "ai"
    assert "我先确认理解，再开始推进" not in msgs[0]["content"]

    # Re-upgrading the same block is idempotent: it returns the topic already
    # created (so a double-click just navigates), not an error — and it does
    # NOT kick the 分身 off a second time.
    r2 = client.post(f"/api/blocks/{block_id}/upgrade", json={})
    assert r2.status_code == 200
    assert r2.json()["data"]["id"] == new_topic["id"]
    _wait_turns_idle()
    blocks2 = client.get(f"/api/topics/{new_topic['id']}/blocks").json()["data"]["data"]
    msgs2 = [b for b in blocks2 if b["kind"] == "message"]
    assert len(msgs2) == len(msgs)  # no second kickoff turn


def test_upgrade_doc_node_to_subtopic(client):
    # 自上而下拆解 (eval A2): a paragraph in the parent doc is upgraded into a
    # nested subtopic, and the node stays in place as a live-ref (its
    # upgraded_to_topic_id points at the new subtopic).
    p = _project(client)
    topic = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "推荐系统"}
    ).json()["data"]
    # A doc with a 拆解 section; each line becomes a doc node.
    client.put(
        f"/api/topics/{topic['id']}/doc",
        json={
            "content": "## 拆解\n\n数据清洗\n\n特征工程\n\n模型训练",
            "author": "user-1",
        },
    )
    nodes = client.get(f"/api/topics/{topic['id']}/docs").json()["data"]["data"]
    target = next(n for n in nodes if n["content"] == "特征工程")
    assert target["upgraded_to_topic_id"] is None

    r = client.post(
        f"/api/blocks/{target['id']}/upgrade", json={"created_by": "user-1"}
    )
    assert r.status_code == 200
    _wait_turns_idle()
    sub = r.json()["data"]
    assert sub["parent_id"] == topic["id"]
    assert sub["kind"] == "subtopic"

    # The doc node is now a live-ref to the subtopic, in place.
    nodes2 = client.get(f"/api/topics/{topic['id']}/docs").json()["data"]["data"]
    ref = next(n for n in nodes2 if n["id"] == target["id"])
    assert ref["upgraded_to_topic_id"] == sub["id"]
    assert ref["content"] == "特征工程"  # text unchanged; only the link is added


def test_archived_topic_is_frozen(client):
    # 归档后工作面冻结 (spec §6.3): no split, no doc edit on an archived topic.
    p = _project(client)
    topic = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "交付物"}
    ).json()["data"]
    card = client.post(
        f"/api/topics/{topic['id']}/accept-card",
        json={"reviewer_handle": "alice"},
    ).json()["data"]
    client.post(f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"})
    got = client.get(f"/api/topics/{topic['id']}").json()["data"]
    assert got["status"] == "archived"

    # Splitting a frozen topic is rejected.
    r = client.post(f"/api/topics/{topic['id']}/split", json={"title": "续作"})
    assert r.status_code == 422
    # Editing the frozen topic's doc is rejected.
    r = client.put(
        f"/api/topics/{topic['id']}/doc",
        json={"content": "改一下", "author": "alice"},
    )
    assert r.status_code == 422


def test_upgrade_on_archived_topic_rejected(client):
    # Consistent with split/edit_doc: a frozen topic accepts no new work (§6.3).
    p = _project(client)
    topic = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "交付"}
    ).json()["data"]
    block_id = _insert_block(client, p["id"], topic["id"], "某条结论")
    card = client.post(
        f"/api/topics/{topic['id']}/accept-card", json={"reviewer_handle": "alice"}
    ).json()["data"]
    client.post(f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"})
    r = client.post(f"/api/blocks/{block_id}/upgrade", json={"created_by": "alice"})
    assert r.status_code == 422


def test_upgrade_from_private_chat_lands_under_root(client):
    # 私聊不是话题树父节点 (spec §1): upgrading a private-chat block makes a topic
    # under the project root, not an invisible orphan under the chat.
    p = _project(client, owner_handle="user-1")
    priv = client.get(
        f"/api/projects/{p['id']}/private-chat", params={"user_handle": "user-1"}
    ).json()["data"]
    block_id = _insert_block(
        client, p["id"], priv["id"], "我们其实该单独做个数据清洗模块"
    )
    topic = client.post(
        f"/api/blocks/{block_id}/upgrade", json={"created_by": "user-1"}
    ).json()["data"]
    _wait_turns_idle()
    assert topic["parent_id"] == p["root_topic_id"]
    assert topic["kind"] == "topic"
    # Privacy: the private chat's doc is never copied into the public topic.
    doc = client.get(f"/api/topics/{topic['id']}/doc").json()["data"]
    assert doc is not None
    assert "我们其实该单独做个数据清洗模块" in doc["content"]  # source block
    assert "父话题当时还没有活文档" in doc["content"]


def test_split_seeds_brief_doc_and_kicks_off_the_分身(client):
    # 分身开工带简报: the child is born with a task-brief living doc (splitter's
    # brief + parent-doc snapshot), the canned opening is gone, and the 分身's
    # first turn starts by itself (no human message needed).
    p = _project(client)
    topic = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "推荐系统"}
    ).json()["data"]
    client.put(
        f"/api/topics/{topic['id']}/doc",
        json={"content": "## 目标\n\n给校园二手书平台做推荐", "author": "user-1"},
    )

    sub = client.post(
        f"/api/topics/{topic['id']}/split",
        json={
            "title": "清洗数据",
            "created_by": "cheese",
            "brief": "把 10 万条借阅日志去重、去空值，产出干净数据集",
        },
    ).json()["data"]

    # The brief IS the child's living doc, parent doc copied verbatim below it.
    doc = client.get(f"/api/topics/{sub['id']}/doc").json()["data"]
    assert doc is not None
    assert "把 10 万条借阅日志去重" in doc["content"]
    assert "给校园二手书平台做推荐" in doc["content"]
    assert "推荐系统" in doc["content"]  # source: parent title

    # Auto-kickoff (spec §8.4): the 分身's own opening shows up without anyone
    # posting — and it is the FIRST message (no canned template before it).
    _wait_turns_idle()
    blocks = client.get(f"/api/topics/{sub['id']}/blocks").json()["data"]["data"]
    msgs = [b for b in blocks if b["kind"] == "message"]
    assert msgs, "分身没有自动开工（没等到它的开场白）"
    assert msgs[0]["author_type"] == "ai"
    assert "我先确认理解，再开始推进" not in msgs[0]["content"]  # template gone


def test_split_without_brief_still_seeds_doc(client):
    # A human split from the UI carries no brief: the child still gets a doc
    # (source + parent snapshot + an explicit "no brief" notice).
    p = _project(client)
    topic = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "大话题"}
    ).json()["data"]
    sub = client.post(
        f"/api/topics/{topic['id']}/split", json={"title": "小任务"}
    ).json()["data"]
    _wait_turns_idle()
    doc = client.get(f"/api/topics/{sub['id']}/doc").json()["data"]
    assert doc is not None
    assert "拆分时没有附说明" in doc["content"]
    assert "大话题" in doc["content"]


def test_split_and_return_conclusion(client):
    p = _project(client)
    topic = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "大话题"}
    ).json()["data"]

    # Split a todo into a sub-topic.
    sub = client.post(
        f"/api/topics/{topic['id']}/split", json={"title": "实现数据清洗"}
    ).json()["data"]
    assert sub["parent_id"] == topic["id"]
    assert sub["kind"] == "subtopic"
    # Let the 分身's auto-kickoff finish before writing more to the shared
    # in-memory DB (otherwise the two interleave on one SQLite connection).
    _wait_turns_idle()

    # Sub-topic shows up under children.
    children = client.get(f"/api/topics/{topic['id']}/children").json()["data"]["data"]
    assert any(c["id"] == sub["id"] for c in children)

    # Conclusion flows back to the parent topic.
    r = client.post(
        f"/api/topics/{sub['id']}/return-conclusion",
        json={"conclusion": "数据清洗完成，去重后剩 8000 条"},
    )
    assert r.status_code == 200
    parent_blocks = client.get(f"/api/topics/{topic['id']}/blocks").json()["data"][
        "data"
    ]
    assert any("数据清洗完成" in b["content"] for b in parent_blocks)

    # C4: the conclusion is also woven into the parent's living doc …
    doc = client.get(f"/api/topics/{topic['id']}/doc").json()["data"]
    assert doc is not None and "数据清洗完成" in doc["content"]
    assert "子话题结论" in doc["content"]
    # … and the coordinator (本体) is notified.
    notifs = client.get(f"/api/projects/{p['id']}/notifications").json()["data"]["data"]
    assert any("实现数据清洗" in n["title"] for n in notifs)


def test_return_conclusion_on_root_topic_fails(client):
    p = _project(client)
    root_id = _project_root(client, p["id"])
    r = client.post(
        f"/api/topics/{root_id}/return-conclusion", json={"conclusion": "x"}
    )
    assert r.status_code == 422


def _project_root(client, project_id) -> str:
    topics = client.get(f"/api/topics?project_id={project_id}").json()["data"]["data"]
    return next(t["id"] for t in topics if t["kind"] == "root")
