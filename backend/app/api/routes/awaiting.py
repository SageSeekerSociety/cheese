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
from app.domain.agent.liveness import running_tasks
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.addressing import (
    REASON_ASKED,
    REASON_REVIEWER,
    Event,
    address,
    hand_of,
)
from app.domain.notification.services import (
    ProjectNotificationService,
    asks_for_decision,
)
from app.domain.project.repositories import ProjectRepository
from app.domain.review import archive
from app.domain.review.models import AcceptCard
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task import awaiting, presentation
from app.domain.room_task.repositories import TaskRepository
from app.domain.thread.services import onto_rooms, threads_of_rooms
from app.domain.topic.repositories import TopicRepository

router = APIRouter(prefix="/awaiting-me", tags=["awaiting"])

#: 决策请求那一行的理由。它不是一条事件点名的结果（`addressing` 的那几种），是一条
#: 通知本身就写着收件人，所以码只在这一份清单里有。
REASON_DECIDE = "decide"
#: 它也不是任务的哪一格，所以短语码同样只在这里。
PHRASE_DECIDE = "decision"
#: 变更提醒：芝士说了一句「这一轮改了什么」，没有要他答的，读过就了结。
REASON_READ = "read"
PHRASE_CHANGED = "change_alert"

#: 卡停住时，卡上那句原因就是这一行要说的「等的是什么」。
_CARD_SAYS_WHY = frozenset({"checks_failed", "bounced"})


