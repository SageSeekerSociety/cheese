"""待我处理那份清单共用的东西：几个码、两段摘要，和查询本身。

`all_waiting` 是这份清单的查询：项目里的事（`waiting_items`），加上等他回答的邀请
和申请（`awaiting_asks.membership_asks`）。「待我处理」那一页（`routes/awaiting.py`）
拿它渲染，桌面图标上那个数字（`routes/notifications_live.py`）拿它来数，所以两处的
数永远一样。

它留在 API 层、被两个调用方 import，而不留在路由模块里：路由模块之间不互相 import
（C4 `routes-independent`）。它也不进领域层 —— 要问项目、房间、活、验收卡、消息块
五个地方，而项目看板那两份（`projects.list_project_tasks` / `topics.list_topics`）
也是在路由这一层把批查询拼起来的，同一个形状。

上面那几个码只在这份清单里有：`REASON_DECIDE` / `PHRASE_DECIDE` 是一条通知自己就
写着收件人，不是某个事件点名的结果；`REASON_READ` / `PHRASE_CHANGED` 是变更提醒，
没有要他答的，读过就了结。

不放 router：这里没有被挂载的东西，下面这些名字由挂着 router 的模块 import。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.awaiting_asks import (
    MembershipAsk,
    RunAsk,
    TaskAsk,
    membership_asks,
    run_asks,
    task_asks,
)
from app.domain.addressing import (
    REASON_ASKED,
    REASON_REVIEWER,
    Event,
    address,
)
from app.domain.agent.teammate_runs import teammate_runs
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.services import threads_of_rooms
from app.domain.notification.services import (
    ProjectNotificationService,
    asks_for_decision,
)
from app.domain.project.repositories import ProjectRepository
from app.domain.review.queries import RailCard, listed_cards
from app.domain.review.services import AcceptService
from app.domain.room_task import awaiting, presentation
from app.domain.room_task.presentation import hand_of
from app.domain.room_task.repositories import ListedTask, TaskRepository
from app.domain.thread.services import onto_rooms
from app.domain.topic.repositories import TopicRepository

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


def _reviewed(reason: str, card: RailCard | None) -> dict:
    """等你审阅的那一行还说第几版、什么时候递上来的（`card_version` / `card_since`）。"""
    if reason != REASON_REVIEWER or card is None:
        return {}
    return {"card_version": card.version, "card_since": card.created_at}


def _one_line(text: str, limit: int = 140) -> str:
    """第二行只放一行：折掉换行，过长就截断。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


