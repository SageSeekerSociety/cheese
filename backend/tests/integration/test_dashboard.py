"""Aggregation endpoints — project overview + Space board (evals F3/G2)."""

import asyncio
import uuid

from app.domain.block.models import AuthorType, Block, BlockKind
from tests.conftest import seed_space


def _seed_block(client, project_id, topic_id, author, author_type, kind):
    async def _seed():
        async with client.test_factory() as s:
            s.add(
                Block(
                    project_id=uuid.UUID(project_id),
                    topic_id=uuid.UUID(topic_id),
                    kind=kind,
                    author_type=author_type,
                    author=author,
                    content="x",
                    refs=[],
                )
            )
            await s.commit()

    asyncio.run(_seed())


def test_contributions_exclude_system_blocks(client):
    # spec §10.1: by_author counts real contributors, not system lifecycle blocks.
    p = client.post(
        "/api/projects", json={"name": "P", "owner_handle": "user-1"}
    ).json()["data"]
    pid, root = p["id"], p["root_topic_id"]
    _seed_block(client, pid, root, "user-1", AuthorType.human, BlockKind.message)
    _seed_block(client, pid, root, "user-1", AuthorType.system, BlockKind.event)

    c = client.get(f"/api/projects/{pid}/contributions").json()["data"]
    assert c["by_author"].get("user-1") == 1  # the system block is not counted
    assert c["by_author_type"]["system"] >= 1


def test_member_summary_has_active_and_weekly(client, bearer):
    p = client.post(
        "/api/projects", json={"name": "P", "owner_handle": "user-1"}
    ).json()["data"]
    pid, root = p["id"], p["root_topic_id"]
    client.post(
        f"/api/projects/{pid}/members",
        json={"user_handle": "user-1"},
        headers=bearer("user-1"),  # the project owner
    )
    _seed_block(client, pid, root, "user-1", AuthorType.human, BlockKind.message)

    s = client.get(f"/api/projects/{pid}/members/user-1/summary").json()["data"]
    assert "topics_active" in s and "weekly_contributions" in s
    assert s["weekly_contributions"] == 1
    # root topic is active and user-1 contributed → it shows under 在忙的话题.
    assert any(t["id"] == root for t in s["topics_active"])


def test_project_overview(client, bearer):
    p = client.post(
        "/api/projects", json={"name": "P", "owner_handle": "user-1"}
    ).json()["data"]
    pid = p["id"]
    client.post(
        f"/api/projects/{pid}/members",
        json={"user_handle": "user-1"},
        headers=bearer("user-1"),  # the project owner
    )
    client.post(
        f"/api/projects/{pid}/milestones",
        json={"title": "中期", "due_date": "2030-01-01T00:00:00Z"},
    )
    # A decision request addressed to user-1 → should appear in 等你处理的事.
    client.post(
        f"/api/projects/{pid}/notifications",
        json={
            "level": "light",
            "kind": "decision_request",
            "title": "选哪个方案",
            "target_handle": "user-1",
        },
    )

    ov = client.get(f"/api/projects/{pid}/overview").json()["data"]
    assert ov["name"] == "P"
    assert ov["topic_count"] >= 1  # root topic auto-created
    assert ov["next_milestone"]["title"] == "中期"
    assert "user-1" in ov["waiting_on_you"]
    assert any(m["handle"] == "user-1" for m in ov["members"])


def test_space_board_lists_linked_teams(client):
    # Build Space → template → task, then link a project.
    space_id = seed_space(client, "明理书院")
    tmpl = client.post(
        f"/api/spaces/{space_id}/templates", json={"name": "入驻"}
    ).json()["data"]
    task = client.post(
        f"/api/templates/{tmpl['id']}/tasks", json={"title": "题目"}
    ).json()["data"]
    p = client.post("/api/projects", json={"name": "队伍A"}).json()["data"]
    client.post(f"/api/projects/{p['id']}/tasks", json={"task_id": task["id"]})

    board = client.get(f"/api/spaces/{space_id}/dashboard").json()["data"]
    assert board["total"] == 1
    assert board["teams"][0]["name"] == "队伍A"


def test_space_board_empty_for_space_without_links(client):
    space_id = seed_space(client, "空书院")
    board = client.get(f"/api/spaces/{space_id}/dashboard").json()["data"]
    assert board["total"] == 0


def test_overview_404_for_missing_project(client):
    r = client.get("/api/projects/00000000-0000-0000-0000-000000000000/overview")
    assert r.status_code == 404
