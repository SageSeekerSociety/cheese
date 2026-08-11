"""Which 芝士 authors a topic's AI blocks.

The author is resolved from the room's roster — a member is an agent iff it
carries an execution binding — rather than from a fixed ``cheese`` string. That
is what lets one room host more than one agent and still attribute each message
to the one that wrote it.
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.identity.handles import topic_agent_handle
from tests.conftest import TEST_DATABASE_URL


def _own_agent(topic_id: str) -> str:
    """The 分身 a room is seeded with: its own agent-user, not a shared account."""
    return topic_agent_handle(uuid.UUID(topic_id))


def _seed_agent(handle: str) -> None:
    """Create a second agent: a real User row plus its platform binding."""

    async def _run() -> None:
        from app.domain.identity.services import IdentityService

        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await IdentityService(session).ensure_agent_user(handle=handle)
            await session.commit()
        await engine.dispose()

    asyncio.run(_run())


def _project_and_topic(client, created_by: str = "alice") -> tuple[str, str]:
    project_id = client.post(
        "/api/projects", json={"name": "P", "owner_handle": created_by}
    ).json()["data"]["id"]
    topic_id = client.post(
        "/api/topics",
        json={"project_id": project_id, "title": "T", "created_by": created_by},
    ).json()["data"]["id"]
    return project_id, topic_id


def _turn(client, topic_id: str) -> list[dict]:
    """Run one summoned turn against the stub agent, return its frames."""
    with client.websocket_connect(f"/api/topics/{topic_id}/chat") as ws:
        ws.send_json(
            {"type": "message", "content": "hi", "author": "alice", "summon": True}
        )
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                return frames


def _ai_authors(client, topic_id: str) -> set[str]:
    blocks = client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]
    return {b["author"] for b in blocks if b["author_type"] == "ai"}


def test_default_room_attributes_ai_blocks_to_its_own_agent(client):
    """The seeded roster carries THIS room's 分身, and its blocks say so —
    the point of 分身独立身份: two rooms' work is told apart by its author."""
    _, topic_id = _project_and_topic(client)
    _turn(client, topic_id)
    assert _ai_authors(client, topic_id) == {_own_agent(topic_id)}


def test_ai_blocks_follow_the_rooms_agent_not_a_fixed_handle(client):
    """Swap which agent is in the room and the AI blocks change hands.

    This is the whole point of resolving the author: with a hard-coded string
    the blocks below would still say ``cheese`` even though 芝士 is no longer a
    member of the room.
    """
    _, topic_id = _project_and_topic(client)
    _seed_agent("ops")
    r = client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": "ops", "role": "member", "actor": "alice"},
    )
    assert r.status_code == 200
    r = client.delete(
        f"/api/topics/{topic_id}/members/{_own_agent(topic_id)}?actor=alice"
    )
    assert r.status_code == 200

    _turn(client, topic_id)
    assert _ai_authors(client, topic_id) == {"ops"}


def test_the_summon_receipt_carries_the_same_agent(client):
    """The ✅ receipt is authored by the platform, so it must agree with the
    message author — otherwise the room shows a reaction from someone absent."""
    _, topic_id = _project_and_topic(client)
    _seed_agent("ops")
    client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": "ops", "role": "member", "actor": "alice"},
    )
    client.delete(f"/api/topics/{topic_id}/members/{_own_agent(topic_id)}?actor=alice")

    frames = _turn(client, topic_id)
    ack = next(f for f in frames if f["type"] == "reaction")
    assert ack["reactions"] == [{"emoji": "✅", "count": 1, "authors": ["ops"]}]


def _swap_agent(client, topic_id: str, handle: str) -> None:
    """Make ``handle`` the room's 芝士 in place of the seeded one."""
    _seed_agent(handle)
    client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": handle, "role": "member", "actor": "alice"},
    )
    client.delete(f"/api/topics/{topic_id}/members/{_own_agent(topic_id)}?actor=alice")


def _remember(client, project_id: str, topic_id: str, fact: str) -> None:
    r = client.post(
        f"/api/projects/{project_id}/memory",
        json={"content": fact, "topic": topic_id},
    )
    assert r.status_code == 200


