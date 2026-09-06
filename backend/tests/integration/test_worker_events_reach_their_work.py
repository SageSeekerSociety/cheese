"""一个房间里跑好几个分身，谁干的事记到谁头上。

Several workers run inside one session, and everything they do arrives on the
same pipe as the session's own. The only thing telling them apart is the
``agent_id`` on each payload, and the only thing saying what that id is DOING is
the binding the room reported. These are the tests for the join between the two.

Two halves that are easy to conflate and must not be:

- a worker the room bound → its events land in that thread;
- a worker nobody bound → its events land on the room's own line, exactly where
  they landed before any of this existed. Not dropped. An unclaimed worker being
  attributed coarsely is a nuisance; its whole run vanishing is a bug nobody can
  see happening.

The one exception is SubagentStart/Stop, which are written ONLY for bound ids —
see `test_an_unknown_workers_stop_is_not_written_anywhere` for the measured
reason.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.claude_code.hook_events import HookRouter
from app.domain.agent.harness.claude_code.hooks_substrate import (
    Channel,
    ClaudeCodeRuntime,
)
from app.domain.agent.runtime import get_broker
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.room_task.services import TaskService
from app.domain.topic.repositories import TopicProgressRepository
from app.domain.topic.services import TopicService

pytestmark = pytest.mark.anyio


class _IdleChannel(Channel):
    """A live screen whose hooks can arrive without a platform request."""

    name = "idle-hooks"

    async def ensure_ready(self, **_: object) -> str:
        return "screen"

    async def send_prompt(self, screen: str, prompt: str) -> None:
        del screen, prompt


async def _seed_room(factory) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u1")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u1"
        )
        await session.commit()
    return project.id, topic.id


async def _dispatch(factory, project_id, room_id, title: str) -> uuid.UUID:
    async with factory() as session:
        task = await TopicService(session).dispatch_task(
            place_id=room_id, title=title, created_by="u1"
        )
        await session.commit()
        return task.id


async def _bind(factory, room_id, task_id, agent_id: str) -> None:
    async with factory() as session:
        await TaskService(session).bind_subagent(
            room_id=room_id, task_id=task_id, subagent_id=agent_id
        )
        await session.commit()


async def _live_runtime(factory, tmp_path, project_id, room_id):
    router = HookRouter()
    provider = ClaudeCodeRuntime(_IdleChannel(), router=router)
    ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([provider], provider.name),
    )
    await provider.ensure_subscription(project_id, room_id)
    provider._live[room_id] = "screen"
    return router, provider


async def _blocks(factory, room_id):
    """Everything written in this room, threads included.

    Not `list_for_topic`, which answers for ONE place — the room's own line
    unless told otherwise. Reading through it here would make "landed in the
    thread" and "was never written at all" look identical, which is the exact
    pair these tests exist to separate.
    """
    async with factory() as session:
        rows = await session.scalars(select(Block).where(Block.topic_id == room_id))
        return list(rows)


async def _on_main_line(factory, room_id):
    async with factory() as session:
        return await BlockRepository(session).list_for_topic(room_id)


async def _settle(factory, room_id, *eids: str) -> None:
    """Wait until every one of `eids` has landed — or give up after a second.

    A fixed sleep is a bet on how loaded the machine is: it holds when this file
    runs alone and starts losing when the whole suite runs in one process, which
    is the run that matters. Waiting for the WRITE instead is the same wait when
    things are fast and a correct one when they are not. An event the platform
    deliberately drops never arrives, so the timeout is a real outcome here, not
    only a failure — the assertions decide which.
    """
    for _ in range(100):
        rows = await _blocks(factory, room_id)
        landed = {(r.meta or {}).get("eid") for r in rows}
        if all(eid in landed for eid in eids):
            return
        await asyncio.sleep(0.01)


async def test_a_bound_workers_tool_call_lands_in_its_thread(client, tmp_path) -> None:
    """归流: the room ran the session, but this piece of work owns the event."""
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    task_id = await _dispatch(factory, project_id, room_id, "查一下分页")
    await _bind(factory, room_id, task_id, "worker-1")
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    assert router.push(
        str(room_id),
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rg TODO"},
            "agent_id": "worker-1",
            "agent_type": "general-purpose",
            "_eid": "bound-tool-1",
        },
    )
    await _settle(factory, room_id, "bound-tool-1")

    rows = await _blocks(factory, room_id)
    tool = next(r for r in rows if (r.meta or {}).get("eid") == "bound-tool-1")
    assert tool.task_id == task_id
    # 房间主线上没有它的副本：一件事只落一次。
    main = await _on_main_line(factory, room_id)
    assert [r for r in main if (r.meta or {}).get("eid") == "bound-tool-1"] == []

    await provider._close_topic(room_id)


async def test_two_workers_in_one_room_do_not_mix(client, tmp_path) -> None:
    """两条活并行时，时间线不是一条谁也认不出的混合流。"""
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    first = await _dispatch(factory, project_id, room_id, "活一")
    second = await _dispatch(factory, project_id, room_id, "活二")
    await _bind(factory, room_id, first, "worker-1")
    await _bind(factory, room_id, second, "worker-2")
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    for agent_id, eid in (("worker-1", "w1-tool"), ("worker-2", "w2-tool")):
        assert router.push(
            str(room_id),
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": f"echo {agent_id}"},
                "agent_id": agent_id,
                "_eid": eid,
            },
        )
    await _settle(factory, room_id, "w1-tool", "w2-tool")

    rows = await _blocks(factory, room_id)
    landed = {
        (r.meta or {}).get("eid"): r.task_id
        for r in rows
        if (r.meta or {}).get("eid") in {"w1-tool", "w2-tool"}
    }
    assert landed == {"w1-tool": first, "w2-tool": second}

    await provider._close_topic(room_id)


async def test_a_workers_closing_message_is_kept_in_full(client, tmp_path) -> None:
    """分身的收尾话只有这一份：它自己的对话记录跟着容器一起没。"""
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    task_id = await _dispatch(factory, project_id, room_id, "查一下分页")
    await _bind(factory, room_id, task_id, "worker-1")
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    assert router.push(
        str(room_id),
        {
            "hook_event_name": "SubagentStop",
            "agent_id": "worker-1",
            "agent_type": "general-purpose",
            "last_assistant_message": "分页那条查完了，结论是 cursor 更稳",
            "agent_transcript_path": "/home/u/.claude/projects/w/sub.jsonl",
            "_eid": "bound-stop-1",
        },
    )
    await _settle(factory, room_id, "bound-stop-1")

    rows = await _blocks(factory, room_id)
    stop = next(r for r in rows if (r.meta or {}).get("eid") == "bound-stop-1")
    assert stop.task_id == task_id
    assert stop.content == "分页那条查完了，结论是 cursor 更稳"
    assert (stop.meta or {})["agent_id"] == "worker-1"
    assert (stop.meta or {})["transcript_path"].endswith("sub.jsonl")

    await provider._close_topic(room_id)


async def test_a_stop_does_not_conclude_the_work(client, tmp_path) -> None:
    """完成通知 ≠ 活干完了。

    One worker reports finished more than once — putting a long command in its
    own background and standing by counts as finishing, and resuming produces
    another Stop later. So a Stop must not open a conclusion card or close the
    thread; the room decides that after reading what came back.
    """
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    task_id = await _dispatch(factory, project_id, room_id, "查一下分页")
    await _bind(factory, room_id, task_id, "worker-1")
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    for eid, text in (("stop-a", "先歇一下"), ("stop-b", "接着跑完了")):
        assert router.push(
            str(room_id),
            {
                "hook_event_name": "SubagentStop",
                "agent_id": "worker-1",
                "last_assistant_message": text,
                "_eid": eid,
            },
        )
    await _settle(factory, room_id, "w1-stop", "w2-stop")

    # 两次都记下来了——第二次不是重复，是它续跑之后又交了一次。
    rows = await _blocks(factory, room_id)
    said = [
        r.content for r in rows if (r.meta or {}).get("eid") in {"stop-a", "stop-b"}
    ]
    assert said == ["先歇一下", "接着跑完了"]

    async with factory() as session:
        from app.domain.conclusion.repositories import ConclusionCardRepository
        from app.domain.room_task.models import TaskStatus

        assert await ConclusionCardRepository(session).live_for_task(task_id) is None
        task = await TaskService(session).get(task_id)
        assert task is not None
        assert task.status is TaskStatus.open

    await provider._close_topic(room_id)


async def test_an_unbound_workers_tool_call_still_reaches_the_room(
    client, tmp_path
) -> None:
    """没绑的分身不会消失——它只是记在房间头上，跟以前一样。"""
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    assert router.push(
        str(room_id),
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rg TODO"},
            "agent_id": "nobody-claimed-me",
            "_eid": "unbound-tool-1",
        },
    )
    await _settle(factory, room_id, "unbound-tool-1")

    rows = await _blocks(factory, room_id)
    tool = next(r for r in rows if (r.meta or {}).get("eid") == "unbound-tool-1")
    assert tool.task_id is None

    await provider._close_topic(room_id)


async def test_an_unknown_workers_stop_is_not_written_anywhere(
    client, tmp_path
) -> None:
    """来路不明的 SubagentStop 一个字都不落。

    Measured twice on 2.1.224: after the session's own Stop, a SubagentStop
    arrives whose id matches no worker we ever saw, whose type is empty, and
    whose "closing message" is a fragment of a prompt — something inside Claude
    Code, not work anybody dispatched. Writing it would put a stranger's
    half-sentence in the room under 芝士's name.
    """
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    assert router.push(
        str(room_id),
        {
            "hook_event_name": "SubagentStop",
            "agent_id": "a-stranger",
            "agent_type": "",
            "last_assistant_message": "You are a helpful assistant. Your task is",
            "_eid": "stranger-stop-1",
        },
    )
    await _settle(factory, room_id, "stranger-stop-1")

    rows = await _blocks(factory, room_id)
    assert [r for r in rows if (r.meta or {}).get("eid") == "stranger-stop-1"] == []
    assert "You are a helpful assistant. Your task is" not in [r.content for r in rows]

    await provider._close_topic(room_id)


async def test_a_finished_threads_id_stops_catching_events(client, tmp_path) -> None:
    """收了的活不再吸走事件——它的 id 要是还认，后来的东西会被它吞掉。"""
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    task_id = await _dispatch(factory, project_id, room_id, "已经收了的活")
    await _bind(factory, room_id, task_id, "worker-1")
    async with factory() as session:
        from app.domain.room_task.models import TaskStatus

        task = await TaskService(session).get(task_id)
        assert task is not None
        task.status = TaskStatus.closed
        await session.commit()
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    assert router.push(
        str(room_id),
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rg TODO"},
            "agent_id": "worker-1",
            "_eid": "after-close-1",
        },
    )
    await _settle(factory, room_id, "after-close-1")

    rows = await _blocks(factory, room_id)
    tool = next(r for r in rows if (r.meta or {}).get("eid") == "after-close-1")
    assert tool.task_id is None

    await provider._close_topic(room_id)


async def test_the_rooms_own_events_are_untouched(client, tmp_path) -> None:
    """房间自己干的事没有 agent_id，一切照旧。"""
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    task_id = await _dispatch(factory, project_id, room_id, "并行的一条活")
    await _bind(factory, room_id, task_id, "worker-1")
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    broker = get_broker()
    async with broker.subscribe(str(room_id)) as room:
        assert router.push(
            str(room_id),
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
                "_eid": "room-tool-1",
            },
        )
        frames = [await asyncio.wait_for(room.get(), 2) for _ in range(2)]

    assert [f["type"] for f in frames] == ["turn_started", "event_block"]
    rows = await _blocks(factory, room_id)
    tool = next(r for r in rows if (r.meta or {}).get("eid") == "room-tool-1")
    assert tool.task_id is None

    await provider._close_topic(room_id)


async def test_a_workers_checklist_is_its_own(client, tmp_path) -> None:
    """分身的清单归它做的那条活，不进房间的清单。

    不是整洁问题：Claude Code 的任务编号**每个 agent 各数各的**，都从 1 开始。混进
    一份 list 里，一个分身的 `TaskUpdate("1")` 会去勾掉房间自己的第一条 —— 房间的
    计划被别人的进度改写，而且谁也看不出是怎么改的。
    """
    factory = client.test_factory
    project_id, room_id = await _seed_room(factory)
    task_id = await _dispatch(factory, project_id, room_id, "查一下分页")
    await _bind(factory, room_id, task_id, "worker-1")
    router, provider = await _live_runtime(factory, tmp_path, project_id, room_id)

    # 房间先给自己列一条，分身随后列它自己的第一条并勾掉它。
    for payload in (
        {"tool_input": {"subject": "房间的第一件事"}, "_eid": "room-todo-1"},
        {
            "tool_input": {"subject": "分身的第一件事"},
            "agent_id": "worker-1",
            "_eid": "w1-todo-1",
        },
    ):
        assert router.push(
            str(room_id),
            {"hook_event_name": "PreToolUse", "tool_name": "TaskCreate", **payload},
        )
    assert router.push(
        str(room_id),
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "1", "status": "completed"},
            "agent_id": "worker-1",
            "_eid": "w1-todo-2",
        },
    )
    # 清单不落 block，所以等的是那两行清单本身写到位 —— 而且等的是**最后一笔**：
    # 「有了一条」会在 TaskCreate 就成立，那时 TaskUpdate 还没到，读到的是一个写了
    # 一半的答案（和 test_topic_progress 里记下的是同一个坑）。
    room_list = thread_list = None
    for _ in range(200):
        async with factory() as session:
            repo = TopicProgressRepository(session)
            room_list = await repo.get(room_id)
            thread_list = await repo.get(room_id, task_id=task_id)
        done = thread_list is not None and [
            (i["subject"], i["status"]) for i in thread_list.items
        ] == [("分身的第一件事", "completed")]
        if room_list is not None and done:
            break
        await asyncio.sleep(0.01)

    assert room_list is not None, "房间自己那条都没记下来"
    assert [i["subject"] for i in room_list.items] == ["房间的第一件事"]
    assert room_list.items[0]["status"] == "pending", "分身勾掉了房间自己的第一条"
    assert thread_list is not None, "分身的清单没被记下来"
    assert [(i["subject"], i["status"]) for i in thread_list.items] == [
        ("分身的第一件事", "completed")
    ]

    await provider._close_topic(room_id)
