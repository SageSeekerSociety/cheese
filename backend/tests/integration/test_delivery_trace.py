"""#189: `git log` can name the 芝士 that wrote a change, and the work it was.

The commit already said who ASKED (`Requested-by`) and who approved
(`Reviewed-by`), and Claude Code signs every commit it writes for anybody
anywhere with `Co-authored-by: Claude Fable 5`. So a reader of the project's
history could learn what model typed the change and nothing at all about which
instance of this platform ran it, or which piece of work inside that instance it
came from — which is what a bad delivery has to be traced back to.

Who did it is DECLARED at 递卡 and never inferred. Everything a machine could
infer — the delivering tree's members, "every task no earlier card claimed" —
establishes that a task row exists, not that its code is in this diff, and the
difference is not academic: measured on this project (2026-09-08), inferring it
from the tree would have signed one PR with three tasks that contributed nothing
to it while naming the task that actually wrote it on the previous PR. So these
go through a real accept on an unbound project, and read the commit that
actually landed on `main` rather than a string a builder returned.
"""

import itertools
import subprocess
import uuid

from tests.integration.conftest import session_auth_headers
from tests.machine_work import machine_commits


def _task_line(pid: str, room: str, task: str, subagent: str, title: str) -> str:
    """`Cheese-Task:` 一条活写一行：**打得开的地址**、分身、标题。

    地址是房间页面加 `?card=<task_id>` —— 房间页读这个查询串，把总览那一格下钻到
    它点名的那条活。活的 id 仍在地址里，`card=` 后面那一段就是。
    """
    from app.core.config import settings

    where = f"{settings.frontend_url.rstrip('/')}/projects/{pid}/topics/{room}"
    return f"Cheese-Task: {where}?card={task} {subagent} {title}"


def _project(client) -> str:
    return client.post("/projects", json={"name": "P"}).json()["data"]["id"]


