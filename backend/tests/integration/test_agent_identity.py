"""Which 芝士 authors a topic's AI blocks.

The author is resolved from the room's roster — a member is an agent iff it
carries an execution binding — rather than from a fixed ``cheese`` string. That
is what lets one room host more than one agent and still attribute each message
to the one that wrote it.
"""

import asyncio
import time
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.agent_instance.services import AgentInstanceService, memory_pool
from app.domain.memory.models import MemoryScope, user_scope_id
from app.domain.memory.store import DbMemoryStore, memory_store
from app.domain.project.services import ProjectService
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    post_message,
    post_project,
    session_auth_headers,
)


def _own_agent(client, topic_id: str) -> str:
    """The agent a room is seeded with — the project's default, on its own seat."""
    rows = client.get(f"/topics/{topic_id}/members").json()["data"]["data"]
    seats = [m["member_handle"] for m in rows if m["agent"]]
    assert len(seats) == 1, seats
    return seats[0]


def _seat_second_agent(client, project_id: str, topic_id: str, handle: str) -> str:
    """A second saved teammate in the project, invited into this room. Returns
    the seat it acts as."""
    created = client.post(f"/projects/{project_id}/agents", json={"handle": handle})
    assert created.status_code == 200, created.text
    seat = created.json()["data"]["seat_handle"]
    r = client.post(
        f"/topics/{topic_id}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return seat


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
    project_id = post_project(client, json={"name": "P"}, owner=created_by).json()[
        "data"
    ]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "T"},
        headers=session_auth_headers(created_by),
    ).json()["data"]["id"]
    return project_id, topic_id


def _turn(client, topic_id: str, content: str = "hi") -> str:
    """Run one addressed turn against the stub agent, in a 支线 of the room —
    where 芝士 answers — and return the 支线.

    点名由正文说了算（I13），所以每条都得点到人；已经点了名的原样发出去。在一句
    「<@ops> hi」前面再补一个 `@芝士`，点到的就是名册上排在前面的那一个，答话的于
    是不是被叫的那个队友 —— 这个文件恰好就是为分辨这件事写的。
    """
    addressed = content if "<@" in content else f"@芝士 {content}"
    thread = in_thread(client, topic_id, "alice")
    with client.websocket_connect(chat_ws_url(thread, "alice")) as ws:
        post_message(client, thread, "alice", {"content": addressed})
        while True:
            if ws.receive_json()["type"] in ("done", "error"):
                return thread


def _ai_authors(client, topic_id: str) -> set[str]:
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return {b["author"] for b in blocks if b["author"].startswith("cheese")}


def test_default_room_attributes_ai_blocks_to_the_seated_agent(client):
    """The seeded roster carries the project's default agent on its own seat,
    and its blocks say so — the point of 分身独立身份: work is told apart by
    its author, and the same agent is the same author in every room."""
    _, topic_id = _project_and_topic(client)
    thread = _turn(client, topic_id)
    assert _ai_authors(client, thread) == {_own_agent(client, topic_id)}


def test_ai_blocks_are_authored_by_the_agent_that_was_addressed(client):
    """Two agents in one room: the one a message names is the one whose seat
    the reply carries. With a hard-coded string the blocks below would say
    ``cheese`` whichever teammate did the work."""
    project_id, topic_id = _project_and_topic(client)
    ops = _seat_second_agent(client, project_id, topic_id, "ops")

    thread = _turn(client, topic_id, f"<@{ops}> hi")
    assert _ai_authors(client, thread) == {ops}


def test_site_steps_are_authored_by_the_agent_that_was_addressed(client, stub_hooks):
    """现场的每一步也署被叫到的那位，不是房间的默认队友。

    署成默认那位时，一间坐着两位队友的房间里，现场和「正在处理」会把干活的认成
    先入座的那个——报上来的样子就是「只有芝士有这个界面」。"""
    project_id, topic_id = _project_and_topic(client)
    own = _own_agent(client, topic_id)
    ops = _seat_second_agent(client, project_id, topic_id, "ops")

    def emit_turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Grep", pattern="TODO", path="src")
        stub_hooks.says(topic, reply)
        stub_hooks.stops(topic, reply)

    stub_hooks.emit_turn = emit_turn
    thread = _turn(client, topic_id, f"<@{ops}> hi")

    tr = client.get(f"/topics/{thread}/transcript").json()["data"]["data"]
    steps = {b["author"] for b in tr if (b.get("meta") or {}).get("tool")}
    assert steps == {ops}, f"现场的步骤署成了 {steps}（默认队友是 {own}）"


