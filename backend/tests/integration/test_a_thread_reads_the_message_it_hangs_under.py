"""A 支线's reading reaches the message it hangs under.

That message stays in the channel's main line, and the platform still counts it
as the 支线's own first input: a session there is held to answer it. Reading the
支线 — which is what the agent's chat tools do, through this endpoint — has to
return it, or the one message the teammate is being asked about is nowhere to
be read.

- the newest page puts it in front of the 支线's own blocks, and the files sent
  with it come along;
- it reads by id from the 支线 as well;
- paging back reaches it once, and no page serves it twice;
- the channel's main line is unchanged: the message is its own, and read once;
- a neighbouring 支线's message is not reachable from this one.
"""

import asyncio
import uuid

from app.domain.block.models import AuthorType, Block, BlockKind
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _channel(client):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    response = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    return project["id"], response.json()["data"]["id"]


def _thread(client, block_id, who="bob"):
    response = client.post(
        f"/blocks/{block_id}/thread", headers=session_auth_headers(who)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _history(client, topic, who="alice", **params):
    response = client.get(
        f"/topics/{topic}/history", headers=session_auth_headers(who), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _attach_with(client, project, root, name, content):
    """A file sent with the main-line message, the way a send stores it: an
    attachment block in the same conversation under the sender's turn."""

    async def write():
        async with client.test_factory() as session:
            session.add(
                Block(
                    id=uuid.uuid4(),
                    project_id=uuid.UUID(project),
                    conversation_id=uuid.UUID(root["conversation_id"]),
                    author=root["author"],
                    author_type=AuthorType.participant,
                    kind=BlockKind.attachment,
                    content=content,
                    reply_to=uuid.UUID(root["id"]),
                    turn_id=uuid.UUID(root["id"]),
                    mime_type="application/pdf",
                    meta={"filename": name, "size": 500},
                )
            )
            await session.commit()

    asyncio.run(write())


def test_the_newest_page_opens_with_the_message_it_hangs_under(client):
    _, channel = _channel(client)
    root = post_message(client, channel, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"])
    said = post_message(client, thread, "bob", {"content": "我这边也是"})

    page = _history(client, thread)

    assert [b["id"] for b in page["data"]] == [root["id"], said["id"]]
    assert page["has_more"] is False


def test_the_files_sent_with_it_read_with_it(client):
    project, channel = _channel(client)
    root = post_message(client, channel, "alice", {"content": "微信里打不开预览"})
    _attach_with(client, project, root, "spec.pdf", "uploads/spec.pdf")
    thread = _thread(client, root["id"])

    page = _history(client, thread)

    assert [b["content"] for b in page["data"]] == [
        "微信里打不开预览",
        "uploads/spec.pdf",
    ]


def test_it_reads_by_id_from_the_thread(client):
    _, channel = _channel(client)
    root = post_message(client, channel, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"])

    response = client.get(
        f"/topics/{thread}/history/{root['id']}", headers=session_auth_headers("alice")
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"] == "微信里打不开预览"


def test_paging_back_reaches_it_once_with_no_gaps(client):
    _, channel = _channel(client)
    root = post_message(client, channel, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"])
    said = [
        post_message(client, thread, "bob", {"content": f"第 {i} 条"}) for i in range(3)
    ]

    newest = _history(client, thread, limit=2)
    older = _history(client, thread, limit=2, before=newest["oldest_id"])
    older_still = _history(client, thread, limit=2, before=older["oldest_id"])

    assert [b["id"] for b in newest["data"]] == [b["id"] for b in said[1:]]
    assert [b["id"] for b in older["data"]] == [root["id"], said[0]["id"]]
    assert older["has_more"] is False
    assert older_still["data"] == []


def test_a_search_in_the_thread_finds_it(client):
    _, channel = _channel(client)
    root = post_message(client, channel, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"])
    post_message(client, thread, "bob", {"content": "我这边也是"})

    found = _history(client, thread, q="打不开")

    assert [b["id"] for b in found["data"]] == [root["id"]]


def test_the_main_line_reads_it_once(client):
    _, channel = _channel(client)
    root = post_message(client, channel, "alice", {"content": "微信里打不开预览"})
    _thread(client, root["id"])

    page = _history(client, channel)

    assert [b["id"] for b in page["data"]] == [root["id"]]


def test_a_neighbouring_thread_cannot_reach_this_one(client):
    _, channel = _channel(client)
    root = post_message(client, channel, "alice", {"content": "微信里打不开预览"})
    mine = _thread(client, root["id"])
    other_root = post_message(client, channel, "alice", {"content": "另起一条"})
    other = _thread(client, other_root["id"])
    said = post_message(client, other, "bob", {"content": "在别处说的"})

    page = _history(client, mine)

    assert [b["id"] for b in page["data"]] == [root["id"]]
    read = client.get(
        f"/topics/{mine}/history/{said['id']}", headers=session_auth_headers("alice")
    )
    assert read.status_code == 404, read.text
