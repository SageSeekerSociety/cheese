"""Forge merge requests attribute only the task whose branch is delivered."""

import asyncio
import itertools
import subprocess
import uuid

import pytest

from tests.delivery import delivery_artifact, delivery_headers
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_accept_pr import _rendered_head, app_world  # noqa: F401
from tests.machine_work import declare_task, machine_commits


@pytest.fixture(autouse=True)
def remote_delivery(client, request):
    client.trace_forge = request.getfixturevalue("app_world")["fake"]


def _task_line(pid: str, room: str, task: str, subagent: str, title: str) -> str:
    """`Cheese-Task:` 一条活写一行：**打得开的地址**、分身、标题。

    地址是房间页面加 `?tab=overview&card=<task_id>` —— 点开一条活时房间页写进地址
    栏的就是这两个查询串。活的 id 仍在地址里，`card=` 后面那一段就是。
    """
    from app.core.config import settings

    where = f"{settings.frontend_url.rstrip('/')}/projects/{pid}/topics/{room}"
    return f"Cheese-Task: {where}?tab=overview&card={task} {subagent} {title}"


def _project(client) -> str:
    return post_project(client, json={"name": "P"}).json()["data"]["id"]


def _room(client, project_id: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": "做一个东西", "created_by": "alice"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _dispatch(client, room_id: str, title: str) -> str:
    """One piece of work in the room, through the only door that makes one."""
    r = client.post(
        f"/topics/{room_id}/split",
        json=dict(reviewer_handle="alice", **{"title": title}),
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["data"]["id"]
    room = client.get(f"/topics/{room_id}").json()["data"]
    from app.core.sandbox_auth import mint_scoped_token

    opened = client.post(
        f"/projects/{room['project_id']}/git/tasks/{task_id}",
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=room["project_id"],
                topic_id=room_id,
                agent_handle=room_agent_seat(client, room_id),
            )
        },
    )
    assert opened.status_code == 200, opened.text
    declare_task(uuid.UUID(room["project_id"]), uuid.UUID(task_id))
    return task_id


def _worker_starts(client, task_id: str, agent_id: str) -> None:
    """平台看见一个分身在这条活上开工，把它的 id 记在卡上。

    它就是 `Cheese-Task:` 那一行里说出「哪台机器干的」的那个字串。真实路径上写它的是
    分身的开工事件（`ChatService._note_worker`）；这里的测试不跑轮次，所以直接踩同一
    个缝。
    """
    from app.domain.room_task.services import TaskService

    async def _write() -> None:
        async with client.test_factory() as session:
            tasks = TaskService(session)
            task = await tasks.get(uuid.UUID(task_id))
            assert task is not None
            await tasks.note_worker(task, agent_id)
            await session.commit()

    asyncio.run(_write())


def _file_card(client, room_id: str, subject: str, tasks: list[str] | None = None):
    assert tasks and len(tasks) == 1
    return client.post(
        f"/topics/{room_id}/tasks/{tasks[0]}/accept-card",
        headers=delivery_headers(client, room_id),
        json={
            **delivery_artifact(client, room_id),
            "change_subject": subject,
            "change_body": "Who wrote this, on the record.",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
    )


def _deliver(
    client, room_id: str, subject: str, tasks: list[str] | None = None
) -> dict:
    r = _file_card(client, room_id, subject, tasks)
    assert r.status_code == 200, r.text
    result = r.json()["data"]

    async def attach_pr():
        from app.domain.review.repositories import AcceptCardRepository
        from tests.support.git_store import branch_for_task

        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(uuid.UUID(result["id"]))
            number = 100 + len(client.trace_forge.prs)
            head = client.trace_forge.seed_pr(
                number, head=branch_for_task(uuid.UUID(tasks[0]))
            )
            client.trace_forge.check_state_by_sha[head] = ("success", "")
            card.pr_number = number
            card.pr_url = f"https://github.com/acme/widgets/pull/{number}"
            card.pr_head_sha = head
            await session.commit()

    asyncio.run(attach_pr())
    return result


def _accept(client, card_id: str) -> None:
    r = client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card_id)},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _landed_body(client) -> str:
    merge = client.trace_forge.merge_calls[-1]
    return f"{merge['commit_title']}\n\n{merge['commit_message']}"


_written = itertools.count()