def _recall(client, project_id: str, topic_id: str, query: str) -> list[dict]:
    return client.post(
        f"/api/projects/{project_id}/memory/search",
        json={"query": query, "topic": topic_id},
    ).json()["data"]["hits"]


def test_two_agents_in_one_project_keep_separate_memories(client):
    """One 芝士's memory is not the other's, the way two teammates' aren't.

    Both rooms live in the same project, which is what makes this the case the
    per-agent key exists for: with a project-wide pool, ops would read what
    cheese wrote.
    """
    project_id = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]

    def _topic(title: str) -> str:
        return client.post(
            "/api/topics",
            json={"project_id": project_id, "title": title, "created_by": "alice"},
        ).json()["data"]["id"]

    cheese_room, ops_room = _topic("A"), _topic("B")
    _swap_agent(client, ops_room, "ops")

    _remember(client, project_id, cheese_room, "部署脚本在 deploy/deploy.sh")
    _remember(client, project_id, ops_room, "告警阈值是 p99 500ms")

    assert any(
        "deploy.sh" in h["abstract"]
        for h in _recall(client, project_id, cheese_room, "部署")
    )
    assert not any(
        "deploy.sh" in h["abstract"]
        for h in _recall(client, project_id, ops_room, "部署")
    )
    assert not any(
        "p99" in h["abstract"] for h in _recall(client, project_id, cheese_room, "告警")
    )


def test_the_shared_pool_stays_readable_by_every_agent(client):
    """Memory written without a topic predates the split, so both rooms see it.

    Rooms that accumulated a project pool keep reading it; only new writes are
    per-agent.
    """
    project_id = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]

    def _topic(title: str) -> str:
        return client.post(
            "/api/topics",
            json={"project_id": project_id, "title": title, "created_by": "alice"},
        ).json()["data"]["id"]

    cheese_room, ops_room = _topic("A"), _topic("B")
    _swap_agent(client, ops_room, "ops")

    # No topic → the legacy shared pool.
    client.post(
        f"/api/projects/{project_id}/memory", json={"content": "本项目用 uv 管依赖"}
    )

    for room in (cheese_room, ops_room):
        assert any(
            "uv" in h["abstract"] for h in _recall(client, project_id, room, "依赖")
        )


def _post_without_summon(client, topic_id: str, content: str, author: str) -> None:
    """Post a human message that notifies but starts no turn."""
    with client.websocket_connect(f"/api/topics/{topic_id}/chat") as ws:
        ws.send_json(
            {"type": "message", "content": content, "author": author, "summon": False}
        )
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


def _notifs(client, project_id: str, handle: str) -> list[dict]:
    return client.get(
        f"/api/projects/{project_id}/notifications?target_handle={handle}"
    ).json()["data"]["data"]


def test_naming_an_agent_notifies_it_while_a_broadcast_does_not(client, bearer):
    """The two paths differ on purpose.

    A broadcast reaches the room's humans — every 芝士 in the room already reads
    the timeline. Naming one directly is how a teammate (or another agent) hands
    it work, so that one must get through.
    """
    project_id, topic_id = _project_and_topic(client)
    _seed_agent("ops")
    # <@handle> resolves against the PROJECT roster, so ops has to be a project
    # member before the room can name it.
    client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": "ops", "role": "member"},
        headers=bearer("alice"),  # the project owner — roster writes are guarded
    )
    client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": "ops", "role": "member", "actor": "alice"},
    )

    _post_without_summon(client, topic_id, "<@all> 大家看一下", author="alice")
    assert _notifs(client, project_id, "ops") == []

    _post_without_summon(client, topic_id, "<@ops> 帮忙看下告警", author="alice")
    assert len(_notifs(client, project_id, "ops")) == 1