def _room(client, project_id: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": "做一个东西", "created_by": "alice"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _dispatch(client, room_id: str, title: str) -> str:
    """One piece of work in the room, through the only door that makes one."""
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _bind(client, room_id: str, task_id: str, agent_id: str) -> None:
    """认领: the room reports which worker in its session took the work — the id
    Claude Code minted inside the container, which is the whole of what says
    WHICH machine did this."""
    r = client.post(
        f"/topics/{room_id}/tasks/{task_id}/bind",
        json={"agent_id": agent_id},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _file_card(client, room_id: str, subject: str, tasks: list[str] | None = None):
    return client.post(
        f"/topics/{room_id}/accept-card",
        json={
            "change_subject": subject,
            "change_body": "Who wrote this, on the record.",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
            "task_ids": tasks or [],
        },
    )


def _deliver(
    client, room_id: str, subject: str, tasks: list[str] | None = None
) -> dict:
    r = _file_card(client, room_id, subject, tasks)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _accept(client, card_id: str) -> None:
    r = client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _landed_body(project_id: str, ref: str = "main") -> str:
    from app.domain.workspace import service as ws

    return subprocess.run(
        ["git", "log", "-1", "--format=%B", ref],
        cwd=ws.ensure_repo(uuid.UUID(project_id)),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


_written = itertools.count()


def _batch(client, pid: str, room: str, subject: str, tasks: list[str]) -> str:
    """Write something, deliver it naming *tasks*, and return what landed.

    A fresh file every time, because a room delivering twice has to have
    something new on its branch the second time — that is what "还有东西可以交付"
    means, and re-writing the same bytes is a no-op git refuses to commit.
    """
    name = f"work-{next(_written)}.txt"
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {name: subject})
    card = _deliver(client, room, subject, tasks)
    _accept(client, card["id"])
    landed = _landed_body(pid)
    # The commit THIS delivery produced, not whatever was on main already: a
    # second batch that quietly merged nothing would otherwise be checked
    # against the first one's trailers and pass for the wrong reason.
    assert landed.splitlines()[0] == subject, landed
    return landed


def test_the_landed_commit_names_the_agent_and_every_worker_declared(client):
    pid = _project(client)
    room = _room(client, pid)
    mine = _dispatch(client, room, "补 trailer")
    theirs = _dispatch(client, room, "顺手修 flaky 测试")
    _bind(client, room, mine, "ac2c038d44616a2f2")
    _bind(client, room, theirs, "9f1b7c22e0d341a80")
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {"a.txt": "one\n"})

    card = _deliver(client, room, "feat: deliver what two workers made", [mine, theirs])
    _accept(client, card["id"])

    body = _landed_body(pid)
    assert f"Cheese-Agent: cheese-{uuid.UUID(room).hex[:12]}" in body
    assert _task_line(pid, room, mine, "ac2c038d44616a2f2", "补 trailer") in body
    assert (
        _task_line(pid, room, theirs, "9f1b7c22e0d341a80", "顺手修 flaky 测试") in body
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


def _next_batch(client, pid: str, room: str) -> None:
    """The batch that just went out is closed and the room starts the next one.

    What an accept on a GitHub-bound project does to the tree it merged
    (`AcceptService._mark_cards_tree_merged`), reached here directly because the
    unbound lane these tests run on has no PR to merge. The state it leaves is
    the ordinary one for every room that has delivered and kept working: the
    task rows sit on a tree that is finished, and the next batch of code is
    written somewhere else entirely.
    """
    from app.domain.room_task.services import WorkTreeService

    async def _roll() -> None:
        async with client.test_factory() as s:
            trees = WorkTreeService(s)
            landed = await trees.current(uuid.UUID(room))
            assert landed is not None
            await trees.mark_merged(landed)
            fresh = await trees.ensure_open(
                project_id=uuid.UUID(pid), room_id=uuid.UUID(room)
            )
            assert fresh.id != landed.id
            await s.commit()

    client.portal.call(_roll)


def _two_batches(client) -> tuple[str, str, str, str]:
    """A room that delivers twice: `earlier`'s code goes out in the first batch,
    `later`'s in the second. Both are dispatched up front, so both hang on the
    FIRST tree — the situation every long-running room is in."""
    pid = _project(client)
    room = _room(client, pid)
    earlier = _dispatch(client, room, "上一批写完的活")
    later = _dispatch(client, room, "代码走下一批的活")
    _bind(client, room, earlier, "aaaa0000aaaa0000a")
    _bind(client, room, later, "bbbb1111bbbb1111b")
    return pid, room, earlier, later


def test_work_delivered_in_a_later_batch_is_named_on_that_batch(client):
    """The task rides a tree that finished batches ago; its code is in THIS one.
    Enumerating the delivering tree misses it entirely — its tree is not this
    tree — and the 分身 that wrote the change vanishes from the history."""
    pid, room, earlier, later = _two_batches(client)

    _batch(client, pid, room, "feat: the first batch", [earlier])
    _next_batch(client, pid, room)
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
    _bind(client, room, real, "cccc2222cccc2222c")

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
    _bind(client, room, done, "dddd3333dddd3333d")
    _bind(client, room, running, "eeee4444eeee4444e")

    now = _batch(client, pid, room, "feat: land only the finished half", [done])
    assert _task_line(pid, room, done, "dddd3333dddd3333d", "这批做完的活") in now
    assert running not in now

    later = _batch(client, pid, room, "feat: land the other half", [running])
    assert _task_line(pid, room, running, "eeee4444eeee4444e", "还在跑的活") in later


def test_an_undeclared_delivery_lands_with_no_task_trailer_at_all(client):
    """诚实的空白, and the one rule with no exception: nothing falls back to the
    tree for a card that named nobody. A wrong `Cheese-Task:` is permanent and
    reads exactly like a right one, so an audit acts on it — a missing one only
    says nobody claimed the work."""
    pid = _project(client)
    room = _room(client, pid)
    _bind(client, room, _dispatch(client, room, "有人干了但没报"), "ffff5555ffff5555f")

    body = _batch(client, pid, room, "feat: land without naming anybody", [])

    assert body.splitlines()[0] == "feat: land without naming anybody"
    assert "Cheese-Task" not in body
    assert f"Cheese-Agent: cheese-{uuid.UUID(room).hex[:12]}" in body


def test_work_no_worker_ever_took_still_appears_when_it_is_declared(client):
    """`subagent_id` is NULL until a worker is bound, and a room can write a
    change itself. Dropping the row would make the batch in the commit smaller
    than the batch the room said it delivered."""
    pid = _project(client)
    room = _room(client, pid)
    unclaimed = _dispatch(client, room, "没人认领的活")

    body = _batch(client, pid, room, "feat: deliver work nobody claimed", [unclaimed])

    assert _task_line(pid, room, unclaimed, "-", "没人认领的活") in body


def test_a_delivery_with_no_work_at_all_still_lands(client):
    """The trailers are a nicety; a room that dispatched nothing must merge
    exactly as it did before they existed."""
    pid = _project(client)
    room = _room(client, pid)

    body = _batch(client, pid, room, "feat: deliver without a task row", [])

    assert body.splitlines()[0] == "feat: deliver without a task row"
    assert "Cheese-Task" not in body
    assert f"Cheese-Agent: cheese-{uuid.UUID(room).hex[:12]}" in body


def test_unreadable_work_costs_the_trailers_and_not_the_merge(client, monkeypatch):
    """Best-effort, like every other part of attribution: whatever else is wrong,
    a trailer must never be the reason a delivery fails to land."""
    from app.domain.room_task.services import TaskService

    pid = _project(client)
    room = _room(client, pid)
    mine = _dispatch(client, room, "补 trailer")
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {"a.txt": "one\n"})
    card = _deliver(client, room, "feat: land when the batch is unreadable", [mine])

    async def _blow_up(_self, _task_ids):
        raise RuntimeError("the batch could not be read")

    monkeypatch.setattr(TaskService, "list_by_ids", _blow_up)
    _accept(client, card["id"])

    body = _landed_body(pid)
    assert body.splitlines()[0] == "feat: land when the batch is unreadable"
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
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {"a.txt": "one\n"})

    r = _file_card(client, room, "feat: claim someone else's work", [theirs])
    assert r.status_code == 422, r.text
    assert theirs in r.json()["message"]


