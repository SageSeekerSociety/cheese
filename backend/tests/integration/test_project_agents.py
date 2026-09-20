"""An agent's memory follows the agent, and a room is where agents sit.

The behaviour these tests pin down is the one the whole change exists for: what
芝士 learns in one room of a project is available in every other room of that
project, because the memory belongs to the AGENT rather than to the room it
happened to be learned in. A room does not point at an agent: it seats any
number of them on its roster, the way it seats people, and which one a memory
belongs to is decided by who was acting when it was written.
"""

from app.core.sandbox_auth import mint_scoped_token
from app.domain.identity.handles import agent_instance_handle
from tests.integration.conftest import chat_ws_url, session_auth_headers


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


def _token(client, project_id: str, topic_id: str, agent: dict | None) -> str:
    """The token the sandbox calls `cheese remember` with. It names who is
    acting when the caller picked a teammate; without one it is the room's
    own turn token, which writes as the room's default."""
    return mint_scoped_token(
        project_id=project_id,
        topic_id=topic_id,
        agent_handle=agent_instance_handle(agent["id"]) if agent else None,
    )


def _remember(
    client, project_id: str, topic_id: str, content: str, *, agent: dict | None = None
) -> None:
    r = client.post(
        f"/projects/{project_id}/memory",
        json={"content": content, "topic": topic_id},
        headers={"X-Cheese-Token": _token(client, project_id, topic_id, agent)},
    )
    assert r.status_code == 200, r.text