def _one_line(text: str, limit: int = 140) -> str:
    """第二行只放一行：折掉换行，过长就截断。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


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

    # 归档的频道是只读的，里面没有人还能推进的事：它的任务和提问都不进清单。
    topics = [
        t
        for t in await TopicRepository(db).list_for_projects(project_ids, viewer=handle)
        if str(t.status) != "archived"
    ]
    rooms = {t.id: t for t in topics}
    # A task is seen where its channel is: none from a private channel I left.
    tasks = [
        t
        for t in await TaskRepository(db).list_for_projects(project_ids)
        if t.room_id in rooms
    ]
    task_ids = [t.id for t in tasks]
    topic_ids = [t.id for t in topics]

    blocks = BlockRepository(db)
    task_cards = await AcceptCardRepository(db).latest_by_task(task_ids)
    room_cards = await _room_cards(db, topic_ids)
    beats = await TaskRepository(db).last_block_at_for_tasks(task_ids)
    # {地点: 这道题在等谁}。一个待确认问题只有**发起那一轮的人**能回答，提问那一刻
    # 就记在题上——不是事后去问轮次：芝士问完就收尾，那一轮早就关了。
    # A question asked in a 支线 is its channel's, and opens on that 支线.
    thread_rooms = await threads_of_rooms(db, topic_ids)
    in_threads = await blocks.awaiting_answer_blocks(list(thread_rooms))
    asked_in_thread = {
        room: thread for thread, room in thread_rooms.items() if thread in in_threads
    }
    questions = onto_rooms(
        await blocks.awaiting_answer_blocks(topic_ids + task_ids) | in_threads,
        thread_rooms,
    )
    asked = {place: question[0] for place, question in questions.items()}
    said = await blocks.contents([question[1] for question in questions.values()])
    # 「运行中」也在这一页出现（一列里的每一格都是同一个函数算的），所以这一屏每行
    # 要的当下事实也一次问完 —— 这批任务有没有一轮在跑（`agent.liveness`）。
    running = await running_tasks(chat, db, tasks)
    # 一次，给整份清单用同一个「现在几点」——见 `list_project_tasks` 里同一行的理由。
    now = datetime.now(UTC)

    items: list[awaiting.WaitingItem] = []
    for task in tasks:
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                task_cards.get(task.id),
                running=task.id in running,
                awaiting_answer=task.id in asked,
            ),
            now=now,
        )
        card = task_cards.get(task.id)
        asking = asked.get(task.id)
        addressed = address(
            Event(
                reviewers=() if card is None else (card.reviewer_handle,),
                reporter=task.reporter_handle,
                asked=asking,
                # 题上没记着谁（平台发起的轮次）：等它的是这条活的人 —— 和通知
                # 那边同一个名单（`announce.notify_question`）。题根本不在等，就没
                # 有这一层。
                asked_also=(task.people if task.id in asked and asking is None else ()),
                owner=(
                    task.owner_handle
                    if presentation.owner_acts_on(shown, running=task.id in running)
                    else None
                ),
            ),
            hand_of(shown.column),
        )
        reason = addressed.reason_for(handle)
        if reason is None:
            continue
        room = rooms.get(task.room_id)
        if reason == REASON_ASKED:
            detail = said.get(questions[task.id][1], "")
        elif reason == REASON_REVIEWER and card is not None:
            detail = card.change_subject or ""
        elif card is not None and shown.phrase in _CARD_SAYS_WHY:
            detail = card.note or ""
        else:
            detail = ""
        items.append(
            awaiting.WaitingItem(
                project_id=task.project_id,
                project_name=names.get(task.project_id, ""),
                topic_id=task.room_id,
                topic_title=room.title if room else "",
                task_id=task.id,
                task_title=task.title,
                task_title_source=str(task.title_source),
                phrase=shown.phrase,
                reason=reason,
                at=beats.get(task.id) or task.updated_at,
                block_id=questions[task.id][1] if reason == "asked" else None,
                detail=_one_line(detail),
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
                awaiting_answer=topic.id in asked,
            ),
            now=now,
        )
        card = room_cards.get(topic.id)
        addressed = address(
            # 房间没有「提需求的人」这一栏 —— 那是一条活上的字段。
            Event(
                reviewers=() if card is None else (card.reviewer_handle,),
                asked=asked.get(topic.id),
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
                task_id=None,
                task_title=None,
                task_title_source=None,
                phrase=shown.phrase,
                reason=reason,
                at=topic.updated_at,
                block_id=questions[topic.id][1] if reason == "asked" else None,
                thread_id=asked_in_thread.get(topic.id) if reason == "asked" else None,
                detail=(
                    _one_line(said.get(questions[topic.id][1], ""))
                    if reason == REASON_ASKED
                    else ""
                ),
            )
        )

    # 写给他、还没了结的通知：芝士请他拍板还没拍的，和芝士说了改了什么他还没读的。
    # 每条通知自己说它关于哪条对话（`conversation_id`：频道自己那条线、它的一条任
    # 务、或一条支线），条目按那条对话记 —— 看不见的、归档了的频道里的不算；不指
    # 哪条对话的，记在项目上。
    task_by_id = {task.id: task for task in tasks}
    room_of_conversation: dict[uuid.UUID, uuid.UUID] = {t.id: t.id for t in topics}
    room_of_conversation.update({task.id: task.room_id for task in tasks})
    room_of_conversation.update(thread_rooms)
    for alert in await ProjectNotificationService(db).still_open(
        project_ids, recipient_handle=handle
    ):
        conversation = alert.conversation_id
        # 它得能找回自己所在的频道：找不回的（看不见的、归档了的频道里的）不算。
        at_room = (
            None if conversation is None else room_of_conversation.get(conversation)
        )
        room = None if at_room is None else rooms.get(at_room)
        if alert.project_id is None or (conversation is not None and room is None):
            continue
        card = None if conversation is None else task_by_id.get(conversation)
        decision = asks_for_decision(alert)
        listed = (alert.metadata_payload or {}).get("options") if decision else None
        options = listed or []
        items.append(
            awaiting.WaitingItem(
                project_id=alert.project_id,
                project_name=names.get(alert.project_id, ""),
                topic_id=room.id if room else None,
                topic_title=room.title if room else "",
                task_id=card.id if card else None,
                task_title=card.title if card else None,
                task_title_source=str(card.title_source) if card else None,
                thread_id=conversation if conversation in thread_rooms else None,
                phrase=PHRASE_DECIDE if decision else PHRASE_CHANGED,
                reason=REASON_DECIDE if decision else REASON_READ,
                at=alert.created_at,
                detail=_one_line(alert.body or ""),
                alert_id=alert.id,
                options=tuple(o for o in options if isinstance(o, str)),
                headline=alert.title or "",
            )
        )

    items.sort(key=lambda item: item.at, reverse=True)
    return items
