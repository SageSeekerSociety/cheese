"""周期任务那一轮读的是**规则主人**的 private 记忆。

一个人交代下来的活，就该按他的口味做：他人在不在房间里说话都一样 —— 那一轮没有
他署名的消息（正文是平台写完塞进投递的），所以主人是从那一笔投递上认出来的
（`ChatService._routine_owner`），而不是在历史里找出来的。

这里两头都看：调度器确实把主人写进了投递（写的那一半），以及组装那一轮时他确实被
算进了「本轮发言人」（读的那一半）。
"""

import uuid

from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.delivery.models import Delivery
from app.domain.memory.files import INDEX_NAME, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from tests.conftest import StubChannel, settle_turn
from tests.integration.test_routines import (
    OWNER,
    PERSON,
    _make_due,
    _project,
    _room,
    _sweep,
    _weekly,
)

PROJECT_HOOK = "- [项目那条](project.md) — 项目那一份的钩子"
OWNER_HOOK = "- [他要结论在最前面](conclusion-first.md) — 规则主人那一份的钩子"
BOB_HOOK = "- [别人的](elsewhere.md) — 没交代这件事的那个人那一份的钩子"
BOB = "user-2"


class Screen(StubChannel):
    """一轮只回一句话的骨架，把交出去的那份 system prompt 留下来。"""

    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="routine-owner")
        self.stops(screen, "Done.", session_id="routine-owner")
        return True


def _db(client, fn):
    async def go():
        async with client.test_request_factory() as session:
            out = await fn(session)
            await session.commit()
            return out

    return client.portal.call(go)


def _lay_indexes(client, project_id: str) -> None:
    """铺下三份索引：项目一份，主人一份，别人一份。"""

    async def go(session):
        store = MemoryFileStore(session)

        async def put(scope: MemoryFileScope, owner: str | None, content: str) -> None:
            await store.write(
                project_id=uuid.UUID(project_id),
                scope=scope,
                owner_handle=owner,
                path=INDEX_NAME,
                content=content,
                updated_by="cheese",
                expected_version=None,
            )

        await put(MemoryFileScope.project, None, PROJECT_HOOK)
        await put(MemoryFileScope.private, OWNER, OWNER_HOOK)
        await put(MemoryFileScope.private, BOB, BOB_HOOK)

    _db(client, go)


def _delivery_of(client) -> tuple[uuid.UUID, dict]:
    """那一轮的投递：调度器写的那一笔，payload 是它自己留的。"""

    async def read(session):
        rows = list(await session.scalars(select(Delivery)))
        match = [
            row for row in rows if (row.payload or {}).get("routineOwner") is not None
        ]
        assert len(match) == 1, match
        return match[0].id, dict(match[0].payload)

    return _db(client, read)


def _prompt_of_the_routine_turn(client, tmp_path, room: str, submitted: dict):
    """把那一轮真的跑一遍，交回它拿到的 system prompt。

    用的是 runner 收下的那几项原样（含投递号和这一次尝试的 id）—— 生产里
    `dispatch_pending` 交出去的就是这几项，`converse` 拿它去认领这一笔投递
    （`begin_send`），换一个 turn id 就认不回自己那一笔了。
    """
    screen = Screen()
    svc = ChatService(
        session_factory=client.test_request_factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    topic_id = uuid.UUID(room)

    async def run():
        async for _ in svc.converse(
            topic_id=topic_id,
            author=submitted["author"],
            content=submitted["content"],
            summon=submitted["addressed"],
            turn_id=submitted["turn_id"],
            delivery_id=submitted["delivery_id"],
            recipient_instance_id=submitted["recipient_instance_id"],
        ):
            pass
        await settle_turn(svc, topic_id)

    client.portal.call(run)
    assert screen.last_system_prompt is not None
    return screen.told


def test_a_routine_runs_with_the_owners_private_memory(client, tmp_path):
    project = _project(client)
    room = _room(client, project)
    rule = _weekly(client, room, headers=PERSON).json()["data"]
    assert rule["owner_handle"] == OWNER
    _lay_indexes(client, project)

    _make_due(client, rule["id"])
    _, runner = _sweep(client)
    assert len(runner.submitted) == 1, "没有派出一轮"
    _topic, submitted = runner.submitted[0]
    delivery_id, payload = _delivery_of(client)
    assert submitted["delivery_id"] == delivery_id
    assert payload["eventType"] == "routine_run"
    assert payload["routineOwner"] == OWNER
    assert submitted["content"] == payload["content"]

    prompt = _prompt_of_the_routine_turn(client, tmp_path, room, submitted)
    assert PROJECT_HOOK in prompt
    assert OWNER_HOOK in prompt, "规则主人那一份没有被读进来"
    assert BOB_HOOK not in prompt, "读到了没交代这件事的人的私有偏好"


def test_another_rooms_turn_does_not_carry_the_routine_owner(client, tmp_path):
    """主人是**这一笔投递**说的，不是这个项目里所有周期任务的主人的并集。

    同一间房里再跑一轮普通的一轮（没有投递号），主人的那一份就不该跟进来：它只
    在替主人做事的那一轮里算数。
    """
    project = _project(client)
    room = _room(client, project)
    _weekly(client, room, headers=PERSON)
    _lay_indexes(client, project)

    screen = Screen()
    svc = ChatService(
        session_factory=client.test_request_factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    topic_id = uuid.UUID(room)

    async def run():
        async for _ in svc.converse(
            topic_id=topic_id, author=BOB, content="问一句", summon=True
        ):
            pass
        await settle_turn(svc, topic_id)

    client.portal.call(run)
    assert screen.last_system_prompt is not None
    assert PROJECT_HOOK in screen.told
    assert BOB_HOOK in screen.told
    assert OWNER_HOOK not in screen.told