def _batch(client, pid: str, room: str, subject: str, tasks: list[str]) -> str:
    """Write something, deliver it naming *tasks*, and return what landed.

    A fresh file every time, because a room delivering twice has to have
    something new on its branch the second time — that is what "还有东西可以交付"
    means, and re-writing the same bytes is a no-op git refuses to commit.
    """
    name = f"work-{next(_written)}.txt"
    machine_commits(uuid.UUID(pid), uuid.UUID(tasks[0]), {name: subject})
    card = _deliver(client, room, subject, tasks)
    _accept(client, card["id"])
    landed = _landed_body(client)
    # The commit THIS delivery produced, not whatever was on main already: a
    # second batch that quietly merged nothing would otherwise be checked
    # against the first one's trailers and pass for the wrong reason.
    assert landed.splitlines()[0].startswith(subject + " (#"), landed
    return landed


def test_the_landed_commit_names_the_agent_and_every_worker_declared(client):
    pid = _project(client)
    room = _room(client, pid)
    mine = _dispatch(client, room, "补 trailer")
    theirs = _dispatch(client, room, "顺手修 flaky 测试")
    _worker_starts(client, mine, "ac2c038d44616a2f2")
    _worker_starts(client, theirs, "9f1b7c22e0d341a80")
    machine_commits(uuid.UUID(pid), uuid.UUID(mine), {"a.txt": "one\n"})

    card = _deliver(client, room, "feat: deliver one task", [mine])
    _accept(client, card["id"])

    body = _landed_body(client)
    assert f"Cheese-Agent: {room_agent_seat(client, room)}" in body
    assert _task_line(pid, room, mine, "ac2c038d44616a2f2", "补 trailer") in body
    assert (
        _task_line(pid, room, theirs, "9f1b7c22e0d341a80", "顺手修 flaky 测试")
        not in body
    )
    # The trailers that were already there did not move over to make room.
    assert f"/topics/{room}" in body
    assert f"Cheese-Card: {card['id']}" in body
    assert "Reviewed-by: alice <alice@zhishi.local>" in body


# --- 跨批次 ---------------------------------------------------------------
#
# A room does not stop working when a batch goes out. The task rows dispatched
# for the batch that just landed stay where they were dispatched — on the tree
# that just merged — while the work they are still doing goes out on the NEXT
# tree. Which is why "the batch" cannot be the delivering tree's membership: it
# is right only for a room that dispatches nothing and delivers once.


def _two_batches(client) -> tuple[str, str, str, str]:
    """A room that delivers twice: `earlier`'s code goes out in the first batch,
    `later`'s in the second. Both are dispatched up front, so both hang on the
    FIRST tree — the situation every long-running room is in."""
    pid = _project(client)
    room = _room(client, pid)
    earlier = _dispatch(client, room, "上一批写完的活")
    later = _dispatch(client, room, "代码走下一批的活")
    _worker_starts(client, earlier, "aaaa0000aaaa0000a")
    _worker_starts(client, later, "bbbb1111bbbb1111b")
    return pid, room, earlier, later


def test_work_delivered_in_a_later_batch_is_named_on_that_batch(client):
    """The task rides a tree that finished batches ago; its code is in THIS one.
    Enumerating the delivering tree misses it entirely — its tree is not this
    tree — and the 分身 that wrote the change vanishes from the history."""
    pid, room, earlier, later = _two_batches(client)

    _batch(client, pid, room, "feat: the first batch", [earlier])
    second = _batch(client, pid, room, "feat: the second batch", [later])

    assert (
        _task_line(pid, room, later, "bbbb1111bbbb1111b", "代码走下一批的活") in second
    )


def test_the_earlier_batch_is_not_signed_by_work_that_had_not_landed_yet(client):
    """The other direction of the same bug, and the worse one: the first
    delivery would be signed by a worker whose code was not in it, and an audit
    reading `git log` has no way to tell that from a real signature."""
    pid, room, earlier, later = _two_batches(client)

    first = _batch(client, pid, room, "feat: the first batch", [earlier])

    assert (
        _task_line(pid, room, earlier, "aaaa0000aaaa0000a", "上一批写完的活") in first
    )
    assert later not in first


def test_a_placeholder_task_that_wrote_no_code_is_never_signed_on(client):
    """A task row can exist for something other than writing code — a carrier
    for a discussion, a placeholder. It is on the tree and no card has claimed
    it, so every rule a machine could apply admits it; only the room knows it
    contributed nothing."""
    pid = _project(client)
    room = _room(client, pid)
    placeholder = _dispatch(client, room, "只是个占位")
    real = _dispatch(client, room, "真的写了代码")
    _worker_starts(client, real, "cccc2222cccc2222c")

    body = _batch(client, pid, room, "feat: deliver only what was written", [real])

    assert _task_line(pid, room, real, "cccc2222cccc2222c", "真的写了代码") in body
    assert placeholder not in body


