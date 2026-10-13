"""判到停摆之后，真的把那一跳补上。

平台自己这一侧没有一条「起一轮」的路径（I12，`agent/runtime.py` 的 `submit`）：一轮是
agent 那一档收件人的到达形态，调用点能做的只有点名。所以补跳走的是点名、定时投递、周
期任务共用的那本条账（`delivery/agent.py` 的 `record_agent`）—— 给那条线上现在坐着的座
位记一条投递，内容就是那句补跳、这次判停摆的依据、以及重挂该照哪份做。记完就到此为止：
现有的「定时投递」那一拍每 30 秒把账上还没送出去的投递派出去（`delivery/timer.py` 的
`deliver_due` 末尾就是 `dispatch_pending`），所以这里既不多出一条起轮的路，也不需要
chat 和 runner —— 判的人不碰「它跑起来没有」，那一问归投递账本。

三道熔断，每一道都是「这一遍不补」比「多补一条」好：

- **一次停摆只补一次。** 投递的去重键是「这条事件 + 收件人」，而事件 id 就是这次停摆的
  记录 id（`beats.stall_record_id`）—— 由「哪一拍、停在哪一刻」算出来的。巡检五分钟看
  一遍，看多少遍、进程重启多少次，账上都只有一条。
- **座位上还有没处理完的输入就不补。** 那个座位这一遍收不下新的一条：平台的入场闸本来
  就会把它挂住（`delivery/input_holds.py` 的 `seat_has_unfinished_input`，也是
  `agent/pending_messages.py` 和 `agent/initial_admission.py` 用的同一句话），挂住的投递
  会一直重试到放弃，最后只落成账本上一行 `failed` 和一条给平台的超时告警
  （`delivery/overdue.py`）—— 这一条是平台的指令（`ROOM_NOTICE`），放弃时不往那条线上
  说话（`delivery/timer.py` 的 `give_up_stale` 只替人的消息说），于是没人会知道这一跳
  没补成。这一遍不记，下一遍再看 —— 停摆还在，判的还是同一次，去重键也没变。
- **不替工作机上那两个循环说话。** 它只点名、只说照重挂步骤做，不从平台这一侧重发那一
  拍，也不重复看门狗自己那句提醒（`【看门狗】你的定时器停了`）。重挂步骤本身也不抄一份
  进仓库：资料库《总调度看门狗：脚本与重挂步骤》那份是源，抄一份迟早两边分叉，这里只
  指过去 —— 那句话里的文档名是 `REHANG_DOC` 这个参数，不是写死在句子里：它是那份文档
  的标题，跟着文档走，不是平台的界面文案。

心跳停摆和定时器停摆点的是同一个座位：重挂那一块本来就是两个脚本一起起（同一份步骤里
`ps` 数的是两个进程），能重挂的是这条线上坐着的人。所以两拍共用这一个入口，不各写一份。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select

from app.core.db import SessionFactory
from app.core.sentences import say
from app.domain.agent.platform_notices import EVENT_WAKE_MISSED, SEVERITY_WARN
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.delivery.agent import record_agent
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.ledger import DeliveryEvent
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.notification.models import NotificationType
from app.domain.project.models import Project
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic
from app.domain.wake_watch import beats
from app.domain.wake_watch.beats import Beat, Ticking, stall_record_id
from app.domain.wake_watch.observe import evidence

logger = logging.getLogger(__name__)

#: 补跳的那一轮落在哪条对话里：总调度那条线（#1343）自己的对话。
#:
#: 任务的 id 就是它自己那条对话（`delivery.agent.record_task_instruction` 里写给
#: 任务的话用的就是它），所以这一条既是对话也是任务行。
LINE: Final = beats.TIMER.conversation_id

#: 重挂该照哪份做。参数，不是句子的一部分：它是资料库里那份文档的标题，人随时可能
#: 改名，改名不该要求改代码、更不该让英文界面显示一个中文书名（`docs/i18n.md` 第 5 节
#: 的「英文无中文」没有例外）。所以句子只留一个 `{title}`，标题由这里传进去。
REHANG_DOC: Final = "总调度看门狗：脚本与重挂步骤"


async def summon(
    sessions: SessionFactory,
    beat: Beat,
    ticking: Ticking,
    since: timedelta,
    at: datetime,
) -> str | None:
    """给这条线上现在坐着的座位点一条补跳。

    返回补给了谁；这一遍不补（这一次已经补过、线不在了、座位忙着）返回 ``None``，
    所以调用方唯一能拿它做的事就是记一笔「补给了谁」—— 判据不在这个返回值里。

    只做一件事：往账本上写一条点名。跑不跑得起来由平台的投递派发决定，这一层不碰
    「有没有在跑」这类状态，也就不可能和别处抢着起轮。
    """
    window = stall_record_id(beat, ticking.at)
    minutes = int(since.total_seconds() // 60)
    async with sessions() as session:
        already = await session.scalar(
            select(Delivery.id).where(Delivery.event_id == window)
        )
        if already is not None:
            # 这一次停摆补过了。按事件 id 查，不按「谁补的」查：一次停摆只补一跳，
            # 中途那条线上换了座位也算补过了 —— 那一刻该叫的人已经叫了。
            return None
        task = await session.get(Task, LINE)
        room = await session.get(Topic, task.room_id) if task is not None else None
        project = (
            await session.get(Project, task.project_id) if task is not None else None
        )
        if task is None or room is None or project is None:
            logger.warning(
                "wake watch: %s 静了 %s 分钟，但 #1343 那条线找不到了，这一遍不补",
                beat.name,
                minutes,
            )
            return None
        if task.status != TaskStatus.open:
            # 和投递派发那一侧同一个条件（`delivery.agent.dispatch_pending` 的任务
            # 分支），先拦在这里：记一条注定会被判失败的投递，等于在那条线上留一行
            # 「没能起来」。
            logger.warning(
                "wake watch: %s 静了 %s 分钟，但 #1343 已经关了，这一遍不补",
                beat.name,
                minutes,
            )
            return None
        agent = await AgentInstanceService(session).for_task(
            project, room, task.agent_handle
        )
        # 用算出来的 handle，不用 `agent.handle`：投递那一行记的收件人
        # （`record_agent` 里的 `agent_instance_handle(instance_id)`）才是座位上
        # 认的那个名字，两处必须是同一个串，闸才问得到同一件事。
        seat = agent_instance_handle(agent.instance_id)
        if await seat_has_unfinished_input(session, LINE, seat):
            logger.warning(
                "wake watch: %s 静了 %s 分钟，但 %s 手上还有没处理完的输入，"
                "这一遍不补（下一遍再看）",
                beat.name,
                minutes,
                seat,
            )
            return None
        await record_agent(
            session,
            DeliveryEvent(
                # 事件 id 就是这一次停摆，去重键跟着它走。
                id=window,
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "projectId": str(project.id),
                    # `topicId` 是房间，`conversation_id` 才是这条线自己的对话 ——
                    # 别的生产者都是这么填的（`review/task_landing.py`、
                    # `api/task_instructions.py`），读它的人也是照房间读的
                    # （`notification/push.py` 拼的是房间链接）。
                    "topicId": str(room.id),
                    "eventType": EVENT_WAKE_MISSED,
                    "severity": SEVERITY_WARN,
                },
                # 这一笔本该是什么时候的事：最后一拍之后静满那一跳，也就是判出停摆
                # 的那一刻，不是这一遍看它的时刻。
                occurred_at=ticking.at + beat.stall_after,
            ),
            conversation_id=LINE,
            instance_id=agent.instance_id,
            content=_words(beat, ticking, at, minutes),
        )
        await session.commit()
    logger.warning(
        "wake watch: %s 静了 %s 分钟，已点名 %s 补跳", beat.name, minutes, seat
    )
    return seat


def _words(beat: Beat, ticking: Ticking, at: datetime, minutes: int) -> str:
    """这一轮补跳要它做什么，写成给座位读的话。

    第一行是这句话本身，后面两段：这次凭什么判停摆（和运行记录里那份依据同一处算
    出来的，`observe.evidence`），以及照哪份重挂。重挂的步骤不写在这里，只写它叫什
    么名字 —— 那份是资料库里的源，抄一份进来迟早两边分叉。
    """
    return "\n\n".join(
        [
            str(say("wakeWatchCatchUp", beat=say(beat.label), minutes=minutes)),
            evidence(beat, ticking, at, minutes),
            str(say("wakeWatchCatchUpHow", title=REHANG_DOC)),
        ]
    )
