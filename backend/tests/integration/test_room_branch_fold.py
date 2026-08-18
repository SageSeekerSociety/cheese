"""采信 = 子话题的提交进母话题那一个 PR，从话题这一侧看。

`test_room_branch_merge.py` covers the git primitive; this covers what 采信 does
with it — including the two places it must REFUSE and queue instead:

- the room's accept card is `pr_open`: the poller pushes that branch every 60s
  and any move restarts a CI queue measured in hours, so a merge in that window
  waits;
- a conflict: reported in the room, loudly, rather than swallowed.

Everything is asserted through what a person in the room can see — the branch
contents and the room's timeline — never through internals.
"""

import asyncio
import subprocess
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.conclusion.repositories import ConclusionCardRepository
from app.domain.conclusion.services import ConclusionCardService
from app.domain.review.models import AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws
from tests.conftest import wait_work_idle as _wait_work_idle

# --- harness ---------------------------------------------------------------


def _project(client) -> dict:
    return client.post("/projects", json={"name": "P"}).json()["data"]


def _room(client, project_id: str, title: str = "房间") -> dict:
    return client.post(
        "/topics", json={"project_id": project_id, "title": title}
    ).json()["data"]


def _split(client, room_id: str, title: str) -> dict:
    sub = client.post(f"/topics/{room_id}/split", json={"title": title}).json()["data"]
    _wait_work_idle()  # the 分身's kickoff turn must finish first
    return sub


def _native_edit(pid: str, topic_id: str, path: str, content: str) -> None:
    """A turn's worth of work: native tools write, the platform snapshots."""
    project, topic = uuid.UUID(pid), uuid.UUID(topic_id)
    wt = ws.topic_worktree(project, topic)
    (wt / path).write_text(content, encoding="utf-8")
    ws.snapshot_worktree(project, topic)


