"""待我处理：跨项目的那一份清单。

看板回答「这个项目现在有什么在等人」，这里回答「**我**现在要处理什么」—— 同一份规则
（`room_task/presentation.py`）、同一份收件人判据（`delivery/addressing.py`，投递用的
也是它），范围换成我能看见的全部项目。为什么它不能由通知表拼出来，写在
`room_task/awaiting.py` 的模块说明里。

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
from app.domain.agent.liveness import task_liveness
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.addressing import Event, address, hand_of
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
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        return ok(page([], 0))
    items = await waiting_items(db, chat, handle=who.handle, user_id=who.user_id)
    rows = [item.as_dict() for item in items]
    return ok(page(rows, len(rows)))


async def waiting_items(
    db: AsyncSession, chat: ChatService, *, handle: str, user_id: int | None
) -> list[awaiting.WaitingItem]:
    """点到 `handle` 的、还没处理完的事项，最近动过的在前。

    待办页和桌面 app 图标上的数字（`notifications_live.py`）读的都是它，所以两处
    的数永远一样。
    """
    projects = await ProjectRepository(db).list_visible_to(
        handle=handle, user_id=user_id
    )
    if not projects:
        return []
    names = {p.id: p.name for p in projects}
    project_ids = list(names)

    tasks = await TaskRepository(db).list_for_projects(project_ids)
    topics = await TopicRepository(db).list_for_projects(project_ids)
    rooms = {t.id: t for t in topics}
    task_ids = [t.id for t in tasks]
    topic_ids = [t.id for t in topics]

    blocks = BlockRepository(db)
    task_cards = await AcceptCardRepository(db).latest_by_task(task_ids)
    room_cards = await _room_cards(db, topic_ids)
    beats = await TaskRepository(db).last_block_at_for_tasks(task_ids)
    # {地点: 这道题在等谁}。一个待确认问题在等**发起那一轮的人**，提问那一刻就记在
    # 题上——不是事后去问轮次：芝士问完就收尾，那一轮早就关了。
    room_questions, task_questions = await blocks.awaiting_answer_blocks(
        topic_ids, task_ids
    )
    asked_tasks = {place: question[0] for place, question in task_questions.items()}
    asked_rooms = {place: question[0] for place, question in room_questions.items()}
    # 「运行中」也在这一页出现（一列里的每一格都是同一个函数算的），所以这一屏每行
    # 要的两位当下事实也一次问完 —— 这批活的屏幕和分身（`agent.liveness`）。
    live = await task_liveness(chat, db, tasks)
    # 一次，给整份清单用同一个「现在几点」——见 `list_project_tasks` 里同一行的理由。
    now = datetime.now(UTC)

    items: list[awaiting.WaitingItem] = []
    for task in tasks:
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                task_cards.get(task.id),
                beats.get(task.id),
                room_screen_live=live[task.id].screen,
                worker_live=live[task.id].worker,
                awaiting_answer=task.id in asked_tasks,
            ),
            now=now,
        )
        card = task_cards.get(task.id)
        addressed = address(
            Event(
                reviewers=() if card is None else (card.reviewer_handle,),
                reporter=task.reporter_handle,
                asked=asked_tasks.get(task.id),
            ),
            hand_of(shown.column),
        )
        reason = addressed.reason_for(handle)
        if reason is None:
            continue
        room = rooms.get(task.room_id)
        items.append(
            awaiting.WaitingItem(
                project_id=task.project_id,
                project_name=names.get(task.project_id, ""),
                topic_id=task.room_id,
                topic_title=room.title if room else "",
                topic_title_source=str(room.title_source) if room else "human",
                task_id=task.id,
                task_title=task.title,
                task_title_source=str(task.title_source),
                phrase=shown.phrase,
                reason=reason,
                at=beats.get(task.id) or task.updated_at,
                block_id=task_questions[task.id][1] if reason == "asked" else None,
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
        card = room_cards.get(topic.id)
        addressed = address(
            # 房间没有「提需求的人」这一栏 —— 那是一条活上的字段。
            Event(
                reviewers=() if card is None else (card.reviewer_handle,),
                asked=asked_rooms.get(topic.id),
            ),
            hand_of(shown.column),
        )
        reason = addressed.reason_for(handle)
        if reason is None:
            continue
        items.append(
            awaiting.WaitingItem(
                project_id=topic.project_id,
                project_name=names.get(topic.project_id, ""),
                topic_id=topic.id,
                topic_title=topic.title,
                topic_title_source=str(topic.title_source),
                task_id=None,
                task_title=None,
                task_title_source=None,
                phrase=shown.phrase,
                reason=reason,
                at=topic.updated_at,
                block_id=room_questions[topic.id][1] if reason == "asked" else None,
            )
        )

    items.sort(key=lambda item: item.at, reverse=True)
    return items
