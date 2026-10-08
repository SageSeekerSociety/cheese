"""改一条已有的记忆，过一个回合边界之后：要么进了平台，要么写它的 agent
听说了为什么没进。

agent 在某一间房里改 `team/MEMORY.md`，回合结束时对一次账，下一回合开场再对一次。
别的房间同时也在改索引是常态：各加各的一行，两行都进平台。平台不收它那一版有两种
正当理由——别人把同一行改成了另一个样子（平台赢），或者它新写的那一行超了上限——
两种都会在下一回合把磁盘铺回平台的版本。铺回去本身不是丢数据，
**没人告诉它**才是：它以为写成功了，下一轮照样写，照样被盖（FB-79）。

所以这里钉的是写它的那间房能读到什么：它下一轮 prompt 里那条 `agent_notice`
（`BlockRepository.turn_history` 正是一轮取输入的那个读法），而不是项目总览——总
览里那位 agent 什么都没写过。
"""

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.runner import Runner, memory_root
from app.domain.agent.live_work import LiveWork
from app.domain.agent.memory_ledger import MemoryLedger
from app.domain.block.models import agent_notice
from app.domain.block.repositories import BlockRepository
from app.domain.memory.files import INDEX_NAME, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio

_INDEX = "- [先给结论](answer-first.md) — 回答先说结论，理由跟在后面\n"
_ADDED = "- [测试连真库](tests-hit-a-real-db.md) — 集成测试不 mock 数据库\n"
_TOO_LONG = f"- [很长的一行]({'x' * 40}.md) — {'这一行把细节全写进了索引' * 12}\n"
_ELSEWHERE = "- [别的房间加的](from-another-room.md) — 另一间房同时写的一行\n"
_REWORDED = "- [先给结论](answer-first.md) — 先说结论\n"
_REWORDED_ELSEWHERE = "- [先给结论](answer-first.md) — 结论放在第一句\n"


