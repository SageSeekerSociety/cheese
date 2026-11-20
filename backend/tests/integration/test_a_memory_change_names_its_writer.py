"""一条改动记录说得出它是从哪条路来的，而且记录只留一段时间。

同一个芝士的 handle 既可能是一轮对话收回来的改动，也可能是整理；人要撤销一次
改动之前，先要知道它是哪一种。这里钉的是不经过面板的那几条路：会话对账收回来
的改动记成 ``turn``，整理的记成它那一轮。
"""

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.domain.day_windows import utc_now
from app.domain.memory import history
from app.domain.memory.files import INDEX_NAME, MemoryFileScope, without_entries
from app.domain.memory.files_store import MemoryFileStore
from app.domain.memory.models import MemoryFileChange
from app.domain.memory.session import apply_tree, read_tree
from app.domain.memory.writers import MemoryWriter
from tests.integration.test_a_memory_edit_is_kept_or_told_across_turns import (
    _INDEX,
    _ledger,
    _seat,
    _Session,
    _setup,
)
from tests.integration.test_what_a_dream_leaves_behind import (
    SECRET,
    TEAM_TIDIED,
    TIDIED,
    WorstScreen,
    _dream,
    _tidies,
)
from tests.support.dream_gateway import answer_dream_calls

_ADDED = "- [测试连真库](tests-hit-a-real-db.md) — 集成测试不 mock 数据库\n"


async def _team_changes(factory, project_id):
    async with factory() as session:
        rows, _ = await history.changes(session, project_id, MemoryFileScope.team, None)
        return rows


@pytest.mark.anyio
async def test_what_a_session_wrote_is_recorded_as_that_turn(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    factory = business_db_factory
    project_id, _, room = await _setup(factory)
    sessions = _Session(tmp_path)
    ledger = _ledger(factory, sessions)
    index = sessions.tree(room) / "team" / INDEX_NAME

    await ledger.sync(_seat(project_id, room))
    index.write_text(_INDEX + _ADDED, encoding="utf-8")
    await ledger.sync(_seat(project_id, room))

    latest = (await _team_changes(factory, project_id))[0]
    assert (latest.path, latest.writer) == (INDEX_NAME, MemoryWriter.turn)
    assert (latest.before, latest.after) == (_INDEX, _INDEX + _ADDED)


@pytest.mark.anyio
async def test_changes_older_than_the_retention_window_are_gone(
    business_db_factory, pinned_clock
):
    factory = business_db_factory
    project_id, _, _ = await _setup(factory)
    async with factory() as session:
        store = MemoryFileStore(session)
        row = await store.get(project_id, MemoryFileScope.team, None, INDEX_NAME)
        assert row is not None
        await store.write(
            project_id=project_id,
            scope=MemoryFileScope.team,
            owner_handle=None,
            path=INDEX_NAME,
            content=_INDEX + _ADDED,
            updated_by="alice",
            expected_version=row.version,
            writer=MemoryWriter.panel,
        )
        await session.commit()
    # Two changes: the seed's creation, then this edit (newest first).
    recent, old = await _team_changes(factory, project_id)
    async with factory() as session:
        await session.execute(
            update(MemoryFileChange)
            .where(MemoryFileChange.id == old.id)
            .values(created_at=utc_now() - history.RETENTION - timedelta(hours=1))
        )
        await session.commit()

    await history.purge_expired(factory)

    assert [c.id for c in await _team_changes(factory, project_id)] == [recent.id]


def test_each_pass_of_a_dream_is_recorded_as_that_pass(client, tmp_path, monkeypatch):
    """The team pass and a person's private pass each leave their changes
    under their own writer, in their own scope's history."""
    monkeypatch.setenv("HOME", str(tmp_path))
    answer_dream_calls(monkeypatch, _tidies)
    result, ids = client.portal.call(lambda: _dream(client, WorstScreen(tmp_path)))
    assert result["status"] == "completed", result

    async def latest(scope, owner):
        async with client.test_request_factory() as session:
            rows, _ = await history.changes(session, ids["project"], scope, owner)
            return rows[0]

    team = client.portal.call(lambda: latest(MemoryFileScope.team, None))
    assert (team.path, team.writer) == ("shared-rule.md", MemoryWriter.dream_team)
    assert TEAM_TIDIED in (team.after or "")
    bobs = client.portal.call(lambda: latest(MemoryFileScope.private, "bob"))
    assert (bobs.path, bobs.writer) == (f"{SECRET}.md", MemoryWriter.dream_private)
    assert TIDIED in (bobs.after or "")


_KEEP = "---\nname: keep\ndescription: Keep it\ntype: feedback\n---\n\nKeep it.\n"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("writer", "owner"),
    [
        (MemoryWriter.turn, None),
        (MemoryWriter.dream_team, None),
        (MemoryWriter.dream_private, "alice"),
        (MemoryWriter.panel, None),
    ],
)
async def test_a_deleted_memory_comes_back_with_its_index_line_whatever_deleted_it(
    business_db_factory, writer, owner
):
    """A reconcile or a dream pass writes the trimmed index before it deletes
    the file; the panel deletes the file first. Either way, restoring the
    deletion brings back the index line the same change took out, so the next
    turn's index points at the memory again."""
    factory = business_db_factory
    project_id, _, _ = await _setup(factory)
    scope = MemoryFileScope.team if owner is None else MemoryFileScope.private
    prefix = "team" if owner is None else f"private/{owner}"
    path = "keep-it.md"
    line = f"- [Keep]({path}) — Keep it"
    common = {
        "project_id": project_id,
        "scope": scope,
        "owner_handle": owner,
        "updated_by": "alice",
        "writer": MemoryWriter.panel,
    }
    async with factory() as session:
        store = MemoryFileStore(session)
        index = await store.get(project_id, scope, owner, INDEX_NAME)
        await store.write(**common, path=path, content=_KEEP, expected_version=None)
        await store.write(
            **common,
            path=INDEX_NAME,
            content=(index.content if index else "") + line + "\n",
            expected_version=index.version if index else None,
        )
        await session.commit()

    async with factory() as session:
        if writer is MemoryWriter.panel:
            await MemoryFileStore(session).forget(**common, paths=[path])
        else:
            scopes = [(scope, owner)]
            stored = await read_tree(session, project_id, scopes)
            files = {
                f"{prefix}/{name}": body
                for name, body in stored.scopes[prefix].items()
                if name != path
            }
            files[f"{prefix}/{INDEX_NAME}"] = without_entries(
                stored.scopes[prefix][INDEX_NAME], {path}
            )
            await apply_tree(
                session,
                project_id,
                stored,
                {"files": files},
                scopes=scopes,
                updated_by="cheese",
                writer=writer,
            )
        await session.commit()

    async with factory() as session:
        rows, _ = await history.changes(session, project_id, scope, owner)
        deletion = next(c for c in rows if c.path == path and c.after is None)
        assert deletion.writer is writer
        await history.restore(session, deletion, by="alice")
        await session.commit()
        store = MemoryFileStore(session)
        restored = await store.get(project_id, scope, owner, path)
        index = await store.get(project_id, scope, owner, INDEX_NAME)
    assert restored is not None and restored.content == _KEEP
    assert index is not None and line in index.content.splitlines()
