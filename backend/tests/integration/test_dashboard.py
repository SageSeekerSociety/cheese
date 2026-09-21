"""Aggregation endpoints — 成员页 + Space board (evals F3/G2)."""

import asyncio
import uuid

from app.domain.block.models import AuthorType, Block, BlockKind
from tests.conftest import seed_space, seed_task_with_protocol
from tests.integration.conftest import session_auth_headers


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
    p = client.post("/projects", json={"name": "P", "owner_handle": "user-1"}).json()[
        "data"
    ]
    pid, root = p["id"], p["root_topic_id"]
    _seed_block(client, pid, root, "user-1", AuthorType.participant, BlockKind.message)
    _seed_block(client, pid, root, "user-1", AuthorType.system, BlockKind.event)

    c = client.get(f"/projects/{pid}/contributions").json()["data"]
    assert c["by_author"].get("user-1") == 1  # the system block is not counted
    assert c["by_author_type"]["system"] >= 1


def test_member_summary_has_active_and_weekly(client, bearer):
    p = client.post("/projects", json={"name": "P", "owner_handle": "user-1"}).json()[
        "data"
    ]
    pid, root = p["id"], p["root_topic_id"]
    client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "user-1"},
        headers=bearer("user-1"),  # the project owner
    )
    _seed_block(client, pid, root, "user-1", AuthorType.participant, BlockKind.message)

    # The member page resolves the viewer from the credential — read as user-1.
    s = client.get(
        f"/projects/{pid}/members/user-1/summary",
        headers=session_auth_headers("user-1"),
    ).json()["data"]
    assert "topics_active" in s and "weekly_contributions" in s
    assert s["weekly_contributions"] == 1
    # root topic is active and user-1 contributed → it shows under 在忙的话题.
    assert any(t["id"] == root for t in s["topics_active"])


def test_space_board_lists_linked_teams(client):
    # Space → its 赛题 → a project created from it (#370).
    space_id = seed_space(client, "明理书院")
    task_id = seed_task_with_protocol(client, space_id=space_id)
    client.post(
        "/projects", json={"name": "队伍A", "external_task_id": task_id}
    ).json()["data"]

    board = client.get(f"/spaces/{space_id}/dashboard").json()["data"]
    assert board["total"] == 1
    assert board["teams"][0]["name"] == "队伍A"


def test_space_board_empty_for_space_without_links(client):
    space_id = seed_space(client, "空书院")
    board = client.get(f"/spaces/{space_id}/dashboard").json()["data"]
    assert board["total"] == 0
