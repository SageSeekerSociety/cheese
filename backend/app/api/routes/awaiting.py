"""待我处理：跨项目的那一份清单。

看板回答「这个项目现在有什么在等人」，这里回答「**我**现在要处理什么」—— 同一份规则
（`room_task/presentation.py`）、同一份收件人判据（`room_task/awaiting.py`），范围换
成我能看见的全部项目。为什么它不能由通知表拼出来，写在 `room_task/awaiting.py` 的模
块说明里。

查询留在这一层，不进领域：这一份要问项目、房间、活、验收卡、消息块、轮次六个地方，
而项目看板那两份（`projects.list_project_tasks` / `topics.list_topics`）也是在路由里
把批查询拼起来的 —— 同一个形状，同一个理由。
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service
from app.api.response import ok, page
from app.core.db import get_db
from app.domain.agent.chat import ChatService
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.repositories import BlockRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.review import archive
from app.domain.review.models import AcceptCard
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import awaiting, presentation
from app.domain.room_task.repositories import TaskRepository
from app.domain.topic.repositories import TopicRepository

router = APIRouter(prefix="/awaiting-me", tags=["awaiting"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _room_cards(
    db: AsyncSession, topic_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AcceptCard]:
    """每个房间**自己**那张还没结算的验收卡。

    `task_id is None` 才是房间自己的：一条活递的卡把房间记在 `topic_id` 上，不过滤
    的话，一条活在等验收会让它上面那个房间也显示成等验收。哪些状态算「还没结算」
    不在这里数 —— 那张表是 `review/archive.py` 维护的。
    """
    if not topic_ids:
        return {}
    cards = await AcceptCardRepository(db).list_live_for_places(
        topic_ids, statuses=archive.OPEN_CARD_STATUSES
    )
    # 按 created_at 升序回来，所以同一个房间后写的覆盖先写的 = 留下最新那张。
    return {c.topic_id: c for c in cards if c.task_id is None}


@router.get("")
async def list_awaiting_me(
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """我现在要处理的事项，最近动过的在前。

    访客拿到空列表，不是错误：这个清单的定义就是「点到了我的那些」，而没有身份的调
    用者没有被任何一件事点到。
    """
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated or not who.handle:
        return ok(page([], 0))
    handle = who.handle

    projects = await ProjectRepository(db).list_visible_to(
        handle=handle, user_id=who.user_id
    )
    if not projects:
        return ok(page([], 0))
    names = {p.id: p.name for p in projects}
    project_ids = list(names)

    tasks = await TaskRepository(db).list_for_projects(project_ids)
    topics = await TopicRepository(db).list_for_projects(project_ids)
    titles = {t.id: t.title for t in topics}
    task_ids = [t.id for t in tasks]
    topic_ids = [t.id for t in topics]

    blocks = BlockRepository(db)
    task_cards = await AcceptCardRepository(db).latest_by_task(task_ids)
    room_cards = await _room_cards(db, topic_ids)
    beats = await TaskRepository(db).last_block_at_for_tasks(task_ids)
    asked_tasks = await blocks.tasks_awaiting_an_answer(task_ids)
    asked_rooms = await blocks.rooms_awaiting_an_answer(topic_ids)
    # 一个待确认问题只有**发起那一轮的人**能回答，所以「它在等谁」由轮次的作者决定。
    # 只问那些真的停在提问上的房间 —— 其余的房间问了也用不上。
    waiting_for = await AgentTurnRepository(db).open_turn_authors_for_topics(
        list(
            asked_rooms | {t.room_id for t in tasks if t.id in asked_tasks},
        )
    )
    # 一次，给整份清单用同一个「现在几点」——见 `list_project_tasks` 里同一行的理由。
    now = datetime.now(UTC)

    items: list[awaiting.WaitingItem] = []
    for task in tasks:
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                task_cards.get(task.id),
                beats.get(task.id),
                room_screen_live=chat.has_live_screen(task.room_id),
                awaiting_answer=task.id in asked_tasks,
            ),
            now=now,
        )
        if shown.column is not presentation.Column.needs_you:
            continue
        reason = awaiting.why_a_task_is_mine(
            task,
            task_cards.get(task.id),
            handle,
            asked=task.id in asked_tasks and waiting_for.get(task.room_id) == handle,
        )
        if reason is None:
            continue
        items.append(
            awaiting.WaitingItem(
                project_id=task.project_id,
                project_name=names.get(task.project_id, ""),
                topic_id=task.room_id,
                topic_title=titles.get(task.room_id, ""),
                task_id=task.id,
                task_title=task.title,
                display_status=shown.display_status,
                reason=reason,
                at=beats.get(task.id) or task.updated_at,
            )
        )

    for topic in topics:
        shown = presentation.room_presentation(
            presentation.facts_for_room(
                topic,
                # 「在跑」由跑轮次的进程说，而这份清单只收待处理的事项 —— 在跑的落
                # building，所以那个答案不会进来，这里不需要问它。
                set(),
                room_cards.get(topic.id),
                awaiting_answer=topic.id in asked_rooms,
            ),
            now=now,
        )
        if shown.column is not presentation.Column.needs_you:
            continue
        reason = awaiting.why_a_room_is_mine(
            room_cards.get(topic.id),
            handle,
            asked=topic.id in asked_rooms and waiting_for.get(topic.id) == handle,
        )
        if reason is None:
            continue
        items.append(
            awaiting.WaitingItem(
                project_id=topic.project_id,
                project_name=names.get(topic.project_id, ""),
                topic_id=topic.id,
                topic_title=topic.title,
                task_id=None,
                task_title=None,
                display_status=shown.display_status,
                reason=reason,
                at=topic.updated_at,
            )
        )

    items.sort(key=lambda item: item.at, reverse=True)
    rows = [item.as_dict() for item in items]
    return ok(page(rows, len(rows)))