def test_work_still_running_is_not_signed_onto_the_batch_going_out_now(client):
    """The sibling case: another thread is live in the same room right now and
    its changes go out next time. It is open, bound, on the delivering tree and
    unclaimed by anything — and it belongs in neither this commit nor this
    reader's idea of who wrote it."""
    pid = _project(client)
    room = _room(client, pid)
    done = _dispatch(client, room, "这批做完的活")
    running = _dispatch(client, room, "还在跑的活")
    _worker_starts(client, done, "dddd3333dddd3333d")
    _worker_starts(client, running, "eeee4444eeee4444e")

    now = _batch(client, pid, room, "feat: land only the finished half", [done])
    assert _task_line(pid, room, done, "dddd3333dddd3333d", "这批做完的活") in now
    assert running not in now

    later = _batch(client, pid, room, "feat: land the other half", [running])
    assert _task_line(pid, room, running, "eeee4444eeee4444e", "还在跑的活") in later


def test_work_no_worker_ever_took_still_appears_when_it_is_declared(client):
    """`subagent_id` is NULL until a worker starts, and a room can write a
    change itself. Dropping the row would make the batch in the commit smaller
    than the batch the room said it delivered."""
    pid = _project(client)
    room = _room(client, pid)
    unclaimed = _dispatch(client, room, "没人认领的活")

    body = _batch(client, pid, room, "feat: deliver work nobody claimed", [unclaimed])

    assert _task_line(pid, room, unclaimed, "-", "没人认领的活") in body


def test_unreadable_work_costs_the_trailers_and_not_the_merge(client, monkeypatch):
    """Best-effort, like every other part of attribution: whatever else is wrong,
    a trailer must never be the reason a delivery fails to land."""
    from app.domain.room_task.services import TaskService

    pid = _project(client)
    room = _room(client, pid)
    mine = _dispatch(client, room, "补 trailer")
    machine_commits(uuid.UUID(pid), uuid.UUID(mine), {"a.txt": "one\n"})
    card = _deliver(client, room, "feat: land when the batch is unreadable", [mine])

    async def _blow_up(_self, _task_ids):
        raise RuntimeError("the batch could not be read")

    monkeypatch.setattr(TaskService, "list_by_ids", _blow_up)
    _accept(client, card["id"])

    body = _landed_body(client)
    assert body.splitlines()[0].startswith("feat: land when the batch is unreadable (#")
    assert "Cheese-Task" not in body
    assert f"Cheese-Card: {card['id']}" in body


# --- What the declaration is checked against ------------------------------
#
# Exactly one thing about a claim is checkable at all, and it is checked rather
# than trusted: a wrong `Cheese-Task:` is permanent, and it reads exactly like a
# right one.


def test_work_from_another_room_cannot_be_signed_onto_this_change(client):
    """An id pasted out of another room's brief would otherwise credit that
    room's worker for a change they never saw."""
    pid = _project(client)
    room = _room(client, pid)
    elsewhere = _room(client, pid)
    theirs = _dispatch(client, elsewhere, "别的房间的活")

    r = _file_card(client, room, "feat: claim someone else's work", [theirs])
    assert r.status_code == 404, r.text


def test_git_itself_parses_the_trailers_on_the_commit_that_landed(client):
    """不是「这行字面量在不在消息里」，是「git 认不认」。

    一个 trailer 块在空行处结束，而 `git interpret-trailers --parse` 只读最后
    一块。所以整套 trailer 之间只要有一个空行，上半截就整个消失 —— 消息里看着
    还在，`git log --format='%(trailers)'` 里没有，任何按 trailer 做的审计都查
    不到。只有带共同作者的改动才踩得到（没有共同作者就不会插那个空行），所以它
    躲过了历史上每一次交付，也躲过了每一个用「这行字面量在不在」写的断言。
    """
    pid = _project(client)
    room = _room(client, pid)
    mine = _dispatch(client, room, "写了这一批")
    _worker_starts(client, mine, "ac2c038d44616a2f2")

    landed = _batch(client, pid, room, "feat: parse me with real git", [mine])

    parsed = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=landed,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tokens = {line.split(":", 1)[0] for line in parsed.splitlines() if line.strip()}
    assert tokens == {
        "Requested-by",
        "Reviewed-by",
        "Cheese-Topic",
        "Cheese-Card",
        "Cheese-Agent",
        "Cheese-Task",
        "Co-authored-by",
    }
