"""Aggregation endpoints — 成员页 + Space board (evals F3/G2)."""

import asyncio
import uuid

from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from tests.conftest import seed_space, seed_task_with_protocol, start_task_project
from tests.integration.conftest import post_project, session_auth_headers


def _seed_block(client, project_id, topic_id, author, author_type, kind):
    async def _seed():
        async with client.test_factory() as s:
            s.add(
                Block(
                    project_id=uuid.UUID(project_id),
                    conversation_id=uuid.UUID(topic_id),
                    kind=kind,
                    author_type=author_type,
                    author=author,
                    content="x",
                    refs=[],
                )
            )
            await s.commit()

    asyncio.run(_seed())


def test_contributions_exclude_platform_blocks(client):
    # spec §10.1: by_author counts real contributors, not platform lifecycle blocks.
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    pid, root = p["id"], p["root_topic_id"]
    _seed_block(client, pid, root, "user-1", AuthorType.participant, BlockKind.message)
    _seed_block(client, pid, root, "user-1", AuthorType.platform, BlockKind.event)

    c = client.get(f"/projects/{pid}/contributions").json()["data"]
    assert c["by_author"].get("user-1") == 1  # the platform block is not counted
    # `by_author_type` is this endpoint's own three-bucket vocabulary
    # (human / ai / system), read off the SIGNATURE — not the column's value. The
    # platform bucket is still spelled "system" there; renaming the wire is its
    # own change.
    assert c["by_author_type"]["system"] >= 1


def test_member_summary_has_active_and_weekly(client, bearer):
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    pid, root = p["id"], p["root_topic_id"]
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
    project_id = start_task_project(client, task_id, handle="owner")

    board = client.get(f"/spaces/{space_id}/dashboard").json()["data"]
    assert board["total"] == 1
    assert board["teams"][0]["project_id"] == project_id


def test_space_board_empty_for_space_without_links(client):
    space_id = seed_space(client, "空书院")
    board = client.get(f"/spaces/{space_id}/dashboard").json()["data"]
    assert board["total"] == 0


def test_an_ai_teammates_summary_says_it_is_one_and_how_to_message_it(client):
    """The member page of an AI teammate names it a teammate, not a person who
    came in with the team, and carries the handle its private chat opens by."""
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    pid = p["id"]
    headers = session_auth_headers("user-1")
    # Opening a private chat with the default teammate is what seats it.
    assert (
        client.get(
            f"/projects/{pid}/private-chat?user_handle=user-1", headers=headers
        ).status_code
        == 200
    )
    roster = client.get(f"/projects/{pid}/members", headers=headers).json()["data"][
        "data"
    ]
    agent = next(m for m in roster if m.get("agent"))

    s = client.get(
        f"/projects/{pid}/members/{agent['user_handle']}/summary", headers=headers
    ).json()["data"]

    assert s["source"] == "agent"
    chat = client.get(
        f"/projects/{pid}/private-chat?user_handle=user-1"
        f"&agent_handle={s['instance_handle']}",
        headers=headers,
    )
    assert chat.status_code == 200


def test_an_ai_teammates_year_counts_the_same_work_as_its_week(client):
    """An AI teammate's page counts its own project: what it wrote this week
    is in its year too, and the project is listed as the one it is the agent
    of, not as one it was invited into."""
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    pid, root = p["id"], p["root_topic_id"]
    headers = session_auth_headers("user-1")
    assert (
        client.get(
            f"/projects/{pid}/private-chat?user_handle=user-1", headers=headers
        ).status_code
        == 200
    )
    roster = client.get(f"/projects/{pid}/members", headers=headers).json()["data"][
        "data"
    ]
    seat = next(m for m in roster if m.get("agent"))["user_handle"]
    _seed_block(client, pid, root, seat, AuthorType.participant, BlockKind.message)

    week = client.get(
        f"/projects/{pid}/members/{seat}/summary", headers=headers
    ).json()["data"]["weekly_contributions"]
    profile = client.get(f"/users/{seat}/profile", headers=headers).json()["data"]

    assert week == 1
    assert profile["activity"]["total"] == week
    assert [(x["project_id"], x["source"]) for x in profile["projects"]] == [
        (pid, "agent")
    ]