def _branch_files(pid: str, topic_id: str) -> set[str]:
    repo = ws.ensure_repo(uuid.UUID(pid))
    out = subprocess.run(
        [
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            ws.branch_for_topic(uuid.UUID(topic_id)),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return set(out.stdout.split())


def _file_conclusion(client, sub_id: str, conclusion: str = "做完了") -> str:
    """开一张结论卡但不唤醒母话题 —— 这些测试要自己决定什么时候结算。"""

    async def _run() -> str:
        async with client.test_factory() as session:
            _, card = await TopicService(session).return_conclusion(
                subtopic_id=uuid.UUID(sub_id), conclusion=conclusion
            )
            assert card is not None
            out = str(card.id)
            await session.commit()
            return out

    return asyncio.run(_run())


def _settle_by_turn_end(client, room_id: str) -> None:
    """机制①：母话题那一轮结束，还开着的结论卡默认采信。"""

    async def _run() -> None:
        async with client.test_factory() as session:
            await ConclusionCardService(session).settle_open_for_turn(
                receiver_topic_id=uuid.UUID(room_id),
                turn_started_at=datetime.now(UTC) + timedelta(seconds=1),
            )
            await session.commit()

    asyncio.run(_run())


def _sweep(client) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            await ConclusionCardService(session).sweep_room_merges()
            await session.commit()

    asyncio.run(_run())


def _file_accept_card(client, topic_id: str) -> str:
    r = client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "reviewer_handle": "alice",
            "routing_reason": "最懂这块",
            "change_subject": "feat(room): ship what the room built",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _force_card_status(client, card_id: str, status: AcceptStatus) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(uuid.UUID(card_id))
            assert card is not None
            card.status = status
            await session.commit()

    asyncio.run(_run())


def _room_text(client, room_id: str) -> str:
    blocks = client.get(f"/topics/{room_id}/blocks").json()["data"]["data"]
    return "\n".join(b["content"] or "" for b in blocks)


def _card_status(client, sub_id: str) -> str:
    async def _run() -> str:
        async with client.test_factory() as session:
            cards = await ConclusionCardRepository(session).list_for_topic(
                uuid.UUID(sub_id)
            )
            return cards[0].status

    return asyncio.run(_run())


# --- 分支从母话题长出来 -----------------------------------------------------


def test_a_split_task_starts_from_what_the_room_already_built(client):
    p = _project(client)
    room = _room(client, p["id"])
    _native_edit(p["id"], room["id"], "room.txt", "built by the room\n")

    task = _split(client, room["id"], "接着做")

    wt = ws.topic_worktree(uuid.UUID(p["id"]), uuid.UUID(task["id"]))
    assert (wt / "room.txt").read_text() == "built by the room\n"


# --- 采信 → 提交进母话题的分支 -----------------------------------------------


def test_accepting_a_conclusion_folds_the_commits_into_the_room(client):
    p = _project(client)
    room = _room(client, p["id"])
    _native_edit(p["id"], room["id"], "room.txt", "room\n")
    task = _split(client, room["id"], "写个功能")
    _native_edit(p["id"], task["id"], "feature.py", "def go(): ...\n")

    _file_conclusion(client, task["id"], "功能写好了")
    _settle_by_turn_end(client, room["id"])

    assert "feature.py" in _branch_files(p["id"], room["id"])
    assert "并入本房间的分支" in _room_text(client, room["id"])


def test_a_rooms_own_conclusion_does_not_fold_into_the_root_topic(client):
    """房间照旧靠采纳并进 main —— 把它并进根话题是没有意义的。"""
    p = _project(client)
    room = _room(client, p["id"])
    _native_edit(p["id"], room["id"], "room.txt", "room work\n")
    root_id = p["root_topic_id"]

    _file_conclusion(client, room["id"], "房间这一阶段做完了")
    _settle_by_turn_end(client, root_id)

    assert "room.txt" not in _branch_files(p["id"], root_id)


# --- 风险三：母话题在等 CI 时必须排队 ----------------------------------------


def test_a_room_waiting_on_ci_queues_the_merge_instead_of_restarting_it(client):
    p = _project(client)
    room = _room(client, p["id"])
    _native_edit(p["id"], room["id"], "room.txt", "room\n")
    task = _split(client, room["id"], "并行的一件活")
    _native_edit(p["id"], task["id"], "extra.py", "x = 1\n")

    # The room's card is riding a PR: every push restarts its CI queue.
    room_card = _file_accept_card(client, room["id"])
    _force_card_status(client, room_card, AcceptStatus.pr_open)

    _file_conclusion(client, task["id"], "做完了")
    _settle_by_turn_end(client, room["id"])

    # 采信 still happened — the queue must not hold the conclusion hostage.
    assert _card_status(client, task["id"]) == "accepted"
    assert "extra.py" not in _branch_files(p["id"], room["id"])
    assert "先排队" in _room_text(client, room["id"])

    # The PR settles; the next sweep is the exit from the queue.
    _force_card_status(client, room_card, AcceptStatus.accepted)
    _sweep(client)
    assert "extra.py" in _branch_files(p["id"], room["id"])
    assert "并入本房间的分支" in _room_text(client, room["id"])


# --- 风险一：冲突必须报出来 --------------------------------------------------


def test_two_tasks_touching_one_file_report_the_conflict_in_the_room(client):
    p = _project(client)
    room = _room(client, p["id"])
    _native_edit(p["id"], room["id"], "shared.py", "value = 0\n")

    first = _split(client, room["id"], "第一件活")
    second = _split(client, room["id"], "第二件活")
    _native_edit(p["id"], first["id"], "shared.py", "value = 1\n")
    _native_edit(p["id"], second["id"], "shared.py", "value = 2\n")

    _file_conclusion(client, first["id"], "改成 1")
    _file_conclusion(client, second["id"], "改成 2")
    _settle_by_turn_end(client, room["id"])

    text = _room_text(client, room["id"])
    assert "冲突" in text
    assert "shared.py" in text
    # Both conclusions are still 采信'd: a merge conflict is not a verdict on the
    # conclusion, and blocking 采信 on git would break 默认采信.
    assert _card_status(client, first["id"]) == "accepted"
    assert _card_status(client, second["id"]) == "accepted"


# --- 风险二：母话题有人在改时不能扫掉 ----------------------------------------


def test_an_edit_in_progress_in_the_room_holds_the_merge_back(client):
    p = _project(client)
    room = _room(client, p["id"])
    _native_edit(p["id"], room["id"], "room.txt", "room\n")
    task = _split(client, room["id"], "一件活")
    _native_edit(p["id"], task["id"], "landed.py", "y = 2\n")

    room_wt = ws.topic_worktree(uuid.UUID(p["id"]), uuid.UUID(room["id"]))
    (room_wt / "room.txt").write_text("someone is typing right now\n", encoding="utf-8")

    _file_conclusion(client, task["id"], "做完了")
    _settle_by_turn_end(client, room["id"])

    assert (room_wt / "room.txt").read_text() == "someone is typing right now\n"
    assert "landed.py" not in _branch_files(p["id"], room["id"])
    assert "先排队" in _room_text(client, room["id"])

    # The edit gets committed (a turn ends, or the human's next snapshot) and the
    # queued merge goes through on its own.
    ws.snapshot_worktree(uuid.UUID(p["id"]), uuid.UUID(room["id"]))
    _sweep(client)
    assert "landed.py" in _branch_files(p["id"], room["id"])
