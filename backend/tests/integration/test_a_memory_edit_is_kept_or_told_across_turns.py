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

import json
import os
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.runner import Runner, memory_root
from app.domain.agent.memory_ledger import MemoryLedger
from app.domain.agent.run_records import for_site
from app.domain.agent_instance.adapters import named_teammate
from app.domain.block.models import agent_notice
from app.domain.block.repositories import BlockRepository
from app.domain.memory.files import INDEX_NAME, MemoryFileScope, prompt_path
from app.domain.memory.files_store import MemoryFileStore
from app.domain.topic.services import TopicService
from tests.integration.conftest import project_service, registered

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _no_seat_left_over(monkeypatch):
    """`_Session.tree` points the runner at a seat through the environment;
    this puts it back after the test."""
    monkeypatch.delenv("CHEESE_EXECUTION_CONFIG", raising=False)


_INDEX = "- [先给结论](answer-first.md) — 回答先说结论，理由跟在后面\n"
_ADDED = "- [测试连真库](tests-hit-a-real-db.md) — 集成测试不 mock 数据库\n"
_TOO_LONG = f"- [很长的一行]({'x' * 40}.md) — {'这一行把细节全写进了索引' * 12}\n"
_ELSEWHERE = "- [别的房间加的](from-another-room.md) — 另一间房同时写的一行\n"
_REWORDED = "- [先给结论](answer-first.md) — 先说结论\n"
_REWORDED_ELSEWHERE = "- [先给结论](answer-first.md) — 结论放在第一句\n"


class _Session:
    """会话机那一半：真的 `Runner.sync_memory`，一个座位一棵树，都在 `home` 下。

    树在座位自己的目录里（`runner.memory_root`，按座位的执行配置），所以两间房、
    同一间房的两个座位，在这里各有各的一棵。
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self.runner = object.__new__(Runner)
        self.runner.launch = "test"

    def tree(self, topic_id: uuid.UUID, agent: str = "cheese") -> Path:
        seat = self.home / str(topic_id) / ".cheese/seats" / agent
        os.environ["CHEESE_EXECUTION_CONFIG"] = str(
            seat / "remote-session/execution.json"
        )
        return memory_root()

    async def memory(self, session: SessionRef, request: dict) -> dict:
        self.tree(session.topic_id, session.agent_handle)
        return self.runner.sync_memory(request)


def _seat(project_id: uuid.UUID, room: uuid.UUID) -> SessionRef:
    """房间里那位 agent 的会话：对账按座位来。"""
    return SessionRef(project_id, room, "cheese", harness=CLAUDE_CODE)


async def _setup(factory) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """一个项目、它的总览、另一间房；team 的索引里已经有一行。"""
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
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
        host=None,  # type: ignore[arg-type]
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


async def _site(factory, room_id: uuid.UUID) -> list[str]:
    """What the people in this room read in 现场 that the platform wrote."""
    async with factory() as session:
        records = await for_site(session, room_id, since=None, until=None)
    return [record["content"] for record in records]


async def _records(factory, room_id: uuid.UUID) -> list[dict]:
    """Every record the platform kept in this room, its detail included."""
    async with factory() as session:
        return await for_site(session, room_id, since=None, until=None)


async def test_the_room_reads_in_the_site_what_its_agent_was_told(
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

    async with factory() as session:
        history = await BlockRepository(session).turn_history(room)
    [line] = [block.content for block in history if agent_notice(block)]
    # The agent hears why its line was not kept; the people in its room read
    # that line in 现场, and the overview, where nobody wrote, does not.
    assert line in await _site(factory, room)
    assert line not in await _site(factory, overview)


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


async def test_what_a_private_channel_learns_stays_out_of_the_shared_memory(
    business_db_factory, tmp_path, monkeypatch
):
    """Only the people in a private channel may read what is said there, and
    everyone in the project reads the shared memory (#3830): an edit to `team/`
    made in that channel never reaches the platform."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, _ = await _setup(factory)
    async with factory() as session:
        private = (
            await TopicService(session).create(
                project_id=project_id, title="私下", created_by="u", members_only=True
            )
        ).id
        await session.commit()
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)

    await ledger.sync(_seat(project_id, private))
    assert (sessions.tree(private) / "team" / INDEX_NAME).read_text(
        encoding="utf-8"
    ) == _INDEX
    (sessions.tree(private) / "team" / INDEX_NAME).write_text(
        _INDEX + _ADDED, encoding="utf-8"
    )
    await ledger.sync(_seat(project_id, private))
    await ledger.sync(_seat(project_id, private))

    assert await _index(factory, project_id) == (_INDEX, 1)


