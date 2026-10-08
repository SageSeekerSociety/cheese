"""Which 芝士 authors a topic's AI blocks.

The author is resolved from the room's roster — a member is an agent iff it
carries an execution binding — rather than from a fixed ``cheese`` string. That
is what lets one room host more than one agent and still attribute each message
to the one that wrote it.
"""

import asyncio
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.agent_instance.services import AgentInstanceService, memory_pool
from app.domain.memory.models import (
    MemoryEntry,
    MemoryScope,
    agent_project_scope_id,
)
from app.domain.memory.store import memory_store
from app.domain.project.services import ProjectService
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
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
    with room_socket(client, thread, "alice") as ws:
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
    """这位 agent 的池里现在有什么，按 handle 点名读那张表。

    条目池已经没有读点了：`GET /memory` 现在读的是那棵记忆树（「项目文档 → 记忆」
    那一档），关键词检索那条读路径（`/projects/{id}/memory/search`）也连同条目池的
    读侧一起撤了。所以「写进哪一位的池子」只能问表本身——池是按 agent 分的，这几
    条用例守的正是这件事。
    """

    async def _read() -> list[str]:
        async with client.test_factory() as s:
            rows = await s.scalars(
                select(MemoryEntry.content)
                .where(
                    MemoryEntry.scope == MemoryScope.agent_project,
                    MemoryEntry.scope_id
                    == agent_project_scope_id(uuid.UUID(project_id), handle),
                    MemoryEntry.retired_at.is_(None),
                )
                .order_by(MemoryEntry.created_at.desc())
            )
            return list(rows.all())

    return asyncio.run(_read())


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
