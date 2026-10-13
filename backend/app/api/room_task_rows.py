"""A room's tasks as its pages read them: each task with its board cell, who it
waits on, its card, what it last said and which model it runs on.

One function for every read that hands room tasks to a page: the room's task
list (`GET /topics/{id}/tasks`) and the tasks a conversation page carries under
its messages (`GET /topics/{id}/blocks`, `tasks`). The same rows from both, so a
card under a message and the same task in the channel overview cannot disagree.
Batched: each extra is one query for all the rows, never one per task.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.chat import ChatService
from app.domain.agent.liveness import running_tasks
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.identity.actor import Actor
from app.domain.project.repositories import ProjectRepository
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