async def waiting_items(
    db: AsyncSession, *, handle: str, user_id: int | None
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
    # 这一趟只要主键：看得见、没归档的频道有哪几个，用来判断一条活所在的频道他
    # 看不看得见。整行只为真正会摆上这一屏的那几个再取一次（见下面的
    # `render_rooms`）——一屏常常只点到一两个人，却要为全部频道付整行装配的钱。
    topic_ids = await TopicRepository(db).visible_unarchived_ids_for_projects(
        project_ids, viewer=handle
    )
    visible_rooms = set(topic_ids)
    # A task is seen where its channel is: none from a private channel I left.
    # 关了的活落「已结束」那一列，那一列的下一步在平台手上，`address` 不点任何人
    # —— 所以判据、卡、待答题都只为还开着的活去问。一个项目有史以来派出去的活
    # 十之九都关了（dev：1437 条里 1355 条，而某个人名下的就有 548 条），不先按
    # 这一笔收，候选收窄收不住，后面每一趟查询都在为早就结束的事付钱。所以下面
    # 这一笔先在库里按「还开着」收，再按列投影收到「可能点到他的活」：一屏的代价
    # 该跟着「点到他多少」走，不是「他看得见多少」。关了的活对判据没用，只对「通知
    # 指到的活认回自己在哪个频道」有用，而那一笔按 id 问就够（下面的 `pointed`）
    # ——本机 13 个项目实测，这一笔的整行扫描 1917 条，收窄后 10.9ms 降到 1ms 上下
    # （`brief_for_projects` 的 `open_only`）。
    underway = [
        b
        for b in await TaskRepository(db).brief_for_projects(
            project_ids, open_only=True
        )
        if b.room_id in visible_rooms
    ]

    blocks = BlockRepository(db)
    # 每条活最新那张卡，和每个房间**自己**那张还没结算的卡（`task_id` 为空才是房
    # 间自己的：一条活递的卡把房间记在 `topic_id` 上，不分开的话一条活在等验收会让
    # 它上面那个房间也显示成等验收）—— 同一张表问一趟，交回来的是纯值（`RailCard`）。
    room_cards, task_cards = await listed_cards(
        db, room_ids=topic_ids, task_ids=[b.id for b in underway]
    )
    # 卡递给谁在 `accept_card_reviewer` 表里（一卡多人），不在卡那一行上：这两批卡
    # 的评审人各一次查完，下面每行只做一次集合查询（`address` 要的就是这个集合）。
    cards_service = AcceptService(db)
    task_card_reviewers = await cards_service.reviewer_handles_for_cards(
        [c.id for c in task_cards.values()]
    )
    room_card_reviewers = await cards_service.reviewer_handles_for_cards(
        [c.id for c in room_cards.values()]
    )
    # {地点: 这道题在等谁}。芝士问谁，提问那一刻就记在题上（`block.asked`）——不是
    # 事后去问轮次：芝士问完就收尾，那一轮早就关了。跳过了的人不再等。
    # A question asked in a 支线 is its channel's, and opens on that 支线.
    thread_rooms = await threads_of_rooms(db, topic_ids)
    # 一次问完所有可能停着一道题的地方：频道、活、支线。以前这里分两次问，「题问
    # 出来之后谁说过话」那一趟因此要扫几百个地方两遍 —— 而两份答案本来就要并起
    # 来用（`onto_rooms` 把支线的补到频道上），并起来之后分不出哪一半是后问的。
    # 合并少一次往返；支线在 dev 上没有几条，这一笔省下的是往返，不是扫描。
    found = await blocks.awaiting_answer_blocks(
        [*thread_rooms, *topic_ids, *(b.id for b in underway)]
    )
    in_threads = [place for place in found if place in thread_rooms]
    asked_in_thread = {
        room: thread for thread, room in thread_rooms.items() if thread in in_threads
    }
    questions = onto_rooms(found, thread_rooms)
    asked = {place: question[0] for place, question in questions.items()}
    # 写给他、还没了结的通知也在这份清单上，每条自己说它关于哪条对话。先问出来，
    # 是因为它指到的那几条**活**也要一起取（下面的循环要拿它们的标题）。
    alerts = await ProjectNotificationService(db).still_open(
        project_ids, recipient_handle=handle
    )
    # 一条活写得出 `handle` 的名字，只可能在这些地方之一：他提的、他负责的、他协
    # 力的、卡递给他的，或者有一道题问在他身上。这是**候选，只多不少** —— 一道题
    # 问到谁要多查一步（那个人的名字不一定在这条活的字段里），所以带题的活一律留
    # 下 —— 真正算数的还是下面对每一行跑的地址判据。
    task_ids = [
        b.id
        for b in underway
        if b.reporter_handle == handle
        or b.owner_handle == handle
        or handle in (b.contributor_handles or ())
        or b.id in asked
        or (
            task_cards.get(b.id) is not None
            and handle in (task_card_reviewers.get(task_cards[b.id].id) or ())
        )
    ]
    # 通知指到的活不走判据（关了的也算，一条「改了什么」的提醒不因为活关了就不用
    # 读），它们只要被认出标题和所在的频道。通知指到的可能是一条**关了的**活 ——
    # 关了的活不在上面的候选里（`brief_for_projects` 的 `open_only`），但通知照旧
    # 要摆出来，所以这一笔按 id 直接问。指到频道自己或支线的认不出活来，按 id 问
    # 自然给空；看不见的频道里的照旧不算。
    on_the_list = set(task_ids)
    pointed_at = {
        a.conversation_id
        for a in alerts
        if a.conversation_id is not None and a.conversation_id not in on_the_list
    }
    # 两批活一趟取回，每条带着它最后一次说话的时间（看板的心跳）：这一屏读的是
    # 活的状态、人和日期，拿到的是那几列折成的值（`ListedTask`），不是整行实体。
    tasks: list[ListedTask] = []
    pointed: list[ListedTask] = []
    for task in await TaskRepository(db).listed_by_ids([*task_ids, *pointed_at]):
        if task.id in on_the_list:
            tasks.append(task)
        elif task.room_id in visible_rooms:
            pointed.append(task)
    # 哪几条活自己那位队友的会话此刻在跑：它在这一次跑里没报过别的，就是「在做」
    # （`presentation`），不点到负责人。
    runs = await teammate_runs(db, tasks)
    # 题文和题一起回来的（`awaiting_answer_blocks`），不再按块 id 取第二趟。
    said = {question[1]: question[2] for question in questions.values()}
    # 一次，给整份清单用同一个「现在几点」——见 `list_project_tasks` 里同一行的理由。
    now = datetime.now(UTC)

    # 每条通知自己说它关于哪条对话，它得能找回自己在哪个频道才作数 —— 那道映射要
    # 认得出**他看得见**的每条活，不只是留到这一屏上的那些（上面按判据收窄过）。
    room_of_conversation: dict[uuid.UUID, uuid.UUID] = {tid: tid for tid in topic_ids}
    room_of_conversation.update({b.id: b.room_id for b in underway})
    room_of_conversation.update({t.id: t.room_id for t in pointed})
    room_of_conversation.update(thread_rooms)
    # 真正会摆上这一屏的频道只有这么几个：留到最后的每条活所在的频道，两条判据可
    # 能点到 `handle` 的频道，通知指到的频道。整行只为它们读一次 —— 看得见的频道
    # 成百上千，而一屏常常只点到一两个人，别为全部频道付装配整行的钱。
    # 房间自己那两条判据在这里就能算完；「留到最后的每条活」要等下面判据跑过才知
    # 道，所以那一半并进下面一起，别先按候选读一遍。
    render_rooms: set[uuid.UUID] = {
        tid
        for tid in topic_ids
        if (tid in asked and handle in asked[tid].waiting())
        or (
            room_cards.get(tid) is not None
            and handle in (room_card_reviewers.get(room_cards[tid].id) or ())
        )
    }
    for alert in alerts:
        if alert.conversation_id is None:
            continue
        at_room = room_of_conversation.get(alert.conversation_id)
        if at_room is not None:
            render_rooms.add(at_room)

    # 判据先跑：它不碰房间整行，只读手上这几份。跑完才知道哪些活真的摆上来 ——
    # 本机实测，候选里过 `address` 这一关的不到一半，先按候选把房间读回来，一半
    # 是白读的整行装配。
    left: list[tuple[ListedTask, presentation.Presentation, str, str]] = []
    for task in tasks:
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                task_cards.get(task.id),
                awaiting_answer=task.id in asked,
                teammate_runs=runs.get(task.id),
            ),
            now=now,
        )
        card = task_cards.get(task.id)
        asking = asked.get(task.id)
        card_reviewers = tuple(task_card_reviewers.get(card.id, [])) if card else ()
        addressed = address(
            Event(
                reviewers=card_reviewers,
                reporter=task.reporter_handle,
                # 题上没点名（平台发起的轮次）：等它的是这条活的人 —— 和通知那边同
                # 一个名单（`announce.notify_question`）。题根本不在等，就没有这一层。
                asked_also=() if asking is None else asking.waiting(task.people),
                owner=(
                    task.owner_handle if presentation.owner_acts_on(shown) else None
                ),
            ),
            hand_of(shown.column),
        )
        reason = addressed.reason_for(handle)
        if reason is None:
            continue
        if reason == REASON_ASKED:
            detail = said.get(questions[task.id][1], "")
        elif reason == REASON_REVIEWER and card is not None:
            detail = card.change_subject or ""
        elif card is not None and shown.phrase in _CARD_SAYS_WHY:
            detail = card.note or ""
        elif shown.phrase == presentation.NeedsYou.blocked.value:
            # 卡住是队友自己报的，它附的那句就是要你做什么；没写的留空，由屏幕
            # 说一句「打开看看它需要什么」—— 那句话要按读者的语言画。
            detail = task.progress_note or ""
        else:
            detail = ""
        left.append((task, shown, reason, detail))
    render_rooms.update(task.room_id for task, _, _, _ in left)

    rooms = {t.id: t for t in await TopicRepository(db).listed_by_ids(render_rooms)}
    # 频道这一格按原来的次序走（`topic_ids` 是查出来的顺序），只是现在只装得下真
    # 会摆上来的那几个。
    topics = [rooms[tid] for tid in topic_ids if tid in rooms]

    items: list[awaiting.WaitingItem] = []
    # 卡住的那几行要叫得出做它的队友：它没留话时，第二行写「某某卡住了」。只为这几
    # 行去问 —— 一屏卡住的任务通常只有一两件，不为整份清单装配每一位队友。
    teammates = AgentInstanceService(db)
    for task, shown, reason, detail in left:
        room = rooms.get(task.room_id)
        named = (
            await teammates.for_task_by_id(
                task.project_id, task.room_id, task.agent_handle
            )
            if shown.phrase == presentation.NeedsYou.blocked.value
            else None
        )
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
                at=task.last_said_at or task.updated_at,
                block_id=questions[task.id][1] if reason == "asked" else None,
                detail=_one_line(detail),
                **_reviewed(reason, task_cards.get(task.id)),
                agent_name=None if named is None else named.display_name,
                agent_name_source=None if named is None else str(named.name_source),
            )
        )

    for topic in topics:
        # 频道这一格只可能点到两种人：卡递给他的（`reviewers`），或有一道题问在他
        # 身上（`asked`）—— 房间没有「提需求的人」「负责人」这些栏，`Event` 的其余
        # 字段这里都是空的。两者都不沾的，算到哪一格都写不出他的名字，先跳过，省下
        # 一次 `room_presentation`。
        card = room_cards.get(topic.id)
        asking = asked.get(topic.id)
        waiting = () if asking is None else asking.waiting()
        room_reviewers = tuple(room_card_reviewers.get(card.id, [])) if card else ()
        if not (handle in waiting or handle in room_reviewers):
            continue
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
        addressed = address(
            # 房间没有「提需求的人」这一栏 —— 那是一条活上的字段。
            Event(
                reviewers=room_reviewers,
                asked_also=waiting,
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
                # 这间房这一格若是在等一道题，日期就是**那一道题**问出来的时刻；
                # `topic.updated_at` 只在频道行自己被改时动，一道挂了十天的题会写成
                # 频道最后一次改名的那天。
                at=(
                    questions[topic.id][3]
                    if reason == REASON_ASKED
                    else topic.updated_at
                ),
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
    task_by_id = {task.id: task for task in (*tasks, *pointed)}
    for alert in alerts:
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


async def all_waiting(
    db: AsyncSession, *, handle: str, user_id: int | None
) -> list[awaiting.WaitingItem | MembershipAsk | RunAsk | TaskAsk]:
    """等 `handle` 处理的全部事项，最近的在前：项目里的事，等他回答的邀请、申请，
    等他允许在自己电脑上运行的命令，和他出的题目上等他批准、批改的。"""
    rows: list[awaiting.WaitingItem | MembershipAsk | RunAsk | TaskAsk] = [
        *await waiting_items(db, handle=handle, user_id=user_id),
        *await membership_asks(db, handle=handle, user_id=user_id),
        *await run_asks(db, handle=handle),
        *await task_asks(db, user_id=user_id),
    ]
    rows.sort(key=lambda row: row.at, reverse=True)
    return rows