class _Session:
    """会话机那一半：真的 `Runner.sync_memory`，一间房一个家，都在 `home` 下。

    一间房一棵树（会话的家是那一间房的），所以两间房在这里各有各的 `$HOME`。
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self.runner = object.__new__(Runner)
        self.runner.launch = "test"

    def tree(self, topic_id: uuid.UUID) -> Path:
        os.environ["HOME"] = str(self.home / str(topic_id))
        return memory_root()

    async def memory(self, session: SessionRef, request: dict) -> dict:
        self.tree(session.topic_id)
        return self.runner.sync_memory(request)


def _seat(project_id: uuid.UUID, room: uuid.UUID) -> SessionRef:
    """房间里那位 agent 的会话：对账按座位来。"""
    return SessionRef(project_id, room, "cheese", harness=CLAUDE_CODE)


async def _setup(factory) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """一个项目、它的总览、另一间房；team 的索引里已经有一行。"""
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        room = await TopicService(session).create(
            project_id=project.id, title="排查", created_by="u"
        )
        await MemoryFileStore(session).write(
            project_id=project.id,
            scope=MemoryFileScope.team,
            owner_handle=None,
            path=INDEX_NAME,
            content=_INDEX,
            updated_by="someone",
            expected_version=None,
        )
        await session.commit()
        assert project.root_topic_id is not None
        return project.id, project.root_topic_id, room.id


async def _another_room(factory, project_id: uuid.UUID) -> uuid.UUID:
    async with factory() as session:
        room = await TopicService(session).create(
            project_id=project_id, title="另一件事", created_by="u"
        )
        await session.commit()
        return room.id


def _ledger(factory, sessions: _Session) -> MemoryLedger:
    return MemoryLedger(
        sessions=factory,
        compute=sessions,  # type: ignore[arg-type]
        gateway=None,
        gateway_lock=asyncio.Lock(),
        base_prompt="",
        host=None,  # type: ignore[arg-type]
        live=LiveWork(),
    )


async def _index(factory, project_id: uuid.UUID) -> tuple[str, int]:
    async with factory() as session:
        rows = await MemoryFileStore(session).list(
            project_id, MemoryFileScope.team, None
        )
    row = next(row for row in rows if row.path == INDEX_NAME)
    return row.content, row.version


async def _told(factory, room_id: uuid.UUID) -> str:
    """这间房下一轮 prompt 里会带上的那几句给 agent 的话。"""
    async with factory() as session:
        history = await BlockRepository(session).turn_history(room_id)
    return "\n".join(said for block in history if (said := agent_notice(block)))


async def test_an_edit_to_an_existing_memory_reaches_the_platform(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    index = sessions.tree(room) / "team" / INDEX_NAME

    await ledger.sync(_seat(project_id, room))  # 回合开场：铺下去
    index.write_text(_INDEX + _ADDED, encoding="utf-8")
    await ledger.sync(_seat(project_id, room))  # 回合结束：收回来
    await ledger.sync(_seat(project_id, room))  # 下一回合开场

    assert await _index(factory, project_id) == (_INDEX + _ADDED, 2)
    assert index.read_text(encoding="utf-8") == _INDEX + _ADDED


async def test_two_rooms_each_adding_an_index_line_both_keep_theirs(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    other = await _another_room(factory, project_id)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)

    # 两间房同一份索引开场，各自记下一条新记忆、各在索引末尾加一行。
    await ledger.sync(_seat(project_id, room))
    await ledger.sync(_seat(project_id, other))
    (sessions.tree(room) / "team" / INDEX_NAME).write_text(
        _INDEX + _ADDED, encoding="utf-8"
    )
    (sessions.tree(other) / "team" / INDEX_NAME).write_text(
        _INDEX + _ELSEWHERE, encoding="utf-8"
    )
    await ledger.sync(_seat(project_id, other))  # 另一间房先收
    await ledger.sync(
        _seat(project_id, room)
    )  # 这一间后收：平台那一份已经不是它开场时的那一份
    await ledger.sync(_seat(project_id, other))  # 另一间房的下一回合开场

    content, _ = await _index(factory, project_id)
    assert content == _INDEX + _ADDED + _ELSEWHERE
    for topic in (room, other):
        assert (sessions.tree(topic) / "team" / INDEX_NAME).read_text(
            encoding="utf-8"
        ) == content
        assert not (sessions.tree(topic) / "team" / "MEMORY.conflict.md").exists()
        assert "MEMORY.md" not in await _told(factory, topic)


async def test_an_index_line_over_the_limit_is_told_to_the_agent_that_wrote_it(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, overview, room = await _setup(factory)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    index = sessions.tree(room) / "team" / INDEX_NAME

    await ledger.sync(_seat(project_id, room))
    index.write_text(_INDEX + _ADDED + _TOO_LONG, encoding="utf-8")
    await ledger.sync(_seat(project_id, room))
    await ledger.sync(_seat(project_id, room))

    # 不收是规矩：这一版有一行超了上限。
    assert await _index(factory, project_id) == (_INDEX, 1)
    told = await _told(factory, room)
    assert "~/.cheese/memory/team/MEMORY.md" in told
    assert "~/.cheese/memory/team/MEMORY.rejected.md" in told
    assert (sessions.tree(room) / "team" / "MEMORY.rejected.md").read_text(
        encoding="utf-8"
    ) == _INDEX + _ADDED + _TOO_LONG
    # 总览里那位什么都没写，这句话不是说给它的。
    assert "MEMORY.md" not in await _told(factory, overview)


async def test_a_line_over_the_limit_is_refused_even_when_merged(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    index = sessions.tree(room) / "team" / INDEX_NAME

    await ledger.sync(_seat(project_id, room))
    await _written_elsewhere(factory, project_id, _INDEX + _ELSEWHERE)
    index.write_text(_INDEX + _TOO_LONG, encoding="utf-8")
    await ledger.sync(_seat(project_id, room))

    assert await _index(factory, project_id) == (_INDEX + _ELSEWHERE, 2)
    assert "~/.cheese/memory/team/MEMORY.rejected.md" in await _told(factory, room)


async def test_an_edit_the_platform_overwrote_is_told_to_the_agent_that_wrote_it(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, overview, room = await _setup(factory)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    index = sessions.tree(room) / "team" / INDEX_NAME

    await ledger.sync(_seat(project_id, room))
    # 这一轮里，别的房间先把索引里那一行改了一个说法；这间房的 agent 在它手里那
    # 一份上把同一行改成了另一个说法——同一处两种改法，合不了。
    await _written_elsewhere(factory, project_id, _REWORDED_ELSEWHERE)
    index.write_text(_REWORDED, encoding="utf-8")
    await ledger.sync(_seat(project_id, room))

    assert await _index(factory, project_id) == (_REWORDED_ELSEWHERE, 2)
    told = await _told(factory, room)
    assert "~/.cheese/memory/team/MEMORY.md" in told
    assert "~/.cheese/memory/team/MEMORY.conflict.md" in told
    assert (sessions.tree(room) / "team" / "MEMORY.conflict.md").read_text(
        encoding="utf-8"
    ) == _REWORDED
    assert "MEMORY.md" not in await _told(factory, overview)


async def _written_elsewhere(factory, project_id: uuid.UUID, content: str) -> None:
    """别的房间在这一轮里把平台上的索引改成了 `content`。"""
    async with factory() as session:
        await MemoryFileStore(session).write(
            project_id=project_id,
            scope=MemoryFileScope.team,
            owner_handle=None,
            path=INDEX_NAME,
            content=content,
            updated_by="another-room",
            expected_version=1,
        )
        await session.commit()