async def _two_people(factory, project_id: uuid.UUID, index: str = "") -> None:
    """alice 和 bob 在这个项目里各有一份 private 索引。"""
    async with factory() as session:
        for owner in ("alice", "bob"):
            await MemoryFileStore(session).write(
                project_id=project_id,
                scope=MemoryFileScope.private,
                owner_handle=owner,
                path=INDEX_NAME,
                content=index or f"- [{owner} 的偏好]({owner}.md) — 只是 {owner} 的\n",
                updated_by=owner,
                expected_version=None,
            )
        await session.commit()


async def test_two_seats_working_for_two_people_at_once_each_hold_only_theirs(
    business_db_factory, tmp_path, monkeypatch
):
    """Two teammates in one room answer two people at the same time. Each
    seat's session is given the shared memory and the private memory of the
    person its turn answers; the other person's is nowhere its file tools
    reach, whichever seat reconciled last."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    await _two_people(factory, project_id)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    first = _seat(project_id, room)
    second = SessionRef(project_id, room, "helper", harness=CLAUDE_CODE)
    ledger.remember_turn(
        room, (room, first.agent_handle), acting="cheese", speaker="alice"
    )
    ledger.remember_turn(
        room, (room, second.agent_handle), acting="helper", speaker="bob"
    )

    await ledger.sync(first)
    await ledger.sync(second)

    alices = sessions.tree(room, first.agent_handle)
    bobs = sessions.tree(room, second.agent_handle)
    assert (alices / "private" / "alice" / INDEX_NAME).exists()
    assert not (alices / "private" / "bob").exists()
    assert (bobs / "private" / "bob" / INDEX_NAME).exists()
    assert not (bobs / "private" / "alice").exists()
    assert (alices / "team" / INDEX_NAME).exists()
    assert (bobs / "team" / INDEX_NAME).exists()


async def test_a_private_memory_written_before_the_seat_turns_to_someone_else_reaches_its_owner(
    business_db_factory, tmp_path, monkeypatch
):
    """One seat answers alice, then bob. What alice's turn wrote into her
    private memory is hers even when no reconciliation ran between the two
    turns: bob's turn hands it to the platform as hers and takes it off the
    tree, and her next turn lays it down again."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    original = "- [她的偏好](note.md) — 原来那一版\n"
    edited = "- [她的偏好](note.md) — 她这一轮改过的\n"
    note = (
        "---\nname: alice-new\ndescription: 新记下的\ntype: feedback\n---\n\n"
        "alice-unsynced-new-note\n"
    )
    await _two_people(factory, project_id, original)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    seat = _seat(project_id, room)
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="alice"
    )
    await ledger.sync(seat)
    hers = sessions.tree(room, seat.agent_handle) / "private" / "alice"
    (hers / INDEX_NAME).write_text(edited, encoding="utf-8")
    (hers / "alice-new.md").write_text(note, encoding="utf-8")

    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="bob"
    )
    await ledger.sync(seat)

    assert not hers.exists()
    async with factory() as session:
        store = MemoryFileStore(session)
        index = await store.get(
            project_id, MemoryFileScope.private, "alice", INDEX_NAME
        )
        written = await store.get(
            project_id, MemoryFileScope.private, "alice", "alice-new.md"
        )
    assert index is not None and index.content == edited
    assert written is not None and written.content == note

    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="alice"
    )
    await ledger.sync(seat)

    assert (hers / INDEX_NAME).read_text(encoding="utf-8") == edited
    assert (hers / "alice-new.md").read_text(encoding="utf-8") == note


_NOTE = "---\nname: first-note\ndescription: 她记下的第一条\ntype: feedback\n---\n\n"


