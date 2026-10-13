"""A room's and a project rail's tasks as their pages read them: each task with its
board cell, who it waits on, its card, what it last said and which model it runs on.

One function for every read that hands room tasks to a page: the room's task
list (`GET /topics/{id}/tasks`) and the tasks a conversation page carries under
its messages (`GET /topics/{id}/blocks`, `tasks`). The same rows from both, so a
card under a message and the same task in the channel overview cannot disagree.
Batched: each extra is one query for all the rows, never one per task.

`project_task_rows` and `rail_tasks` moved here from `routes/projects.py` for the
same reason the room's own rows already lived here — they are a page's view of a
task, not a route's, and the topics list asks for the rail's rows too.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.chat import ChatService
from app.domain.agent.liveness import running_tasks
from app.domain.block.models import Block
from app.domain.block.queries import awaiting_an_answer
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.delivery.addressing import Event, address, hand_of
from app.domain.identity.actor import Actor
from app.domain.project.repositories import ProjectRepository
from app.domain.review.queries import latest_cards_by_task
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import binding, presentation
from app.domain.room_task.models import Task
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import TaskService
from app.domain.usage.repositories import UsageRepository


async def task_rows(
    db: AsyncSession,
    chat: ChatService,
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
    choices = binding.catalog(project.settings if project else None)
    running = await running_tasks(chat, db, [t for t, _ in threads])
    # 每件任务最后说的一句：频道概览上的「最新进展」。
    said = await BlockRepository(db).last_messages(thread_ids)
    now = datetime.now(UTC)
    items = []
    for task, blocks in threads:
        card = cards.get(task.id)
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                card,
                running=task.id in running,
                awaiting_answer=task.id in asked,
            ),
            now=now,
        )
        waiting = presentation.waiting_on(
            shown,
            running=task.id in running,
            owner=task.owner_handle,
            reviewer=card.reviewer_handle if card is not None else None,
            asked=asked.get(task.id),
        )
        accepted = landed.get(task.id, [])
        items.append(
            {
                **TaskOut.model_validate(task).model_dump(mode="json"),
                # 用哪个模型。花过就是它真花的那个，没花过就是它绑的那个。
                "model": presentation.card_model(
                    task, spent=spent.get(task.id), choices=choices
                ),
                # 同一个函数算的那一格，和项目级列表、和这条活自己的头一模一样。
                "presentation": shown.as_dict(),
                # 在等谁：「待 某某 审阅」；是看的人自己就写「待你审阅」。
                "waiting_on": waiting,
                "awaits_me": waiting is not None and waiting == actor.handle,
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
    chat: ChatService,
    actor: Actor,
    *,
    room_id: uuid.UUID,
    project_id: uuid.UUID,
    blocks: Sequence[Block],
) -> dict[uuid.UUID, list[dict]]:
    """The tasks each of ``blocks`` carries, as rows: the tasks made from a
    message, under that message, and the task a room-line row says began, on
    that row. Blocks that carry none are left out."""
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
            await task_rows(db, chat, actor, project_id, threads),
            strict=True,
        )
    )
    carried: dict[uuid.UUID, list[dict]] = {}
    for task, _ in threads:
        if task.upgraded_from_block_id is not None:
            carried.setdefault(task.upgraded_from_block_id, []).append(rows[task.id])
    for block_id, task_id in named.items():
        if task_id in rows:
            carried.setdefault(block_id, []).append(rows[task_id])
    return carried


async def project_task_rows(
    db: AsyncSession, chat: ChatService, me: str, tasks: Sequence[Task]
) -> list[dict]:
    """The project rail's rows for ``tasks``: each with its board cell, its
    card, whether it waits on ``me``, whether it stalled, when it last moved."""
    task_ids = [t.id for t in tasks]
    cards = await latest_cards_by_task(db, task_ids)
    # 哪几条停在一个未回答的提问上 —— 第三次批查询，走只收提问那几行的部分索引
    # （`ix_blocks_questions`）。这是唯一会中断「运行中」的一格，所以不能留
    # 给调用方各自去问。
    asked = await awaiting_an_answer(db, task_ids)
    # 一次，给全部行用同一个「现在几点」：逐行取 now 会让同一批数据里两条本该
    # 一样的活分到不同格子，而那种差别没人再能复现。
    now = datetime.now(UTC)
    # 第四次批查询：哪几条此刻有一轮在跑（`agent.liveness`：不止看内存）。
    running = await running_tasks(chat, db, tasks)
    # 第五次：每条最后一次有人或芝士说话是什么时候 —— 侧栏按它排「最近有动静」。
    last_said = await TaskService(db).last_block_at_for_tasks(task_ids)
    items = []
    for task in tasks:
        card = cards.get(task.id)
        said_at = last_said.get(task.id)
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                card,
                running=task.id in running,
                awaiting_answer=task.id in asked,
            ),
            now=now,
        )
        last_activity = said_at or task.created_at
        is_running = task.id in running
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
                asked=asking,
                # 题上没记着谁（平台发起的轮次）：等它的是这条活的人 —— 和通知、
                # 待办同一个名单（`announce.notify_question`）。题根本不在等，就
                # 没有这一层。
                asked_also=(task.people if task.id in asked and asking is None else ()),
                owner=(
                    task.owner_handle
                    if presentation.owner_acts_on(shown, running=is_running)
                    else None
                ),
            ),
            hand_of(shown.column),
        ).reason_for(me)
        items.append(
            {
                **TaskOut.model_validate(task).model_dump(mode="json"),
                "presentation": shown.as_dict(),
                "awaits_me": awaits is not None,
                "stalled": presentation.is_stalled(
                    shown, running=is_running, last_activity=last_activity, now=now
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


#: How many of my tasks a channel's row in the sidebar carries under it.
RAIL_TASKS = 5


async def rail_tasks(
    db: AsyncSession,
    chat: ChatService,
    actor: Actor,
    room_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict]:
    """What the sidebar hangs under each channel, keyed by channel: how many
    tasks are underway there (`open`, the count after 「全部任务」), and the ones
    of them the caller owns or helps on (`shown`) — owned first, then helped,
    each most recently moved first, at most `RAIL_TASKS`, leaving out one that
    stalled. Every channel in ``room_ids`` gets an entry; the rows are the
    project rail's (`project_task_rows`).

    The project's open tasks are not read for this: the count is one grouped
    query, and only the caller's own tasks are made into rows."""
    me = actor.handle or ""
    service = TaskService(db)
    counts = await service.underway_counts(room_ids)
    mine = await service.underway_with(room_ids, me) if actor.authenticated else []
    rows = await project_task_rows(db, chat, me, mine)
    shown: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda row: row["last_activity_at"], reverse=True):
        if not row["stalled"]:
            shown.setdefault(row["room_id"], []).append(row)
    out: dict[uuid.UUID, dict] = {}
    for room in room_ids:
        here = shown.get(str(room), [])
        owned = [row for row in here if row["owner_handle"] == me]
        helped = [row for row in here if row["owner_handle"] != me]
        out[room] = {
            "shown": (owned + helped)[:RAIL_TASKS],
            "open": counts.get(room, 0),
        }
    return out
