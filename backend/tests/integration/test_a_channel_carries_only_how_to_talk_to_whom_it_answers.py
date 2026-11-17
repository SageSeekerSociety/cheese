"""In a multi-person place a turn carries, of a person's private memory, only
how they want to be talked to (`communication`), and only for the people it
answers; in their private chat it carries all of it. The platform picks and
keeps that class by the note's type, and tells the agent about every write it
did not keep.

The seat's half is the real `Runner.sync_memory`, the platform's half the real
`MemoryLedger` on a real database (`test_a_memory_edit_is_kept_or_told_across_turns`).
"""

import uuid

import pytest

from app.domain.agent_instance.adapters import named_teammate
from app.domain.memory.files import INDEX_NAME, MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.writers import MemoryWriter
from app.domain.topic.services import TopicService
from tests.integration.test_a_memory_edit_is_kept_or_told_across_turns import (
    _ledger,
    _no_seat_left_over,  # noqa: F401 — the autouse fixture, for this module too
    _seat,
    _Session,
    _setup,
    _told,
)

pytestmark = pytest.mark.anyio


def _note(name: str, kind: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {body}\ntype: {kind}\n---\n\n{body}\n"


SHORT = _note("reply-short", "communication", "给她的回复简短，先说结论")
REVIEW = _note("review-twice", "feedback", "交给她审阅的改动先多核对一遍")
VIEW = _note("no-countdown", "project", "alice 认为首页不必放倒计时")
BOBS = _note("bob-at", "communication", "要他做的事直接 @ 他")
ALICE_INDEX = (
    "- [回复简短](reply-short.md) — 先说结论\n"
    "- [审阅多核对](review-twice.md) — 交给她的改动多看一遍\n"
    "- [她的看法](no-countdown.md) — 首页不放倒计时\n"
)


async def _lay(factory, project_id: uuid.UUID) -> dict[str, str]:
    """alice 有三条（每类一条）和一份索引，bob 有一条 communication。"""
    alice = {
        "reply-short.md": SHORT,
        "review-twice.md": REVIEW,
        "no-countdown.md": VIEW,
        INDEX_NAME: ALICE_INDEX,
    }
    async with factory() as session:
        store = MemoryFileStore(session)
        for owner, files in (
            ("alice", alice),
            ("bob", {"bob-at.md": BOBS, INDEX_NAME: "- [直接 @](bob-at.md) — 点名\n"}),
        ):
            for path, content in files.items():
                await store.write(
                    project_id=project_id,
                    scope=MemoryFileScope.private,
                    owner_handle=owner,
                    path=path,
                    content=content,
                    updated_by=owner,
                    expected_version=None,
                    writer=MemoryWriter.panel,
                )
        await session.commit()
    return alice


async def _alices(factory, project_id: uuid.UUID) -> dict[str, str]:
    async with factory() as session:
        rows = await MemoryFileStore(session).list(
            project_id, MemoryFileScope.private, "alice"
        )
    return {row.path: row.content for row in rows}


def _on_disk(tree) -> dict[str, str]:
    return {
        str(path.relative_to(tree)): path.read_text(encoding="utf-8")
        for path in sorted(tree.rglob("*"))
        if path.is_file() and not path.name.startswith(".")
    }


async def _channel_turn_for_alice(factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_id, _, room = await _setup(factory)
    stored = await _lay(factory, project_id)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    seat = _seat(project_id, room)
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speakers=("alice",)
    )
    await ledger.sync(seat)
    tree = sessions.tree(room, seat.agent_handle)
    return project_id, room, ledger, seat, tree, stored


async def test_a_channel_turn_gets_only_how_to_talk_to_the_person_it_answers(
    business_db_factory, tmp_path, monkeypatch
):
    """A channel turn answering alice holds her communication note and the
    index line that points to it — not her other notes, not their index lines,
    and nothing of bob's."""
    _, _, _, _, tree, _ = await _channel_turn_for_alice(
        business_db_factory, tmp_path, monkeypatch
    )

    laid = {
        path: body
        for path, body in _on_disk(tree).items()
        if path.startswith("private/")
    }
    assert laid == {
        "private/alice/reply-short.md": SHORT,
        f"private/alice/{INDEX_NAME}": "- [回复简短](reply-short.md) — 先说结论\n",
    }


async def test_a_channel_turn_that_writes_nothing_leaves_her_memory_as_it_was(
    business_db_factory, tmp_path, monkeypatch
):
    """What was not laid down is not read back as deleted: after a channel
    turn, and after the seat turns to someone else, every note and index line
    of hers is still there."""
    factory = business_db_factory
    project_id, room, ledger, seat, _, stored = await _channel_turn_for_alice(
        factory, tmp_path, monkeypatch
    )

    await ledger.sync(seat)
    ledger.remember_turn(
        room, (room, seat.agent_handle), acting="cheese", speakers=("bob",)
    )
    await ledger.sync(seat)

    assert await _alices(factory, project_id) == stored


async def test_a_member_saying_how_to_talk_to_them_in_a_channel_is_kept_as_theirs(
    business_db_factory, tmp_path, monkeypatch
):
    """alice tells the agent in the channel not to @ her; it records that as
    her communication note and adds its index line. Both reach her private
    memory, and nothing else of hers changes."""
    factory = business_db_factory
    project_id, _, ledger, seat, tree, stored = await _channel_turn_for_alice(
        factory, tmp_path, monkeypatch
    )
    new = _note("no-at", "communication", "2026-11-17 在频道里说：不必 @ 她")
    line = "- [不必 @ 她](no-at.md) — 要她定的事再 @\n"
    hers = tree / "private" / "alice"
    (hers / "no-at.md").write_text(new, encoding="utf-8")
    (hers / INDEX_NAME).write_text(
        (hers / INDEX_NAME).read_text(encoding="utf-8") + line, encoding="utf-8"
    )

    await ledger.sync(seat)

    assert await _alices(factory, project_id) == {
        **stored,
        "no-at.md": new,
        INDEX_NAME: ALICE_INDEX + line,
    }


async def test_a_private_note_of_another_kind_from_a_channel_is_refused_and_told(
    business_db_factory, tmp_path, monkeypatch
):
    """In a channel, a note of any other type written into alice's private
    memory is not kept, nor is a change to a note already there, nor a note
    that names another member; the agent hears on its next turn which writes
    were not kept, and why."""
    factory = business_db_factory
    project_id, room, ledger, seat, tree, stored = await _channel_turn_for_alice(
        factory, tmp_path, monkeypatch
    )
    hers = tree / "private" / "alice"
    (hers / "bob-weak.md").write_text(
        _note("bob-weak", "feedback", "bob 的代码不太行，他的改动多检查"),
        encoding="utf-8",
    )
    (hers / "about-u.md").write_text(
        _note("about-u", "communication", "回复她时别提 u 交的东西"), encoding="utf-8"
    )
    (hers / "reply-short.md").write_text(
        _note("reply-short", "communication", "回复要详细"), encoding="utf-8"
    )

    await ledger.sync(seat)

    assert await _alices(factory, project_id) == stored
    told = await _told(factory, room)
    for refused in ("bob-weak.md", "about-u.md", "reply-short.md"):
        assert f"private/alice/{refused}" in told
    assert "communication" in told


async def test_a_write_outside_what_was_laid_down_is_told(
    business_db_factory, tmp_path, monkeypatch
):
    """A memory written where the platform does not collect — another person's
    directory that was not laid down, the tree's root — is not kept, and the
    agent hears so on its next turn instead of finding it gone."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    seat = _seat(project_id, room)
    await ledger.sync(seat)
    tree = sessions.tree(room, seat.agent_handle)
    (tree / "private" / "carol").mkdir(parents=True)
    (tree / "private" / "carol" / "note.md").write_text(
        _note("note", "communication", "回复简短"), encoding="utf-8"
    )
    (tree / "notes.md").write_text("随手记一笔\n", encoding="utf-8")

    await ledger.sync(seat)

    async with factory() as session:
        carols = await MemoryFileStore(session).list(
            project_id, MemoryFileScope.private, "carol"
        )
    assert carols == []
    told = await _told(factory, room)
    assert "private/carol/note.md" in told
    assert "notes.md" in told


async def test_a_private_chat_carries_the_whole_private_memory(
    business_db_factory, tmp_path, monkeypatch
):
    """In alice's private chat every note of hers is laid down, whatever its
    type, and what her turn writes there of any type is kept."""
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, _ = await _setup(factory)
    stored = await _lay(factory, project_id)
    async with factory() as session:
        private = await TopicService(session).get_or_create_private(
            project_id=project_id, user_handle="alice", named_teammate=named_teammate
        )
        await session.commit()
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    seat = _seat(project_id, private.id)
    ledger.remember_turn(
        private.id,
        (private.id, seat.agent_handle),
        acting="cheese",
        speakers=("alice",),
    )
    await ledger.sync(seat)
    hers = sessions.tree(private.id, seat.agent_handle) / "private" / "alice"

    assert _on_disk(hers) == stored
    view = _note("email-field", "project", "2026-11-17 alice 认为报名表不加邮箱")
    (hers / "email-field.md").write_text(view, encoding="utf-8")
    await ledger.sync(seat)

    assert (await _alices(factory, project_id))["email-field.md"] == view