class _LosesOneAnswer(_Session):
    """A session machine whose next answer is lost on the way back: the runner
    did the work, the platform never heard."""

    lose = False

    async def memory(self, session: SessionRef, request: dict) -> dict:
        answer = await super().memory(session, request)
        if self.lose and "scopes" in request:
            self.lose = False
            raise ConnectionError("the answer did not come back")
        return answer


async def _alice_then_bob(factory, tmp_path, sessions, index: str = ""):
    """One seat answers alice, then bob. alice's private memory is empty, or
    holds ``index``."""
    project_id, _, room = await _setup(factory)
    if index:
        await _two_people(factory, project_id, index)
    ledger = _ledger(factory, sessions)
    seat = _seat(project_id, room)
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="alice"
    )
    await ledger.sync(seat)
    hers = sessions.tree(room, seat.agent_handle) / "private" / "alice"
    return project_id, room, ledger, seat, hers


async def _alices(factory, project_id, path: str):
    async with factory() as session:
        return await MemoryFileStore(session).get(
            project_id, MemoryFileScope.private, "alice", path
        )


async def test_a_first_private_memory_reaches_its_owner_when_the_seat_turns_to_someone_else(
    business_db_factory, tmp_path, monkeypatch
):
    """alice has no private memory yet; her turn writes the first one, and the
    seat answers bob next with no reconciliation between. Her note is hers on
    the platform, and not on the seat bob's turn works on."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    sessions = _Session(tmp_path)
    project_id, room, ledger, seat, hers = await _alice_then_bob(
        factory, tmp_path, sessions
    )
    hers.mkdir(parents=True, exist_ok=True)
    (hers / "first-note.md").write_text(_NOTE + "alice-first-note\n", encoding="utf-8")

    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="bob"
    )
    await ledger.sync(seat)

    assert not hers.exists()
    written = await _alices(factory, project_id, "first-note.md")
    assert written is not None and "alice-first-note" in written.content


async def test_a_private_memory_handed_back_on_a_lost_answer_still_reaches_its_owner(
    business_db_factory, tmp_path, monkeypatch
):
    """The seat turns from alice to bob and hands her tree back, but the answer
    carrying it is lost. Nothing of hers is gone: the next reconciliation hands
    it back again, and her next turn lays it down."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    sessions = _LosesOneAnswer(tmp_path)
    project_id, room, ledger, seat, hers = await _alice_then_bob(
        factory, tmp_path, sessions, index="- [她的偏好](note.md) — 已经记着的一条\n"
    )
    hers.mkdir(parents=True, exist_ok=True)
    (hers / "first-note.md").write_text(_NOTE + "alice-kept-note\n", encoding="utf-8")
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="bob"
    )
    sessions.lose = True
    with pytest.raises(ConnectionError):
        await ledger.sync(seat)

    await ledger.sync(seat)
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="alice"
    )
    await ledger.sync(seat)

    written = await _alices(factory, project_id, "first-note.md")
    assert written is not None and "alice-kept-note" in written.content
    assert "alice-kept-note" in (hers / "first-note.md").read_text(encoding="utf-8")


