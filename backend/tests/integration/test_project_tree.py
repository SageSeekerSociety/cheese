"""Project expansion + topic tree (spec §4, §6; evals A1/A2/A4)."""

import asyncio
import uuid

from app.domain.block.models import AuthorType, Block, BlockKind


def _project(client, **kw) -> dict:
    body = {"name": "P", **kw}
    return client.post("/api/projects", json=body).json()["data"]


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

    # New topic opens with 芝士's opening白 (first block, ai author).
    blocks = client.get(f"/api/topics/{new_topic['id']}/blocks").json()["data"]["data"]
    assert len(blocks) >= 1
    assert blocks[0]["author_type"] == "ai"

    # Re-upgrading the same block is idempotent: it returns the topic already
    # created (so a double-click just navigates), not an error.
    r2 = client.post(f"/api/blocks/{block_id}/upgrade", json={})
    assert r2.status_code == 200
    assert r2.json()["data"]["id"] == new_topic["id"]


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