def test_work_that_keeps_going_is_named_on_every_batch_it_wrote(client):
    """交付过 ≠ 做完了：一条活可以交付过而仍然开着（`TaskStatus`），继续往同一条
    分支推，真实地写进下一批。它在两次交付的历史里各出现一次是**真实情况**，
    拒掉第二次就是拒掉一条真的贡献声明。"""
    pid = _project(client)
    room = _room(client, pid)
    kept_going = _dispatch(client, room, "两批都写了的活")
    _bind(client, room, kept_going, "aaaa0000aaaa0000a")

    first = _batch(client, pid, room, "feat: the first half", [kept_going])
    _next_batch(client, pid, room)
    second = _batch(client, pid, room, "feat: the second half", [kept_going])

    line = _task_line(pid, room, kept_going, "aaaa0000aaaa0000a", "两批都写了的活")
    assert line in first
    assert line in second


def test_the_only_way_to_add_a_claim_to_a_filed_card_is_void_and_refile(client):
    """递卡时忘了 `--task`，声明就补不上这张卡 —— `create_card` 拒绝在同一棵树上
    再递一张，而 `pending` 卡上没有任何改声明的入口。唯一走得通的补救是**人**
    把卡作废，房间再带着声明重递（芝士自己点不了作废：`_forbid_ai`）。

    CLI 的提示照着这条路写，所以这条路必须真的走得通。"""
    pid = _project(client)
    room = _room(client, pid)
    mine = _dispatch(client, room, "递卡时忘了报的活")
    _bind(client, room, mine, "cccc2222cccc2222c")
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {"c.txt": "three\n"})

    forgot = _deliver(client, room, "feat: land without naming who wrote it", [])
    # 补声明的两条想当然的路都是死路：直接重递被互斥挡住。
    assert (
        _file_card(client, room, "feat: name them after all", [mine]).status_code == 422
    )

    r = client.post(
        f"/accept-cards/{forgot['id']}/void",
        json={"note": "忘了报活，重递"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    card = _deliver(client, room, "feat: name them after all", [mine])
    _accept(client, card["id"])
    assert _task_line(
        pid, room, mine, "cccc2222cccc2222c", "递卡时忘了报的活"
    ) in _landed_body(pid)


def test_naming_the_same_work_twice_on_one_card_writes_one_trailer(client):
    """同一张卡里把一条活报两遍不是两份贡献，也不值得拒 —— 它跟报一遍说的是同
    一件事，去重即可。"""
    pid = _project(client)
    room = _room(client, pid)
    once = _dispatch(client, room, "被报了两遍的活")
    _bind(client, room, once, "bbbb1111bbbb1111b")

    body = _batch(client, pid, room, "feat: name it twice", [once, once])

    line = _task_line(pid, room, once, "bbbb1111bbbb1111b", "被报了两遍的活")
    assert body.count(line) == 1


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
    _bind(client, room, mine, "ac2c038d44616a2f2")

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
    }