async def test_an_owners_version_the_platform_overwrote_on_hand_back_is_kept_for_them(
    business_db_factory, tmp_path, monkeypatch
):
    """alice's turn edits her note while the platform's copy of it changes too;
    the seat turns to bob before they are reconciled. The platform's version
    wins, as it always does, and alice's version is kept where only she reads
    it, her private chat — not in the room bob's turn works in."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    sessions = _Session(tmp_path)
    project_id, room, ledger, seat, hers = await _alice_then_bob(
        factory, tmp_path, sessions
    )
    async with factory() as session:
        await MemoryFileStore(session).write(
            project_id=project_id,
            scope=MemoryFileScope.private,
            owner_handle="alice",
            path="first-note.md",
            content=_NOTE + "the platform's first version\n",
            updated_by="alice",
            expected_version=None,
        )
        await session.commit()
    await ledger.sync(seat)
    (hers / "first-note.md").write_text(
        _NOTE + "alice-unique-edit-4985\n", encoding="utf-8"
    )
    async with factory() as session:
        store = MemoryFileStore(session)
        row = await store.get(
            project_id, MemoryFileScope.private, "alice", "first-note.md"
        )
        await store.write(
            project_id=project_id,
            scope=MemoryFileScope.private,
            owner_handle="alice",
            path="first-note.md",
            content=_NOTE + "the platform's newer version\n",
            updated_by="alice",
            expected_version=row.version,
        )
        await session.commit()

    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="bob"
    )
    await ledger.sync(seat)

    kept = await _alices(factory, project_id, "first-note.md")
    assert kept is not None and "newer version" in kept.content
    async with factory() as session:
        private = await TopicService(session).get_or_create_private(
            project_id=project_id, user_handle="alice", named_teammate=named_teammate
        )
        await session.commit()
    told_alice = await _told(factory, private.id) + json.dumps(
        await _records(factory, private.id), ensure_ascii=False
    )
    assert "alice-unique-edit-4985" in told_alice
    told_room = await _told(factory, room) + json.dumps(
        await _records(factory, room), ensure_ascii=False
    )
    assert "alice-unique-edit-4985" not in told_room


async def test_a_turn_that_worked_for_someone_without_reading_their_index_is_counted(
    business_db_factory, tmp_path, monkeypatch, caplog
):
    """A turn that answers alice is told where her private index is, and the
    rule is to read it before anything else. One line is logged for each turn
    that worked for her and did not, so skips can be counted in production;
    a turn that read it logs nothing."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    await _two_people(factory, project_id)
    sessions = _Session(tmp_path)
    sessions.runner = Runner(tmp_path / "state")
    sessions.runner.session_id = "session-alice"
    ledger = _ledger(factory, sessions)
    seat = _seat(project_id, room)
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="alice", pointed=True
    )
    await ledger.sync(seat)

    def work(*blocks: dict) -> None:
        for record in (
            {"type": "assistant", "message": {"content": list(blocks)}},
            {"type": "result", "result": "done"},
        ):
            sessions.runner.observe(
                {**record, "session_id": "session-alice", "uuid": str(uuid.uuid4())}
            )

    caplog.set_level("INFO", logger="app.domain.agent.memory_ledger")
    work(
        {
            "type": "tool_use",
            "id": "read-index",
            "name": "Read",
            "input": {"file_path": prompt_path(f"private/alice/{INDEX_NAME}")},
        }
    )
    await ledger.sync(seat)
    assert "memory_private_index_unread" not in caplog.text

    work({"type": "text", "text": "直接回答了"})
    await ledger.sync(seat)
    assert "memory_private_index_unread" in caplog.text
    assert "speaker=alice" in caplog.text


async def test_a_handoff_merged_once_is_not_merged_again_when_its_receipt_is_lost(
    business_db_factory, tmp_path, monkeypatch
):
    """The seat turns from alice to bob and hands her tree back; the platform
    merges it, but the word that it did never reaches the seat. alice then
    deletes the note from her memory. The seat hands the same tree back again
    on the next reconciliation: it is told taken and not merged a second time,
    so her deletion stands."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    await _two_people(factory, project_id, "- [original](note.md) — original\n")
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    seat = _seat(project_id, room)
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="alice"
    )
    await ledger.sync(seat)
    hers = sessions.tree(room, seat.agent_handle) / "private" / "alice"
    (hers / "secret.md").write_text(_NOTE + "alice-wants-this-forgotten-4985\n")
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speaker="bob"
    )
    heard = sessions.memory
    lost = {"once": False}

    async def loses_the_receipt(ref, request):
        if "acked" in request and not lost["once"]:
            lost["once"] = True
            raise ConnectionError("the receipt did not arrive")
        return await heard(ref, request)

    sessions.memory = loses_the_receipt
    with pytest.raises(ConnectionError):
        await ledger.sync(seat)
    merged = await _alices(factory, project_id, "secret.md")
    assert merged is not None
    async with factory() as session:
        await MemoryFileStore(session).delete(
            project_id=project_id,
            scope=MemoryFileScope.private,
            owner_handle="alice",
            path="secret.md",
            expected_version=merged.version,
        )
        await session.commit()

    await ledger.sync(seat)

    assert await _alices(factory, project_id, "secret.md") is None