def test_the_summon_receipt_carries_the_same_agent(client):
    """The 👀 receipt is authored by the platform, so it must agree with the
    message author — otherwise the room shows a reaction from someone absent."""
    project_id, topic_id = _project_and_topic(client)
    ops = _seat_second_agent(client, project_id, topic_id, "ops")

    thread = _turn(client, topic_id, f"<@{ops}> hi")
    # Asked of the durable block, not of the turn's frames: the receipt that
    # places the mark is reported on the harness's own task, so it is not
    # ordered against them.
    expected = [{"emoji": "👀", "count": 1, "authors": [ops]}]
    for _ in range(200):
        blocks = client.get(f"/topics/{thread}/blocks").json()["data"]["data"]
        landed = [b["reactions"] for b in blocks if b["reactions"]]
        if landed:
            assert landed == [expected]
            break
        time.sleep(0.02)
    else:
        raise AssertionError("the 👀 receipt never landed")


def _remember(client, project_id: str, fact: str, *, seat: str | None = None) -> None:
    """把一条事实写进某个 agent 的池子，直接按键。

    写那一侧（`cheese_remember`）已经整个撤掉，条目池只剩读侧；`seat` 是哪位
    队友的座位 handle，不给就是项目默认那位。
    """

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = await ProjectService(s).get_or_404(uuid.UUID(project_id))
            service = AgentInstanceService(s)
            writer = await service.for_seat_handle(project, seat)
            if writer is None:
                writer = await service.for_project(project)
            await memory_store(s).remember(*memory_pool(project.id, writer), fact)
            await s.commit()

    asyncio.run(_seed())


def _pool(client, project_id: str, handle: str) -> list[str]:
    """这位 agent 的池里现在有什么，按 handle 点名读。

    关键词检索那条读路径（`/projects/{id}/memory/search`）连同条目池的读侧一
    起撤了，所以这里读记忆列表本身。归属这件事它答得一样清楚：池是按 agent 分
    的，写进哪一位的池子，哪一位点名读得到。
    """
    return [e["content"] for e in _list_memory(client, project_id, agent_handle=handle)]


