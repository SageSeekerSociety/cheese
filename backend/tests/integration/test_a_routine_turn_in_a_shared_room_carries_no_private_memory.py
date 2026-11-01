"""多人房间里周期任务的那一轮，不带任何人的 private 记忆，规则主人的也不带。

座位上的会话一轮一轮接着用，下一轮可能就替房间里别的人干活；它为主人读过的东西，
下一轮一条命令就拿得出来，而命令的输出标不上主人（`turn_speakers` 的开头那一段）。
所以那一轮和这间房里任何一轮一样，只带项目共看的那一份。
"""

import uuid

from sqlalchemy import select

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.delivery.models import Delivery
from app.domain.memory.files import INDEX_NAME, MemoryFileScope, prompt_path
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.writers import MemoryWriter
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

TEAM_HOOK = "- [团队那条](team.md) — 项目那一份的钩子"
OWNER_HOOK = "- [他要结论在最前面](conclusion-first.md) — 规则主人那一份的钩子"
BOB_HOOK = "- [别人的](elsewhere.md) — 没交代这件事的那个人那一份的钩子"
BOB = "user-2"


def _index_of(person: str) -> str:
    """一个人的 private 索引在会话里的路径：注入里有它，没有它的正文。"""
    return prompt_path(f"private/{person}/{INDEX_NAME}")


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
                writer=MemoryWriter.panel,
            )

        await put(MemoryFileScope.team, None, TEAM_HOOK)
        await put(MemoryFileScope.private, OWNER, OWNER_HOOK)
        await put(MemoryFileScope.private, BOB, BOB_HOOK)

    _db(client, go)


def _delivery_of(client) -> tuple[uuid.UUID, dict]:
    """那一轮的投递：调度器写的那一笔，payload 是它自己留的。"""

    async def read(session):
        rows = list(await session.scalars(select(Delivery)))
        match = [
            row for row in rows if (row.payload or {}).get("eventType") == "routine_run"
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
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        compute=ComputePool([screen.runtime]),
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


def test_a_routine_run_in_a_shared_room_carries_nobodys_private_memory(
    client, tmp_path
):
    project = _project(client)
    room = _room(client, project)
    rule = _weekly(client, room, headers=PERSON).json()["data"]
    assert rule["owner_handle"] == OWNER
    _lay_indexes(client, project)

    _make_due(client, rule["id"])
    _, runner = _sweep(client)
    assert len(runner.submitted) == 1, "没有派出一轮"
    conversation, submitted = runner.submitted[0]
    delivery_id, payload = _delivery_of(client)
    assert submitted["delivery_id"] == delivery_id
    assert payload["eventType"] == "routine_run"
    assert submitted["content"] == payload["content"]

    # The run is answered where the runner was told to: the run's own 支线.
    prompt = _prompt_of_the_routine_turn(client, tmp_path, conversation, submitted)
    assert TEAM_HOOK in prompt
    for absent in (_index_of(OWNER), OWNER_HOOK, _index_of(BOB), BOB_HOOK):
        assert absent not in prompt


def test_a_persons_turn_in_a_room_with_a_routine_carries_nobodys_private_memory(
    client, tmp_path
):
    """同一间房里 bob 问一句：他自己的那一份、规则主人的那一份都不在。"""
    project = _project(client)
    room = _room(client, project)
    _weekly(client, room, headers=PERSON)
    _lay_indexes(client, project)

    screen = Screen()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        compute=ComputePool([screen.runtime]),
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
    assert TEAM_HOOK in screen.told
    for absent in (_index_of(OWNER), OWNER_HOOK, _index_of(BOB), BOB_HOOK):
        assert absent not in screen.told
