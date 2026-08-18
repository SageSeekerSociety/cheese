"""An agent's memory follows the agent, and a room says which agent works in it.

The behaviour these tests pin down is the one the whole change exists for: what
芝士 learns in one room of a project is available in every other room of that
project, because the memory belongs to the AGENT rather than to the room it
happened to be learned in.
"""

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import chat_ws_url


def _project(client, name: str = "Agents") -> str:
    r = client.post("/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _topic(client, project_id: str, title: str = "room", by: str = "u") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": by},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _remember(client, project_id: str, topic_id: str, content: str) -> None:
    r = client.post(
        f"/projects/{project_id}/memory",
        json={"content": content, "topic": topic_id},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=project_id, topic_id=topic_id
            )
        },
    )
    assert r.status_code == 200, r.text


def _recall(client, project_id: str, topic_id: str, query: str) -> list[dict]:
    r = client.post(
        f"/projects/{project_id}/memory/search",
        json={"query": query, "topic": topic_id},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=project_id, topic_id=topic_id
            )
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["hits"]


def _agents(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/agents")
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _add_agent(client, project_id: str, **body) -> dict:
    r = client.post(f"/projects/{project_id}/agents", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# --- memory follows the agent ------------------------------------------------


def test_what_one_room_learns_the_next_room_knows(client):
    """The point of the whole change: one 芝士 per project, one memory."""
    pid = _project(client)
    kitchen, garden = _topic(client, pid, "kitchen"), _topic(client, pid, "garden")

    _remember(client, pid, kitchen, "部署脚本在 deploy.sh")

    hits = _recall(client, pid, garden, "deploy.sh")
    assert [h["abstract"] for h in hits] == ["部署脚本在 deploy.sh"]


def test_two_agents_in_one_project_do_not_share_a_memory(client):
    """Separate agents are separate teammates — one's notes are not the other's."""
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer", display_name="评审")
    default_room = _topic(client, pid, "default")
    review_room = _topic(client, pid, "review")

    r = client.put(f"/topics/{review_room}/agent", json={"instance_id": reviewer["id"]})
    assert r.status_code == 200, r.text

    _remember(client, pid, default_room, "默认芝士记的事")
    _remember(client, pid, review_room, "评审记的事")

    assert [h["abstract"] for h in _recall(client, pid, default_room, "记的事")] == [
        "默认芝士记的事"
    ]
    assert [h["abstract"] for h in _recall(client, pid, review_room, "记的事")] == [
        "评审记的事"
    ]


def test_a_rooms_own_pool_stays_readable(client):
    """Memory written while the pool was keyed by the ROOM must not disappear.

    Nothing new lands there, but a room that had already learned something has
    to keep reading it — otherwise the day this shipped looks, from inside that
    room, exactly like amnesia.
    """
    import asyncio

    from app.domain.identity.handles import topic_agent_handle
    from app.domain.memory.models import MemoryScope, agent_project_scope_id
    from app.domain.memory.store import memory_store

    pid = _project(client)
    room = _topic(client, pid, "old room")

    async def _seed() -> None:
        async with client.test_factory() as session:
            await memory_store(session).remember(
                MemoryScope.agent_project,
                agent_project_scope_id(pid, topic_agent_handle(room)),
                "这条是按房间分池时代记下的",
            )
            await session.commit()

    asyncio.run(_seed())

    hits = _recall(client, pid, room, "按房间分池")
    assert [h["abstract"] for h in hits] == ["这条是按房间分池时代记下的"]


# --- which agent works where -------------------------------------------------


def test_a_project_starts_with_an_implicit_cheese_that_owns_a_pool(client):
    """A project nobody configured is not agent-less — 芝士 is already there."""
    pid = _project(client)

    agents = _agents(client, pid)
    assert len(agents) == 1
    default = agents[0]
    assert (default["handle"], default["is_default"]) == ("cheese", True)
    # No row to edit yet, which a settings screen has to be able to tell.
    assert default["configured"] is False
    assert default["id"] is None


def test_a_new_topic_follows_the_projects_default(client):
    pid = _project(client)
    designer = _add_agent(client, pid, handle="designer", type_name="product-design")

    before = _topic(client, pid, "before")
    r = client.put(
        f"/projects/{pid}/default-agent", json={"instance_id": designer["id"]}
    )
    assert r.status_code == 200, r.text
    after = _topic(client, pid, "after")

    # Neither room pinned an agent, so both follow the project — including the
    # one created before the default changed. A copy taken at creation time
    # would have frozen the old answer into it.
    for topic_id in (before, after):
        agent = client.get(f"/topics/{topic_id}/agent").json()["data"]
        assert agent["handle"] == "designer"
        assert agent["inherited"] is True


def test_a_topic_can_be_created_with_its_own_agent(client):
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer")

    r = client.post(
        "/topics",
        json={
            "project_id": pid,
            "title": "review",
            "agent_instance_id": reviewer["id"],
        },
    )
    assert r.status_code == 200, r.text
    topic_id = r.json()["data"]["id"]

    agent = client.get(f"/topics/{topic_id}/agent").json()["data"]
    assert agent["handle"] == "reviewer"
    assert agent["inherited"] is False


def test_a_topic_cannot_borrow_another_projects_agent(client):
    """An instance id is the key to a memory pool, so it may not cross projects."""
    mine, theirs = _project(client, "mine"), _project(client, "theirs")
    stranger = _add_agent(client, theirs, handle="stranger")

    r = client.post(
        "/topics",
        json={"project_id": mine, "title": "t", "agent_instance_id": stranger["id"]},
    )
    assert r.status_code == 404

    room = _topic(client, mine)
    r = client.put(f"/topics/{room}/agent", json={"instance_id": stranger["id"]})
    assert r.status_code == 404


def test_a_duplicate_handle_in_one_project_is_rejected(client):
    pid = _project(client)
    _add_agent(client, pid, handle="reviewer")
    r = client.post(f"/projects/{pid}/agents", json={"handle": "reviewer"})
    assert r.status_code == 422


def test_the_unresolved_sentinel_cannot_be_claimed_as_an_agent(client):
    """It names "we cannot tell who this is" — it must never own a pool."""
    pid = _project(client)
    r = client.post(f"/projects/{pid}/agents", json={"handle": "cheese-unresolved"})
    assert r.status_code == 422


# --- "we cannot tell who this is" is its own identity ------------------------


def test_a_credential_naming_no_agent_is_attributed_to_the_room(client):
    """``cheese`` used to be the answer to "this call names no 分身", and it is
    now a real agent owning a real memory pool — so that answer would file every
    unattributable action under the default agent's name. The room answers
    instead: the same 分身 a per-turn token would have named."""
    from app.core.sandbox_auth import mint_project_agent_credential
    from app.domain.identity.handles import topic_agent_handle

    pid = _project(client)
    room = _topic(client, pid)

    # A project-wide credential reaches every room of its project and names no
    # 分身 of its own — the case `cheese` used to answer for.
    r = client.post(
        f"/topics/{room}/comments",
        json={"content": "从项目级凭据发出的"},
        headers={
            "X-Cheese-Token": mint_project_agent_credential(project_id=pid, epoch=0)
        },
    )
    assert r.status_code == 200, r.text
    comment = r.json()["data"]
    assert comment["author"] == topic_agent_handle(room)
    assert comment["author"] != "cheese"
    assert comment["author_type"] == "ai"


def test_a_credential_with_no_room_at_all_falls_to_the_sentinel(client):
    """Nothing identifies this caller beyond "some agent of this project". It
    must not borrow the default agent's name to write under."""
    import asyncio
    import uuid as _uuid

    from app.api.auth import ActorResolver
    from app.domain.identity.handles import CHEESE_HANDLE, UNRESOLVED_AGENT_HANDLE

    pid = _project(client)
    token = mint_scoped_token(project_id=pid)

    async def _resolve():
        async with client.test_factory() as session:
            resolver = ActorResolver(session=session, bearer=None, cheese_token=token)
            return await resolver.resolve(
                fallback_handle=None, project_id=_uuid.UUID(pid)
            )

    actor = asyncio.run(_resolve())
    assert actor.is_agent is True
    assert actor.handle == UNRESOLVED_AGENT_HANDLE
    assert actor.handle != CHEESE_HANDLE


# --- switching agents costs the session --------------------------------------


def test_switching_agent_drops_the_conversation(client, stub_agent):
    """A session is ONE agent's memory of the conversation. Resuming it as
    somebody else produces an agent confidently remembering things it never
    said — so the thread does not survive the switch, and the API says so."""
    pid = _project(client)
    room = _topic(client, pid, "handover")
    reviewer = _add_agent(client, pid, handle="reviewer")

    with client.websocket_connect(chat_ws_url(room, "u")) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break

    r = client.put(f"/topics/{room}/agent", json={"instance_id": reviewer["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["session_reset"] is True
    assert r.json()["data"]["handle"] == "reviewer"

    # The next turn starts a fresh conversation rather than resuming the one the
    # previous agent was having.
    with client.websocket_connect(chat_ws_url(room, "u")) as ws:
        ws.send_json({"type": "message", "content": "还在吗", "summon": True})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break
    assert stub_agent.last_resume_session_id is None


def test_switching_back_to_the_project_default_is_a_switch_too(client):
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer")
    room = _topic(client, pid)

    client.put(f"/topics/{room}/agent", json={"instance_id": reviewer["id"]})
    r = client.put(f"/topics/{room}/agent", json={"instance_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["handle"] == "cheese"
    assert r.json()["data"]["inherited"] is True


def test_setting_the_same_agent_again_keeps_the_conversation(client):
    """Only a real change costs the session — an idempotent PUT must not."""
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer")
    room = _topic(client, pid)

    client.put(f"/topics/{room}/agent", json={"instance_id": reviewer["id"]})
    r = client.put(f"/topics/{room}/agent", json={"instance_id": reviewer["id"]})
    assert r.json()["data"]["session_reset"] is False