def _seat_a_new_agent(client, project_id: str, topic_id: str, handle: str) -> str:
    """Put a SECOND agent in this project and seat it in this room.

    Which agent a memory belongs to is decided by whose pool it went into, not
    by the room: a room seats any number of teammates, and each has its own.
    Returns the seat handle the new one acts as.
    """
    created = client.post(f"/projects/{project_id}/agents", json={"handle": handle})
    assert created.status_code == 200, created.text
    seat = created.json()["data"]["seat_handle"]
    r = client.post(
        f"/topics/{topic_id}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return seat


def test_a_memory_without_a_seat_is_the_projects_own_cheese(client):
    """没有共享池（结论 7）：不带座位的那一条也归一位芝士，就是项目自己那位。

    所以它落在项目默认那位的池里，而另一位队友的池里没有——写进谁的池子，决定的
    就是谁点名读得到，不存在一个谁都能写、谁都能读的中间地带。座位在哪间房不作
    数：池是按 agent 分的，点名读的也是 agent。
    """
    project_id = post_project(client, json={"name": "P"}, owner="alice").json()["data"][
        "id"
    ]

    def _topic(title: str) -> str:
        return client.post(
            "/topics",
            json={"project_id": project_id, "title": title},
            headers=session_auth_headers("alice"),
        ).json()["data"]["id"]

    # 另一位队友坐进一间房，好让它是一位真的队友；项目默认那位由项目自己坐。
    _seat_a_new_agent(client, project_id, _topic("A"), "ops")

    _remember(client, project_id, "本项目用 uv 管依赖")

    assert "本项目用 uv 管依赖" in _pool(client, project_id, "cheese")
    assert "本项目用 uv 管依赖" not in _pool(client, project_id, "ops")


def _post_without_summon(client, topic_id: str, content: str, author: str) -> None:
    """Post a human message in the room's main line. Whoever it names is told
    as the message lands; an AI teammate it calls answers in its 支线."""
    post_message(client, topic_id, author, {"content": content})


def _notifs(client, project_id: str, handle: str) -> list[dict]:
    return client.get(
        f"/projects/{project_id}/alerts",
        headers=session_auth_headers(handle),
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
        f"/projects/{project_id}/members",
        json={"user_handle": "ops"},
        headers=bearer("alice"),  # the project owner — seating a teammate is guarded
    )
    client.post(
        f"/topics/{topic_id}/members",
        json={"handle": "ops", "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )

    _post_without_summon(client, topic_id, "<@all> 大家看一下", author="alice")
    assert _notifs(client, project_id, "ops") == []

    _post_without_summon(client, topic_id, "<@ops> 帮忙看下告警", author="alice")
    assert len(_notifs(client, project_id, "ops")) == 1


def test_human_members_are_not_mistaken_for_agents(client):
    """A plain member carries no binding, so it never authors AI blocks."""
    _, topic_id = _project_and_topic(client)
    client.post(
        f"/topics/{topic_id}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    thread = _turn(client, topic_id)
    assert _ai_authors(client, thread) == {_own_agent(client, topic_id)}


# --- 记忆可见: the agent's own pool has to be listable, not just searchable ---


def _remember_about(client, project_id: str, person: str, fact: str) -> None:
    """项目默认芝士对某个人的一条记忆。

    直接按键写库：写的那一侧（私聊里的 `cheese_remember`）已经撤掉，这一组问的
    是列出来的时候都带回了什么。"""

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = await ProjectService(s).get_or_404(uuid.UUID(project_id))
            agent = await AgentInstanceService(s).for_project(project)
            await DbMemoryStore(s).remember(
                MemoryScope.user,
                user_scope_id(project.id, agent.handle, person),
                fact,
            )
            await s.commit()

    asyncio.run(_seed())


def _list_memory(
    client, project_id: str, *, headers: dict | None = None, **params
) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/memory?project_id={project_id}" + (f"&{query}" if query else "")
    return client.get(url, headers=headers).json()["data"]["data"]


def test_listing_a_project_shows_what_its_agents_remembered(client):
    """每一条 agent 记忆都落在某个 agent 的池子里（写那一侧撤掉前如此，撤掉后池
    子里的行仍是这个形状）。列表要是漏了它们，界面上的记忆面板就会在池子还在长
    的时候显示一个空项目——凭空审计不到。"""
    project_id = post_project(client, json={"name": "P"}, owner="alice").json()["data"][
        "id"
    ]

    _remember(client, project_id, "部署脚本在 deploy/deploy.sh")

    entries = _list_memory(client, project_id)
    assert [e["content"] for e in entries] == ["部署脚本在 deploy/deploy.sh"]
    assert entries[0]["scope"] == "agent_project"
    # Keyed by the AGENT working in the room — the project's default 芝士 here —
    # not by the room, so what it learns is one pool across every room it works
    # in rather than one pool per room.
    assert entries[0]["scope_id"] == f"{project_id}:cheese"


def test_listing_covers_every_agent_pool_in_the_project(client):
    """Two 芝士 keep separate pools; the project view must still see both, and
    `agent_handle` narrows to one."""
    project_id = post_project(client, json={"name": "P"}, owner="alice").json()["data"][
        "id"
    ]

    def _topic(title: str) -> str:
        return client.post(
            "/topics",
            json={"project_id": project_id, "title": title},
            headers=session_auth_headers("alice"),
        ).json()["data"]["id"]

    ops_room = _topic("B")
    ops = _seat_a_new_agent(client, project_id, ops_room, "ops")
    _remember(client, project_id, "部署脚本在 deploy/deploy.sh")
    _remember(client, project_id, "告警阈值是 p99 500ms", seat=ops)

    everything = _list_memory(client, project_id)
    assert {e["content"] for e in everything} == {
        "部署脚本在 deploy/deploy.sh",
        "告警阈值是 p99 500ms",
    }
    assert {e["scope_id"] for e in everything} == {
        f"{project_id}:cheese",
        f"{project_id}:ops",
    }

    only_ops = _list_memory(client, project_id, agent_handle="ops")
    assert [e["content"] for e in only_ops] == ["告警阈值是 p99 500ms"]
    assert [e["scope_id"] for e in only_ops] == [f"{project_id}:ops"]


def test_one_projects_agent_pool_never_leaks_into_another(client):
    """The prefix scan is keyed on this project — a sibling project's identical
    agent handle must not come along."""
    ids = [
        post_project(client, json={"name": n}, owner="alice").json()["data"]["id"]
        for n in ("P1", "P2")
    ]
    for pid, fact in zip(ids, ("P1 的事", "P2 的事"), strict=True):
        _remember(client, pid, fact)

    assert [e["content"] for e in _list_memory(client, ids[0])] == ["P1 的事"]
    assert [e["content"] for e in _list_memory(client, ids[1])] == ["P2 的事"]


def test_listing_answers_what_was_remembered_about_me(client):
    """问「关于我记了什么」的人在请求里写了 `user_handle`，那是另一个问题。

    它和「这个项目的芝士都记了什么」一起答：两条各自成立，谁也不挡谁。"""
    project_id = post_project(client, json={"name": "P"}, owner="alice").json()["data"][
        "id"
    ]
    _remember(client, project_id, "芝士自己记的")
    _remember_about(client, project_id, "alice", "他要结论在最前面")

    about = _list_memory(
        client, project_id, headers=session_auth_headers("alice"), user_handle="alice"
    )
    assert {"芝士自己记的", "他要结论在最前面"} <= {e["content"] for e in about}