def _recall(
    client, project_id: str, topic_id: str, query: str, *, agent: dict | None = None
) -> list[dict]:
    r = client.post(
        f"/projects/{project_id}/memory/search",
        json={"query": query, "topic": topic_id},
        headers={"X-Cheese-Token": _token(client, project_id, topic_id, agent)},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["hits"]


def _seat(client, topic_id: str, agent: dict, by: str = "u") -> str:
    """Invite a saved teammate into a room: a row on the roster, like a person."""
    r = client.post(
        f"/topics/{topic_id}/members",
        json={"handle": agent["seat_handle"], "role": "member", "actor": by},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return agent["seat_handle"]


def _agent_rows(client, topic_id: str) -> dict[str, dict]:
    r = client.get(f"/topics/{topic_id}/members")
    assert r.status_code == 200, r.text
    return {m["member_handle"]: m for m in r.json()["data"]["data"] if m["agent"]}


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


def test_two_agents_in_one_room_do_not_share_a_memory(client):
    """Separate agents are separate teammates — one's notes are not the other's,
    even when both sit in the same room. What keys the pool is who was acting,
    not where."""
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer", display_name="评审")
    room = _topic(client, pid, "shared")
    _seat(client, room, reviewer)

    _remember(client, pid, room, "默认芝士记的事")
    _remember(client, pid, room, "评审记的事", agent=reviewer)

    assert [h["abstract"] for h in _recall(client, pid, room, "记的事")] == [
        "默认芝士记的事"
    ]
    assert [
        h["abstract"] for h in _recall(client, pid, room, "记的事", agent=reviewer)
    ] == ["评审记的事"]


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


def test_a_project_starts_with_one_editable_cheese(client):
    pid = _project(client)
    agents = _agents(client, pid)
    assert len(agents) == 1
    assert agents[0]["handle"] == "cheese"
    assert agents[0]["configured"] is True
    assert agents[0]["id"] is not None
    assert agents[0]["configuration"]["model"]
    assert (
        client.post(f"/projects/{pid}/agents", json={"handle": "cheese"}).status_code
        == 422
    )


def test_a_new_topic_follows_the_projects_default(client):
    pid = _project(client)
    designer = _add_agent(client, pid, handle="designer", type_name="product-design")

    before = _topic(client, pid, "before")
    r = client.put(
        f"/projects/{pid}/default-agent", json={"instance_id": designer["id"]}
    )
    assert r.status_code == 200, r.text
    after = _topic(client, pid, "after")

    # A room seats the project's default when it is born, and a room from
    # before the change resolves its seat to the default of today. Neither
    # room has an agent of its own to point at.
    for topic_id in (before, after):
        names = {row["name"] for row in _agent_rows(client, topic_id).values()}
        assert designer["display_name"] in names, names


def test_an_agent_joins_a_room_through_its_roster(client):
    """Inviting a teammate is adding it to the roster — the same call, the same
    row shape, as inviting a person. The default stays seated beside it."""
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer", display_name="评审")
    assert reviewer["seat_handle"] == agent_instance_handle(reviewer["id"])
    room = _topic(client, pid, "review")

    seat = _seat(client, room, reviewer)

    rows = _agent_rows(client, room)
    assert rows[seat]["name"] == "评审"
    assert len(rows) == 2, rows
    # Once is enough: the roster refuses a second row for the same teammate.
    r = client.post(
        f"/topics/{room}/members",
        json={"handle": seat, "role": "member", "actor": "u"},
        headers=session_auth_headers("u"),
    )
    assert r.status_code == 422, r.text


def test_work_split_out_of_a_room_learns_into_the_rooms_pool(client):
    """This is the loop the split exists for: whatever the 分身 learns doing the
    work lands in the SAME pool the room reads, so the room has it afterwards.

    没有第二个 agent 要解析 —— 做这条活的分身跑在房间那一个会话里，它就是房间的
    agent 在干活。所以「这条活归谁」不是一个问题，「它学到的东西进谁的池子」才是。
    """
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer")
    room = _topic(client, pid, "review room")
    _seat(client, room, reviewer)

    r = client.post(
        f"/topics/{room}/split",
        json=dict(
            reviewer_handle="alice", **{"title": "拆出来的活", "created_by": "u"}
        ),
    )
    assert r.status_code == 200, r.text
    # 一张卡不是地点：拆出来的活在房间那一个会话里做，记忆也从房间记。
    _remember(client, pid, room, "分身查出来的事", agent=reviewer)
    assert [
        h["abstract"] for h in _recall(client, pid, room, "查出来", agent=reviewer)
    ] == ["分身查出来的事"]


def test_a_room_cannot_seat_another_projects_agent(client):
    """A seat that can never act is a trap on the roster: nothing addresses it,
    nothing writes as it. The roster refuses it up front."""
    mine, theirs = _project(client, "mine"), _project(client, "theirs")
    stranger = _add_agent(client, theirs, handle="stranger")

    room = _topic(client, mine)
    r = client.post(
        f"/topics/{room}/members",
        json={"handle": stranger["seat_handle"], "role": "member", "actor": "u"},
        headers=session_auth_headers("u"),
    )
    assert r.status_code == 404, r.text
    assert stranger["seat_handle"] not in _agent_rows(client, room)


# --- renaming and retiring an agent ------------------------------------------


def _update_agent(client, project_id: str, agent_id: str, **body):
    return client.put(f"/projects/{project_id}/agents/{agent_id}", json=body)


def test_renaming_an_agent_keeps_the_memory_it_had(client):
    """A rename changes what it is CALLED. Its handle keys the memory pool, so
    moving that would be handing it somebody else's notes — or an empty pool."""
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer", display_name="评审")
    room = _topic(client, pid, "review")
    _seat(client, room, reviewer)
    _remember(client, pid, room, "评审记的事", agent=reviewer)

    r = _update_agent(client, pid, reviewer["id"], display_name="严格评审")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["display_name"] == "严格评审"
    assert r.json()["data"]["handle"] == "reviewer"

    assert [
        h["abstract"] for h in _recall(client, pid, room, "记的事", agent=reviewer)
    ] == ["评审记的事"]


def test_an_agents_role_can_be_edited_directly(client):
    pid = _project(client)
    agent = _add_agent(client, pid, handle="reviewer")
    config = {**agent["configuration"], "body": "Review security"}
    r = _update_agent(client, pid, agent["id"], configuration=config)
    assert r.status_code == 200
    assert r.json()["data"]["configuration"] == config


def test_renaming_without_naming_a_type_leaves_the_type_alone(client):
    """The settings page sends both fields, but a caller that sends only a name
    must not have the agent's type silently stripped off it."""
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer", type_name="product-design")

    r = _update_agent(client, pid, reviewer["id"], display_name="改个名")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["type_name"] == "product-design"


def test_editing_an_agent_cannot_rebind_it_to_a_shared_type(client):
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer")

    r = _update_agent(client, pid, reviewer["id"], type_name="no-such-type")
    assert r.status_code == 400
    assert "type_name" in r.text


def test_renaming_an_agent_that_is_not_there_is_a_404(client):
    import uuid as _uuid

    pid = _project(client)
    r = _update_agent(client, pid, str(_uuid.uuid4()), display_name="谁")
    assert r.status_code == 404


def test_one_project_cannot_rename_anothers_agent(client):
    """An instance id keys a memory pool, so it may not cross projects — the
    same rule the topic routes hold, on the write path."""
    mine, theirs = _project(client, "mine"), _project(client, "theirs")
    stranger = _add_agent(client, theirs, handle="stranger", display_name="别人的")

    r = _update_agent(client, mine, stranger["id"], display_name="偷来的")
    assert r.status_code == 404
    assert _agents(client, theirs)[-1]["display_name"] == "别人的"


def test_retiring_an_agent_keeps_it_and_its_memory(client):
    """停用 is not a delete: the row stays, so the pool it keys stays too."""
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer", display_name="评审")
    room = _topic(client, pid, "review")
    seat = _seat(client, room, reviewer)
    _remember(client, pid, room, "评审记的事", agent=reviewer)

    r = client.delete(f"/projects/{pid}/agents/{reviewer['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"deleted": True}

    listed = {a["handle"]: a for a in _agents(client, pid)}
    assert "reviewer" in listed, "管理页要能看到已停用的队友"
    assert listed["reviewer"]["is_active"] is False

    # And the room it already sits in carries on, memory and all.
    assert _agent_rows(client, room)[seat]["name"] == "评审"
    assert [
        h["abstract"] for h in _recall(client, pid, room, "记的事", agent=reviewer)
    ] == ["评审记的事"]


def test_a_retired_agent_is_not_offered_for_new_work(client):
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer")

    client.delete(f"/projects/{pid}/agents/{reviewer['id']}")

    offered = [a["handle"] for a in _agents(client, pid) if a["is_active"]]
    assert "reviewer" not in offered
    # Setting it as the default would put it back in front of every new room.
    r = client.put(
        f"/projects/{pid}/default-agent", json={"instance_id": reviewer["id"]}
    )
    assert r.status_code == 422


def test_retiring_the_default_falls_back_to_the_implicit_cheese(client):
    """Nothing may be left pointing at an agent nobody can choose — otherwise
    every new room in the project is handed the retired one."""
    pid = _project(client)
    designer = _add_agent(client, pid, handle="designer", type_name="product-design")
    client.put(f"/projects/{pid}/default-agent", json={"instance_id": designer["id"]})

    r = client.delete(f"/projects/{pid}/agents/{designer['id']}")
    assert r.status_code == 200, r.text

    listed = {a["handle"]: a for a in _agents(client, pid)}
    assert listed["designer"]["is_default"] is False
    assert listed["cheese"]["is_default"] is True

    room = _topic(client, pid, "after")
    names = {row["name"] for row in _agent_rows(client, room).values()}
    assert names == {listed["cheese"]["display_name"]}, names


def test_a_retired_agent_keeps_its_seat_in_the_rooms_it_is_in(client):
    """It is retired from NEW work, not evicted from the work it is doing."""
    pid = _project(client)
    reviewer = _add_agent(client, pid, handle="reviewer", display_name="评审")
    room = _topic(client, pid, "review")
    seat = _seat(client, room, reviewer)

    client.delete(f"/projects/{pid}/agents/{reviewer['id']}")

    assert _agent_rows(client, room)[seat]["name"] == "评审"


def test_retiring_an_agent_that_is_not_there_is_a_404(client):
    import uuid as _uuid

    pid = _project(client)
    r = client.delete(f"/projects/{pid}/agents/{_uuid.uuid4()}")
    assert r.status_code == 404


def test_one_project_cannot_retire_anothers_agent(client):
    mine, theirs = _project(client, "mine"), _project(client, "theirs")
    stranger = _add_agent(client, theirs, handle="stranger")

    r = client.delete(f"/projects/{mine}/agents/{stranger['id']}")
    assert r.status_code == 404
    assert _agents(client, theirs)[-1]["is_active"] is True


def test_the_last_agent_cannot_be_retired_without_a_replacement(client):
    pid = _project(client)
    cheese = _agents(client, pid)[0]
    assert client.delete(f"/projects/{pid}/agents/{cheese['id']}").status_code == 422
    replacement = _add_agent(client, pid, handle="replacement")
    assert client.delete(f"/projects/{pid}/agents/{cheese['id']}").status_code == 200
    assert next(a for a in _agents(client, pid) if a["id"] == replacement["id"])[
        "is_default"
    ]


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


def test_project_credentials_cannot_borrow_the_destination_agent_seat(client):
    """The root agent needs its own membership in the destination room."""
    from app.core.sandbox_auth import mint_project_agent_credential

    pid = _project(client)
    room = _topic(client, pid)

    r = client.post(
        f"/topics/{room}/comments",
        json={"content": "从项目级凭据发出的"},
        headers={
            "X-Cheese-Token": mint_project_agent_credential(project_id=pid, epoch=0)
        },
    )
    assert r.status_code == 403, r.text


def test_a_credential_without_an_agent_identity_is_rejected(client):
    """A project capability without a participant cannot authenticate one."""
    import asyncio
    import uuid as _uuid

    import pytest

    from app.api.auth import ActorResolver
    from app.core.errors import AuthenticationRequiredError

    pid = _project(client)
    token = mint_scoped_token(project_id=pid)

    async def _resolve():
        async with client.test_factory() as session:
            resolver = ActorResolver(session=session, bearer=None, cheese_token=token)
            return await resolver.resolve(
                fallback_handle=None, project_id=_uuid.UUID(pid)
            )

    with pytest.raises(AuthenticationRequiredError):
        asyncio.run(_resolve())


# --- switching agents costs the session --------------------------------------


def _turn(client, room: str, text: str) -> None:
    with client.websocket_connect(chat_ws_url(room, "u")) as ws:
        ws.send_json({"type": "message", "content": text, "summon": True})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                break


def test_each_agent_keeps_its_own_thread_in_one_room(client, stub_hooks):
    """Two teammates in one room are two conversations.

    A conversation belongs to ONE agent — resuming it as somebody else produces
    an agent confidently remembering things it never said. So the teammate that
    is addressed next starts fresh. But the other's thread is its own row, not
    the room's single column, so addressing it again finds it exactly where it
    was.
    """
    pid = _project(client)
    room = _topic(client, pid, "two threads")
    reviewer = _add_agent(client, pid, handle="reviewer")
    seat = _seat(client, room, reviewer)

    _turn(client, room, "你好")
    first_session = stub_hooks.last_resume_session_id
    _turn(client, room, "再说一句")
    # 芝士 is resuming its own thread by now.
    assert stub_hooks.last_resume_session_id is not None
    cheese_session = stub_hooks.last_resume_session_id
    assert first_session is None

    # The reviewer starts a fresh conversation rather than inheriting 芝士's.
    _turn(client, room, f"<@{seat}> 还在吗")
    assert stub_hooks.last_resume_session_id is None

    # ...and 芝士's thread is still there when it is next up.
    _turn(client, room, "我回来了")
    assert stub_hooks.last_resume_session_id == cheese_session
