"""A person's save in 改动 while the AI teammate changes the same file.

Rules: a save over a file that changed since it was read keeps both people's
edits when they touch different lines; when they touch the same lines nothing
is written and the person gets both versions to pick from. A person's save is
said in the task, and the teammate is told to re-read the file before touching
it.
"""

import asyncio

from sqlalchemy import select

from app.domain.block.models import AGENT_NOTICE_META_KEY, Block
from app.domain.room_task.models import Task
from tests.delivery import delivery_task_id
from tests.integration.test_file_panel_safety import (  # noqa: F401 — fixture
    _mkproject,
    _mktopic,
    _owner,
    _put,
    _worktree,
    task_machine,
)


def _read(client, pid, tid, headers):
    return client.get(
        f"/projects/{pid}/file",
        params={
            "path": "note.txt",
            "topic": str(tid),
            "task": str(delivery_task_id(client, tid)),
        },
        headers=headers,
    ).json()["data"]


def _save(client, pid, tid, headers, content, read):
    return client.put(
        f"/projects/{pid}/file",
        params={"topic": str(tid), "task": str(delivery_task_id(client, tid))},
        json={
            "path": "note.txt",
            "content": content,
            "version": read["version"],
            "base": read["content"],
        },
        headers=headers,
    )


def test_edits_on_different_lines_are_both_kept(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "note.txt", "一\n二\n三\n四\n".encode())
    headers = _owner(client)
    read = _read(client, pid, tid, headers)

    # The teammate changes the last line while the person edits the first.
    _put(client, pid, tid, "note.txt", "一\n二\n三\n四（芝士）\n".encode())
    resp = _save(client, pid, tid, headers, "一（人）\n二\n三\n四\n", read)

    assert resp.status_code == 200, resp.text
    disk = (_worktree(client, tid) / "note.txt").read_text(encoding="utf-8")
    assert disk == "一（人）\n二\n三\n四（芝士）\n"
    assert resp.json()["data"]["content"] == disk


def test_edits_on_the_same_line_write_nothing_and_offer_both(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "note.txt", "一\n二\n三\n".encode())
    headers = _owner(client)
    read = _read(client, pid, tid, headers)

    _put(client, pid, tid, "note.txt", "一\n二（芝士）\n三\n".encode())
    resp = _save(client, pid, tid, headers, "一\n二（人）\n三\n", read)

    assert resp.status_code == 409, resp.text
    disk = (_worktree(client, tid) / "note.txt").read_text(encoding="utf-8")
    assert disk == "一\n二（芝士）\n三\n"
    regions = resp.json()["error"]["data"]["regions"]
    (conflict,) = [r for r in regions if r["kind"] == "conflict"]
    assert conflict["mine"] == "二（人）\n"
    assert conflict["theirs"] == "二（芝士）\n"


def test_a_save_is_said_in_the_task_and_told_to_the_teammate(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "note.txt", "一\n二\n".encode())
    headers = _owner(client)
    read = _read(client, pid, tid, headers)

    resp = _save(client, pid, tid, headers, "一\n二改了\n", read)
    assert resp.status_code == 200, resp.text

    task_id = delivery_task_id(client, tid)

    async def lines():
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block).where(Block.conversation_id == task_id)
            )
            return [
                b
                for b in rows
                if (b.meta or {}).get("event_type") == "file_edited"
            ]

    (line,) = asyncio.run(lines())
    told = line.meta[AGENT_NOTICE_META_KEY]
    assert "note.txt" in told and "重新读取" in told


def test_someone_other_than_the_owner_who_edits_is_credited(client):
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _put(client, pid, tid, "note.txt", "一\n".encode())
    task_id = delivery_task_id(client, tid)

    async def hand_to(owner: str):
        async with client.test_factory() as session:
            row = await session.get(Task, task_id)
            row.owner_handle = owner
            await session.commit()

    asyncio.run(hand_to("bob"))
    headers = _owner(client)
    read = _read(client, pid, tid, headers)
    assert _save(client, pid, tid, headers, "一改了\n", read).status_code == 200

    async def credited():
        async with client.test_factory() as session:
            return (await session.get(Task, task_id)).contributor_handles

    assert "alice" in asyncio.run(credited())
