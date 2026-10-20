"""A room's and a project rail's tasks as their pages read them: each task with its
board cell, who it waits on, its card, what it last said and which model it runs on.

One function for every read that hands room tasks to a page: the room's task
list (`GET /topics/{id}/tasks`) and the tasks a conversation page carries under
its messages (`GET /topics/{id}/blocks`, `tasks`). The same rows from both, so a
card under a message and the same task in the channel overview cannot disagree.
Batched: each extra is one query for all the rows, never one per task.

`task_out` is the one-task half of that: what a route returns when it hands a
single task back to its page (`GET|PATCH /topics/{task}/task`, and the title
routes), so a deep link and the board cannot disagree either.

`project_task_rows` and `rail_tasks` moved here from `routes/projects.py`, and
`task_out` from `routes/topics_tasks.py`, for the same reason the room's own
rows already lived here — they are a page's view of a task, not a route's, and
more than one route module asks for them. `room_task_items` (the room's list of
tasks), `task_page_row` (one task's page) and `_opening` (where its first turn
is) followed them out of `routes/topics_tasks.py` later, when the room snapshot
started reading them: it is not a route, so a route module holding them made it
reach one (C4 `routes-independent`).
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.addressing import Event, address
from app.domain.agent.model_catalog import catalog
from app.domain.agent.opening import opening_state
from app.domain.agent.session_state import running_conversations
from app.domain.block.models import Block
from app.domain.block.queries import awaiting_an_answer
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.block.waits import MemberWaits
from app.domain.identity.actor import Actor
from app.domain.living_doc.services import Documents
from app.domain.project.repositories import ProjectRepository
from app.domain.review.queries import latest_cards_by_task
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.services import AcceptService
from app.domain.room_task import presentation
from app.domain.room_task.adapters import task_row_json
from app.domain.room_task.models import Task
from app.domain.room_task.presentation import hand_of
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import TaskService
from app.domain.thread.models import Thread
from app.domain.usage.repositories import UsageRepository


async def from_threads(db: AsyncSession, task_ids: Sequence[uuid.UUID]) -> set:
    """Which of these tasks are a 支线 turned into the task in place: their
    conversation hangs under a message in the main line, and the page opens it
    beside the channel, as it opens a 支线."""
    if not task_ids:
        return set()
    return set(await db.scalars(select(Thread.id).where(Thread.id.in_(list(task_ids)))))


async def task_out(db, task, card=None) -> dict:
    """The task with its board cell — what every route that hands a task back
    to its page returns, so the page never holds one without it. The same
    function the list uses, so a deep link and the board cannot disagree."""
    if card is None:
        card = (await AcceptService(db).latest_by_task([task.id])).get(task.id)
    asked = await BlockRepository(db).awaiting_an_answer([task.id])
    out = TaskOut.model_validate(task).model_dump(mode="json")
    shown = presentation.task_presentation(
        presentation.facts_for_task(task, card, awaiting_answer=task.id in asked),
        now=datetime.now(UTC),
    )
    out["presentation"] = shown.as_dict()
    # 在等谁，和频道里那张卡读的是同一个：等的是看的人，页头和卡一样写「待你审阅」。
    out["waiting_on"] = presentation.waiting_on(
        shown,
        reviewer=card.reviewer_handle if card is not None else None,
        asked=asked[task.id].only() if task.id in asked else None,
    )
    out["from_thread"] = task.id in await from_threads(db, [task.id])
    return out


async def task_rows(
    db: AsyncSession,
    actor: Actor,
    project_id: uuid.UUID,
    threads: Sequence[tuple[Task, Sequence[Block]]],
) -> list[dict]:
    """``threads`` (each task with the blocks to carry, often none) as rows."""
    # The card each thread rides on, in ONE query for the whole room (the same
    # batched loader the project rail uses). Without it "在跑 / 闲着" and "等着
    # 人验收" are indistinguishable on screen — both are quiet — and the room
    # overview would have to ask per thread to tell them apart.
    thread_ids = [t.id for t, _ in threads]
    cards = await AcceptCardRepository(db).latest_by_task(thread_ids)
    asked = await BlockRepository(db).awaiting_an_answer(thread_ids)
    # 每件任务被采纳过几次、最近那次是哪个 PR：频道里的任务卡写「已采纳 · PR #45」。
    landed: dict[uuid.UUID, list] = {}
    for accepted_card in await AcceptCardRepository(db).accepted_for_tasks(thread_ids):
        if accepted_card.task_id is not None:
            landed.setdefault(accepted_card.task_id, []).append(accepted_card)
    # 每条活最后一次花钱花在哪个模型上，一次查完 —— 卡上的模型是从这里算的，
    # `tasks` 上没有一列存它。
    spent = await UsageRepository(db).last_model_by_task(thread_ids)
    # 能用哪些模型，按项目算一次，整屏卡共用 —— 每张卡各算一次就是同一个答案
    # 构造几百遍。
    project = await ProjectRepository(db).get(project_id)
    choices = catalog(project.settings if project else None)
    # 每件任务最后说的一句：频道概览上的「最新进展」。
    said = await BlockRepository(db).last_messages(thread_ids)
    in_place = await from_threads(db, thread_ids)
    now = datetime.now(UTC)
    items = []
    for task, blocks in threads:
        card = cards.get(task.id)
        shown = presentation.task_presentation(
            presentation.facts_for_task(task, card, awaiting_answer=task.id in asked),
            now=now,
        )
        waiting = presentation.waiting_on(
            shown,
            reviewer=card.reviewer_handle if card is not None else None,
            asked=asked[task.id].only() if task.id in asked else None,
        )
        accepted = landed.get(task.id, [])
        items.append(
            {
                **task_row_json(task),
                # 用哪个模型。花过就是它真花的那个，没花过就是它绑的那个。
                "model": presentation.card_model(
                    task, spent=spent.get(task.id), choices=choices
                ),
                # 同一个函数算的那一格，和项目级列表、和这条活自己的头一模一样。
                "presentation": shown.as_dict(),
                # 在等谁：「待 某某 审阅」；是看的人自己就写「待你审阅」。
                "waiting_on": waiting,
                "awaits_me": waiting is not None and waiting == actor.handle,
                "from_thread": task.id in in_place,
                "accepted_count": len(accepted),
                "last_accepted_pr": accepted[-1].pr_number if accepted else None,
                "blocks": [
                    BlockOut.model_validate(b).model_dump(mode="json") for b in blocks
                ],
                "last_message": BlockOut.model_validate(said[task.id]).model_dump(
                    mode="json"
                )
                if task.id in said
                else None,
                "card": None
                if card is None
                else {
                    "id": str(card.id),
                    "status": str(card.status),
                    "pr_number": card.pr_number,
                    "pr_url": card.pr_url,
                },
            }
        )
    return items


#: The room-line rows that say a task began there, naming it in `meta.task_id`:
#: `task_created`, and the `split` rows written before it.
TASK_LINE_ACTIONS = ("task_created", "split")


def named_task(block: Block) -> uuid.UUID | None:
    """The task a room-line row says began there, if it is one."""
    meta = block.meta or {}
    if meta.get("action") not in TASK_LINE_ACTIONS or not meta.get("task_id"):
        return None
    try:
        return uuid.UUID(str(meta["task_id"]))
    except ValueError:
        return None


async def tasks_under_blocks(
    db: AsyncSession,
    actor: Actor,
    *,
    room_id: uuid.UUID,
    project_id: uuid.UUID,
    blocks: Sequence[Block],
) -> dict[uuid.UUID, list[dict]]:
    """The tasks each of ``blocks`` carries, as rows: the tasks made from a
    message, under that message — in the main line the message a task hangs
    under, in a 支线 the reply it was made from, except the task the 支线
    itself became — and the task a room-line row says began, on that row.
    Blocks that carry none are left out."""
    named = {block.id: task_id for block in blocks if (task_id := named_task(block))}
    if not blocks:
        return {}
    threads = await TaskService(db).threads_for_room(
        room_id,
        limit=0,
        ids=set(named.values()),
        origins=[block.id for block in blocks],
    )
    rows = dict(
        zip(
            (task.id for task, _ in threads),
            await task_rows(db, actor, project_id, threads),
            strict=True,
        )
    )
    said_in = {block.id: block.conversation_id for block in blocks}
    carried: dict[uuid.UUID, list[dict]] = {}
    for task, _ in threads:
        hung, said = task.upgraded_from_block_id, task.source_block_id
        if hung is not None and hung in said_in:
            carried.setdefault(hung, []).append(rows[task.id])
        elif said is not None and said in said_in and said_in[said] != task.id:
            carried.setdefault(said, []).append(rows[task.id])
    for block_id, task_id in named.items():
        if task_id in rows:
            carried.setdefault(block_id, []).append(rows[task_id])
    return carried


def _iso(at: datetime | None) -> str | None:
    """一个时刻的 ISO 写法，`None` 照旧 `None`。"""
    return None if at is None else at.isoformat()


async def incomplete_tasks(
    db: AsyncSession, task_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """{活: 这条活上还没恢复的失败从什么时候开始} —— 一行一件活。

    `project_task_rows` 收的是直接喂给它的活（`/projects/{id}/tasks` 那条路），一条
    活就是一行，没有频道那一层要汇总，所以这里一件一件地问 `unresolved_failures`，
    每件活一条行（`units` 里它自己就是那一段对话）。侧栏那条路（`routes/topics.py`）
    要多算频道自己那条线和它名下的活怎么合，所以它自己拼 `units`。
    """
    if not task_ids:
        return {}
    return await MemberWaits(db).unresolved_failures({t: [t] for t in task_ids})


async def project_task_rows(
    db: AsyncSession,
    me: str,
    tasks: list[Task],
    *,
    delivered_of: Callable[[uuid.UUID], bool] | None = None,
    incomplete_of: Callable[[uuid.UUID], datetime | None] | None = None,
) -> list[dict]:
    """The project rail's rows for ``tasks``: each with its board cell, its
    card, whether it waits on ``me``, whether it stalled, when it last moved,
    and — when the caller can say it — whether it holds a delivery ``me`` has
    not opened since (`delivered_of`; the sidebar's 长绿, `TopicRailTaskRow`),
    and when the earliest failure it has not recovered from began
    (`incomplete_of`; the sidebar's red light — see
    `block/waits.MemberWaits.unresolved_failures`)."""
    task_ids = [t.id for t in tasks]
    cards = await latest_cards_by_task(db, task_ids)
    # 哪几条停在一个未回答的提问上 —— 第三次批查询，走只收提问那几行的部分索引
    # （`ix_blocks_questions`）。它压过队友报的「在做」，所以不能留给调用方
    # 各自去问。
    asked = await awaiting_an_answer(db, task_ids)
    # 一次，给全部行用同一个「现在几点」：逐行取 now 会让同一批数据里两条本该
    # 一样的活分到不同格子，而那种差别没人再能复现。
    now = datetime.now(UTC)
    # 第四次：每条最后一次有人或芝士说话是什么时候 —— 侧栏按它排「最近有动静」。
    last_said = await TaskService(db).last_block_at_for_tasks(task_ids)
    items = []
    for task in tasks:
        card = cards.get(task.id)
        said_at = last_said.get(task.id)
        shown = presentation.task_presentation(
            presentation.facts_for_task(task, card, awaiting_answer=task.id in asked),
            now=now,
        )
        last_activity = said_at or task.created_at
        # 这一条在不在等**看的这个人** —— 和「待办」同一个寻址（`address`），侧栏的
        # 点和任务列表的橙字都读它，不各自从列再推一遍。
        asking = asked.get(task.id)
        awaits = address(
            Event(
                reviewers=(
                    ()
                    if card is None or not card.reviewer_handle
                    else (card.reviewer_handle,)
                ),
                reporter=task.reporter_handle,
                # 题上没点名（平台发起的轮次）：等它的是这条活的人 —— 和通知、待办
                # 同一个名单（`announce.notify_question`）。题根本不在等，就没有这
                # 一层。
                asked_also=() if asking is None else asking.waiting(task.people),
                owner=(
                    task.owner_handle if presentation.owner_acts_on(shown) else None
                ),
            ),
            hand_of(shown.column),
        ).reason_for(me)
        items.append(
            {
                **task_row_json(task),
                "presentation": shown.as_dict(),
                "awaits_me": awaits is not None,
                # 这条活上还没恢复的失败从什么时候开始（`incomplete_of`；`None` = 没有
                # 未恢复的失败，或调用方没算）。判据在
                # `block/waits.MemberWaits.unresolved_failures`：一条活停下来报错、
                # 五分钟没人接手，侧栏画红灯。
                "incomplete_since": (
                    None if incomplete_of is None else _iso(incomplete_of(task.id))
                ),
                # 这件活交付完了、而这个人从那以后没点开看过（侧栏的长绿）。和频道
                # 那一头同一个来源（`room_task/repositories.unseen_deliveries`）。
                # 说不了（调用方没算）就是 false——不亮，不是「亮着但说不清」。
                "unseen_delivery": bool(delivered_of(task.id))
                if delivered_of is not None
                else False,
                "stalled": presentation.is_stalled(
                    shown, last_activity=last_activity, now=now
                ),
                "last_activity_at": last_activity.isoformat(),
                "card": None
                if card is None
                else {
                    "id": str(card.id),
                    "status": str(card.status),
                    "pr_number": card.pr_number,
                    "pr_url": card.pr_url,
                },
            }
        )
    return items


#: How few of the caller's own tasks a channel's row in the sidebar may carry
#: under it: a channel with fewer borrows the channel's other underway tasks to
#: reach this many (caisongyang, 2026-10-12). There is no ceiling on its own.
RAIL_TASKS_FLOOR = 3


async def rail_tasks(
    db: AsyncSession,
    actor: Actor,
    room_ids: list[uuid.UUID],
    *,
    delivered_of: Callable[[uuid.UUID], bool] | None = None,
    incomplete_of: Callable[[uuid.UUID], datetime | None] | None = None,
) -> dict[uuid.UUID, dict]:
    """What the sidebar hangs under each channel, keyed by channel: how many
    tasks are underway there (`open`, the count after 「全部任务」), and the ones
    of them the caller owns or helps on (`shown`) — owned first, then helped,
    each most recently moved first, all of them rather than a fixed few, leaving
    out the stalled. A channel where those come to fewer than
    `RAIL_TASKS_FLOOR` borrows the channel's other underway tasks, most recently
    moved first, until it reaches that many; a borrowed row carries `filling_in`.
    Every channel in ``room_ids`` gets an entry; the rows are the project rail's
    (`project_task_rows`).

    A channel with no task of the caller's at all is still brought up to the
    floor, so the rail shows every channel with something going on rather than
    only the ones the caller is in.

    The project's open tasks are still not read for this: the counts are one
    grouped query, and only the caller's own tasks and the borrowed few become
    rows."""
    me = actor.handle or ""
    service = TaskService(db)
    counts = await service.underway_counts(room_ids)
    mine = await service.underway_with(room_ids, me) if actor.authenticated else []
    candidates: list[Task] = []
    if actor.authenticated:
        # 一次把「我的」和每个房间最多补的 `RAIL_TASKS_FLOOR` 条候选一起送进
        # `project_task_rows`：原先分两批调，同一张页上验收卡、在跑轮次、提问和最后
        # 发言被各问了两遍。
        per_room: dict[uuid.UUID, int] = {}
        for task in await service.underway_others(room_ids, me):
            taken = per_room.get(task.room_id, 0)
            if taken < RAIL_TASKS_FLOOR:
                per_room[task.room_id] = taken + 1
                candidates.append(task)
    all_rows = await project_task_rows(
        db,
        me,
        [*mine, *candidates],
        delivered_of=delivered_of,
        incomplete_of=incomplete_of,
    )
    rows = all_rows[: len(mine)]
    candidate_rows = all_rows[len(mine) :]
    shown: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda row: row["last_activity_at"], reverse=True):
        if not row["stalled"]:
            shown.setdefault(row["room_id"], []).append(row)
    out: dict[uuid.UUID, dict] = {}
    short: dict[uuid.UUID, int] = {}
    for room in room_ids:
        here = shown.get(str(room), [])
        owned = [row for row in here if row["owner_handle"] == me]
        helped = [row for row in here if row["owner_handle"] != me]
        out[room] = {"shown": owned + helped, "open": counts.get(room, 0)}
        short[room] = max(0, RAIL_TASKS_FLOOR - len(owned) - len(helped))
    for task, row in zip(candidates, candidate_rows, strict=True):
        if short.get(task.room_id, 0) > 0:
            short[task.room_id] -= 1
            if not row["stalled"]:
                out[uuid.UUID(row["room_id"])]["shown"].append(
                    {**row, "filling_in": True}
                )
    return out


async def room_task_items(
    db: AsyncSession,
    actor: Actor,
    topic,
    *,
    limit: int | None = None,
    status: Literal["open", "closed"] | None = None,
    ids: list[uuid.UUID] | None = None,
    blocks: list[uuid.UUID] | None = None,
    branch: bool = False,
    latest: int | None = None,
) -> list[dict]:
    """A room's tasks as `GET /topics/{id}/tasks` lists them (see there for what
    each option keeps); also the `tasks` of the room snapshot a subscription
    opens with (`room_snapshot`). The caller has authorised the reader."""
    named: set[uuid.UUID] | None = set(ids) if ids is not None else None
    if blocks is not None:
        lines = await BlockRepository(db).many(blocks)
        named = (named or set()) | {
            task_id for line in lines if (task_id := named_task(line)) is not None
        }
    threads = await TaskService(db).threads_for_room(
        topic.id,
        limit=limit,
        status=status,
        ids=named,
        origins=blocks,
        with_branch=branch,
        latest=latest,
    )
    return await task_rows(db, actor, topic.project_id, threads)


async def with_session_running(db: AsyncSession, items: list[dict]) -> list[dict]:
    """Say on each task row whether a session of its AI teammate is running in
    it right now (`session_running`), as the session protocol declares it.

    For readers that act on whether the teammate is at work — the orchestrator's
    patrol, which must not nudge a session in the middle of a long command. The
    board does not read it: what a task shows is what its teammate reported
    (`presentation`).
    """
    running = await running_conversations(db, [uuid.UUID(item["id"]) for item in items])
    return [
        {**item, "session_running": uuid.UUID(item["id"]) in running} for item in items
    ]


async def task_page_row(db: AsyncSession, place, task) -> dict:
    """A task as `GET /topics/{task}/task` reads it, and the `task` of the room
    snapshot a subscription to it opens with. The caller has authorised the
    reader."""
    cards = await AcceptService(db).latest_by_task([task.id])
    out = await task_out(db, task, cards.get(task.id))
    # 用哪个模型：花过就是它真花的那个（`usage` 里这条活最后一行），一分钱没花过
    # 就是它绑的那个。和列表里显示的是同一个函数算的。
    project = await ProjectRepository(db).get(place.project_id)
    out["model"] = presentation.card_model(
        task,
        spent=(await UsageRepository(db).last_model_by_task([task.id])).get(task.id),
        choices=catalog(project.settings if project else None),
    )
    card = cards.get(task.id)
    out["card"] = (
        None
        if card is None
        else {
            "id": str(card.id),
            "status": str(card.status),
            "pr_number": card.pr_number,
            "pr_url": card.pr_url,
        }
    )
    out["opening"] = await _opening(db, task)
    return out


async def _opening(db, task) -> str | None:
    """Where the task's first turn is, while its document is still empty
    (`agent/opening.py`); None once the document says something."""
    if task.document_id is not None:
        doc = await Documents(db).get(task.document_id)
        if doc is not None and doc.content.strip():
            return None
    return await opening_state(db, task.id)
