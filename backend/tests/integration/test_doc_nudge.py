"""一间干过活的房间文档还空着，平台提醒一次（app/domain/topic/doc_nudge.py）。

钉住的：干过活 + 文档空 + 没提醒过 → 点名这间房里最近动过手的 agent 席位、只提醒
一次；文档有内容、只寒暄了两句、私聊，都不提醒。`submit` 换成记录器，并像真的
那条路一样在房间里落下那条平台事件——「提醒过没有」读的正是它。

「是不是工作房间」不自答，用的是 `agent/chat.py` 递给它的那个函数——提示词给不给
「建第一版实况文档」那一段问的是同一个答案，两边必须是同一份声明（结论 19）。

最后一条不走 `check`，走轮末真的那条：`nudge` 起个后台任务、睡一觉再问。
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
import redis

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.topic import doc_nudge
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import seed_user
from tests.integration.conftest import post_project


@pytest.fixture(autouse=True)
def _clear_locks() -> None:
    r = redis.Redis.from_url(settings.redis_url)
    for key in r.scan_iter("doc-nudge:*"):
        r.delete(key)


@pytest.fixture
def alice(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {seed_user(client, 'alice')}"}


def _room(client, alice) -> uuid.UUID:
    r = post_project(client, json={"name": "P"}, headers=alice, owner="alice")
    assert r.status_code == 200, r.text
    project_id = r.json()["data"]["id"]
    r = client.post(
        "/topics", json={"project_id": project_id, "title": "做一件事"}, headers=alice
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["data"]["id"])


def _run(client, fn):
    async def go():
        async with client.test_factory() as s:
            out = await fn(s)
            await s.commit()
            return out

    return asyncio.run(go())


def _agent(client, room_id: uuid.UUID) -> str:
    async def read(s):
        handles = await TopicMemberService(s).agent_handles(room_id)
        assert handles, "a new room seats its agent"
        return handles[0]

    return _run(client, read)


def _add(client, room_id: uuid.UUID, **fields) -> None:
    async def add(s):
        room = await s.get(Topic, room_id)
        s.add(
            Block(
                project_id=room.project_id,
                conversation_id=room.id,
                author_type=fields.pop("author_type", AuthorType.participant),
                **fields,
            )
        )

    _run(client, add)


def _work(client, room_id: uuid.UUID, agent: str, tools: int) -> None:
    _add(client, room_id, kind=BlockKind.message, author="alice", content="帮我查")
    for i in range(tools):
        _add(
            client,
            room_id,
            kind=BlockKind.event,
            author=agent,
            content=f"执行命令 {i}",
            meta={"tool": "Bash", "in_room": False},
        )
    _add(client, room_id, kind=BlockKind.message, author=agent, content="查完了")


def _check(client, room_id: uuid.UUID, sent: list) -> bool:
    def submit(room, seat, prompt, line, meta):
        sent.append(
            {"room": room, "seat": seat, "prompt": prompt, "line": line, "meta": meta}
        )

    before = len(sent)
    nudged = asyncio.run(
        doc_nudge.check(
            room_id,
            submit=submit,
            is_a_work_room=ChatService.room_is_a_work_room,
            session_factory=client.test_factory,
        )
    )
    for s in sent[before:]:
        _add(
            client,
            s["room"],
            kind=BlockKind.event,
            author="system",
            author_type=AuthorType.platform,
            content=s["line"],
            meta=s["meta"],
        )
    return nudged


def test_a_worked_room_without_a_doc_is_reminded_exactly_once(client, alice):
    room = _room(client, alice)
    agent = _agent(client, room)
    _work(client, room, agent, tools=doc_nudge.MIN_TOOL_EVENTS)
    sent: list = []

    assert _check(client, room, sent) is True
    assert len(sent) == 1
    assert sent[0]["seat"] == agent
    assert "cheese_doc_set" in sent[0]["prompt"]
    assert sent[0]["meta"]["event_type"] == "doc_missing"

    # 队友没写，再结束一轮：不再催。
    _work(client, room, agent, tools=3)
    assert _check(client, room, sent) is False
    assert len(sent) == 1


def test_a_room_with_a_doc_is_left_alone(client, alice):
    room = _room(client, alice)
    agent = _agent(client, room)
    _work(client, room, agent, tools=doc_nudge.MIN_TOOL_EVENTS + 3)

    async def write_doc(s):
        from tests.support.living_doc import write_doc as record

        await record(s, room, "## 目标\n\n做一件事。\n", agent)

    _run(client, write_doc)
    sent: list = []
    assert _check(client, room, sent) is False
    assert sent == []


def test_small_talk_is_not_work(client, alice):
    room = _room(client, alice)
    agent = _agent(client, room)
    _work(client, room, agent, tools=doc_nudge.MIN_TOOL_EVENTS - 1)
    sent: list = []

    assert _check(client, room, sent) is False
    assert sent == []


def _make_private(client, room: uuid.UUID) -> None:
    async def go(s):
        (await s.get(Topic, room)).is_private = True

    _run(client, go)


def test_a_private_chat_has_no_doc_to_keep(client, alice):
    room = _room(client, alice)
    agent = _agent(client, room)
    _work(client, room, agent, tools=doc_nudge.MIN_TOOL_EVENTS + 3)
    _make_private(client, room)
    sent: list = []
    assert _check(client, room, sent) is False
    assert sent == []


def test_the_turn_end_path_carries_that_answer(client, alice, monkeypatch):
    """轮末真的那条路（睡一觉、再 check）也把「是不是工作房间」带到，且带的就是
    提示词用的那个答案：`is_private` 的房间不提醒。

    轮末到这里中间隔着两层函数，中间把它丢了不会有别的东西报错——`_run_quietly`
    把异常咽掉，表现是提醒永远不来。
    """
    work = _room(client, alice)
    _work(client, work, _agent(client, work), tools=doc_nudge.MIN_TOOL_EVENTS)
    private = _room(client, alice)
    _work(client, private, _agent(client, private), tools=doc_nudge.MIN_TOOL_EVENTS)
    _make_private(client, private)

    sent: list = []

    def submit(room, seat, prompt, line, meta):
        sent.append(room)

    monkeypatch.setattr(doc_nudge, "runner_submit", lambda chat: submit)
    # 真的 `chat_service` 上取的就是这两个：`session_factory` 是属性，
    # `room_is_a_work_room` 是 `ChatService` 上那个静态方法。
    chat = SimpleNamespace(
        session_factory=client.test_factory,
        room_is_a_work_room=ChatService.room_is_a_work_room,
    )
    # 手里那个对象没有这个方法 —— 轮末收尾是紧接着摘存活标记的一句话，这里抛不出去。
    bare = SimpleNamespace(session_factory=client.test_factory)

    async def go():
        await asyncio.gather(
            doc_nudge._run_quietly(work, chat, 0),
            doc_nudge._run_quietly(private, chat, 0),
            doc_nudge._run_quietly(work, bare, 0),
        )

    asyncio.run(go())
    assert sent == [work]