def test_human_members_are_not_mistaken_for_agents(client):
    """A plain member carries no binding, so it never authors AI blocks."""
    _, topic_id = _project_and_topic(client)
    client.post(
        f"/api/topics/{topic_id}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    _turn(client, topic_id)
    assert _ai_authors(client, topic_id) == {_own_agent(topic_id)}


# --- 记忆可见: the agent's own pool has to be listable, not just searchable ---


def _list_memory(client, project_id: str, **params) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/memory?project_id={project_id}" + (f"&{query}" if query else "")
    return client.get(url).json()["data"]["data"]


def test_listing_a_project_shows_what_its_agents_remembered(client):
    """`cheese remember` always carries a topic, so every agent write lands in
    an agent pool. If listing skipped those, the memory panel showed an empty
    project while the live pool kept growing — unauditable by construction."""
    project_id = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    topic_id = client.post(
        "/api/topics",
        json={"project_id": project_id, "title": "T", "created_by": "alice"},
    ).json()["data"]["id"]

    _remember(client, project_id, topic_id, "部署脚本在 deploy/deploy.sh")

    entries = _list_memory(client, project_id)
    assert [e["content"] for e in entries] == ["部署脚本在 deploy/deploy.sh"]
    assert entries[0]["scope"] == "agent_project"
    assert entries[0]["scope_id"] == f"{project_id}:{_own_agent(topic_id)}"


def test_listing_covers_every_agent_pool_in_the_project(client):
    """Two 芝士 keep separate pools; the project view must still see both, and
    `agent_handle` narrows to one."""
    project_id = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]

    def _topic(title: str) -> str:
        return client.post(
            "/api/topics",
            json={"project_id": project_id, "title": title, "created_by": "alice"},
        ).json()["data"]["id"]

    cheese_room, ops_room = _topic("A"), _topic("B")
    _swap_agent(client, ops_room, "ops")
    _remember(client, project_id, cheese_room, "部署脚本在 deploy/deploy.sh")
    _remember(client, project_id, ops_room, "告警阈值是 p99 500ms")

    everything = _list_memory(client, project_id)
    assert {e["content"] for e in everything} == {
        "部署脚本在 deploy/deploy.sh",
        "告警阈值是 p99 500ms",
    }
    assert {e["scope_id"] for e in everything} == {
        f"{project_id}:{_own_agent(cheese_room)}",
        f"{project_id}:ops",
    }

    only_ops = _list_memory(client, project_id, agent_handle="ops")
    assert [e["content"] for e in only_ops] == ["告警阈值是 p99 500ms"]
    assert [e["scope_id"] for e in only_ops] == [f"{project_id}:ops"]


def test_one_projects_agent_pool_never_leaks_into_another(client):
    """The prefix scan is keyed on this project — a sibling project's identical
    agent handle must not come along."""
    ids = [
        client.post("/api/projects", json={"name": n}).json()["data"]["id"]
        for n in ("P1", "P2")
    ]
    for pid, fact in zip(ids, ("P1 的事", "P2 的事"), strict=True):
        topic_id = client.post(
            "/api/topics",
            json={"project_id": pid, "title": "T", "created_by": "alice"},
        ).json()["data"]["id"]
        _remember(client, pid, topic_id, fact)

    assert [e["content"] for e in _list_memory(client, ids[0])] == ["P1 的事"]
    assert [e["content"] for e in _list_memory(client, ids[1])] == ["P2 的事"]


def test_include_agent_false_is_the_way_back_to_the_shared_pool(client):
    """The escape hatch: callers that only want the pre-split project pool."""
    project_id = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    topic_id = client.post(
        "/api/topics",
        json={"project_id": project_id, "title": "T", "created_by": "alice"},
    ).json()["data"]["id"]

    _remember(client, project_id, topic_id, "芝士自己记的")
    # No topic → the legacy shared project pool.
    client.post(f"/api/projects/{project_id}/memory", json={"content": "项目共享的"})

    assert [
        e["content"] for e in _list_memory(client, project_id, include_agent="false")
    ] == ["项目共享的"]
    shared = _list_memory(client, project_id, include_agent="false")[0]
    assert shared["scope"] == "project"
    assert shared["scope_id"] == project_id
    assert len(_list_memory(client, project_id)) == 2
