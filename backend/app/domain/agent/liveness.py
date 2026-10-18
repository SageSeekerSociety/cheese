"""Which tasks have a turn running right now — the board's 「运行中」.

Two places know, and either is enough to say a turn started. The process
running the turn knows at once (``ChatService.live.active_turn_ids``), but only for
its own turns and only until it is replaced. The turn's interval knows across
processes: it opens when the prompt reaches the task's session and closes when
the session stops, or when the orphan sweep finds it dead. So a task is running
when an interval of one is open and was delivered, or this process is running
a turn in it whose interval has not closed.

Only the interval says a turn ended. This process lets go of a turn a moment
after its interval closes, and the pages are told the turn ended when the close
commits (`room_task.live.turns_moved`); a read in that moment must already say
the turn is over, or the page keeps 运行中 with nothing left to tell it again.
"""

import uuid
from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Uuid, bindparam, column, func, select, table, union_all
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall
from app.domain.agent.models import AgentTurn
from app.domain.room_task.models import Task

# 「哪个房间的支线」：一张裸表，只取 id 和它挂在哪个房间下。`conversation.services.
# threads_of_rooms` 是同一件事的完整版，但那个模块拖着别的依赖，这里只要这两列。
_threads = table("threads", column("id", Uuid), column("room_id", Uuid))


class LiveWork(Protocol):
    """在跑的那一轮自己的状态（`agent/turn/intake/status.AgentWorkRunner`）。

    只取这一处，是为了让这一层不去 import 跑轮次的那些东西——它读的是一份内存里
    的当下事实，谁拿着它不重要。"""

    def live_work_for_topic(self, topic_id: uuid.UUID) -> dict | None: ...


# 最后输出那个时刻取整到几秒。见 `last_output_at`：话题清单的条件请求靠整份 body
# 的指纹（`conditional_json`），一帧一个时间戳会让「有任何一轮在跑」期间每一次轮询
# 都命中不了 304、几百 KB 原样重传一遍 —— 和 `activity` 不带 `expires_in` 是同一条
# 理由。取整把它压到一分钟最多变一次；灯判的是五分钟，误差最多一分钟，够了。
OUTPUT_CLOCK_S = 60


def silent_of(runner: LiveWork, topic_id: uuid.UUID) -> float | None:
    """这个话题里在跑的那一轮「多久没有动静了」（秒）。

    动静有两样：房间里落下了一帧，或者读这一轮的那一路收到了 runner 的一声应答
    （`WorkStatus.live_work_for_topic` 取两者较新的）。长思考、跑长命令的时候只有
    后者 —— 只数前者，会把一轮好好跑着的活说成停了。

    None = 没有在跑的轮次，或者记录里没有这个戳 —— 「不知道」不当成「很新」用，
    读的人（侧栏的灯）自己决定那时画什么。
    """
    work = runner.live_work_for_topic(topic_id)
    return None if work is None else work["silent_for_s"]


def silent_gaps(
    runner: LiveWork, topic_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, float]:
    """`silent_of` 一次问一整页。

    只为调用方说是在跑的那几个问：`live_work_for_topic` 一次只看一个话题，而
    `running_topic_ids` 才是给整页的那次批量读。
    """
    gaps: dict[uuid.UUID, float] = {}
    for topic_id in topic_ids:
        gap = silent_of(runner, topic_id)
        if gap is not None:
            gaps[topic_id] = gap
    return gaps


def last_output_at(gap_s: float | None, now: datetime) -> datetime | None:
    """最后一次有动静的时刻：从那句「多久没动静了」倒推，取整到分钟
    （`OUTPUT_CLOCK_S`）。说不出来（None）就不给时刻 —— 前后端都不许自己编一个。

    字段名还是 `last_output_at`（话题清单的契约），但它数的已经是任何一样动静：
    输出，或者会话自己应的一声（`silent_of`）。"""
    if gap_s is None:
        return None
    at = now - timedelta(seconds=gap_s)
    return at.replace(second=0, microsecond=0)


async def running_tasks(
    chat, db: AsyncSession, task_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these tasks have a turn running in their own conversation.

    Takes ids, not the rows: a listing reads its tasks as the few columns it
    draws (`room_task.repositories.ListedTask`), and this question is about the
    task's own conversation — which the id alone names.
    """
    ids = list(task_ids)
    if not ids:
        return set()
    here = {task_id: set(chat.live.active_turn_ids.get(task_id, ())) for task_id in ids}
    turns_here = set().union(*here.values())
    ended = (
        set(
            await db.scalars(
                select(AgentTurn.id).where(
                    AgentTurn.id.in_(turns_here), AgentTurn.stopped_at.is_not(None)
                )
            )
        )
        if turns_here
        else set()
    )
    open_turns = await db.scalars(
        select(AgentTurn.conversation_id).where(
            AgentTurn.conversation_id.in_(ids),
            AgentTurn.stopped_at.is_(None),
            AgentTurn.delivered_at.is_not(None),
        )
    )
    return {task_id for task_id, turns in here.items() if turns - ended} | {
        task_id for task_id in open_turns if task_id is not None
    }


# 跨进程认一轮在跑，只能靠「那一行还开着、而且刚起来过」。这个窗口比红灯的阈值
# （五分钟）长一段：一个真正在跑、只是这一会儿没输出的轮次，不该先绿一下、再红一
# 下、然后下一帧又回来。
RUNNING_LOOKBACK = timedelta(minutes=15)


async def moving_topics(
    db: AsyncSession,
    topic_ids: Collection[uuid.UUID],
    running: Collection[uuid.UUID] = (),
    threads: Mapping[uuid.UUID, uuid.UUID] | None = None,
    *,
    now: datetime | None = None,
    lookback: timedelta = RUNNING_LOOKBACK,
) -> set[uuid.UUID]:
    """这些**话题**里有哪几个此刻在往前走：它自己那条线上有一轮在跑，或者它名下的
    活 / 支线里有一轮在跑 —— 侧栏那盏呼吸绿问的就是它。

    和 `running_tasks` 的两处不同，都是因为问的是房间而不是活：一轮在跑可能落在这
    个房间名下的任何一段对话上（它自己的线、它的活、它的支线），要归回房间；而且
    房间是成百地一次问的，所以这里不能用 IN 列表 —— 一个项目的房间加上它们名下的
    活和支线能上万，而那会绑一万个参数、每换一个项目就得重新编译一次（
    `conversation.services.threads_of_rooms` 记过同一件事）。

    `running` 是调用方手里那份**当下**在跑的对话（`AgentWorkRunner.running_topic_ids`
    —— 这个进程和它的 broker 知道的那些）。此外还要单独读一遍库：一个进程按不住另
    一个进程正在跑的那一轮（多 worker 时侧栏由谁应答说不准），只有「那一行还开着」
    是哪个进程都看得见的事实。库里那一份只认刚起来过的（`RUNNING_LOOKBACK`），因为
    一行停在那儿不动的僵尸轮次（进程被杀，`finally` 没跑成）会永远读作在跑。

    ``threads`` 是调用方已经问过的那一份（`conversation.services.threads_of_rooms`
    的形状：支线 -> 它所在的房间）：读一个房间的头时它前面已经为别的问题读过一次，
    同一批房间问两遍就是同一张表读两趟。给了就用给的（只取 `topic_ids` 里那些房间
    的，多给的不会替没问的房间作答）；没给就自己读同一张表，免得这一层为了它拖着
    那边的依赖。

    这两个都可以按位置给：调用它的房间头那个文件正好卡在行数上限，多出来的
    那一行会把一次去重变成一次拆文件（`check-file-sizes.py`）。
    """
    ids = list(topic_ids)
    if not ids:
        return set()
    now = now or datetime.now(UTC)
    mine = set(ids)
    # 这些话题名下的对话 -> 它所在的房间。支线也算：一条支线里跑的轮次同样是在管
    # 这间房（`topics._working_in_threads` 认的是同一件事）。
    if threads is None:
        found_threads, cached_threads = recall(db, ("threads_in_rooms", tuple(ids)))
        threads = (
            dict(cached_threads[0])
            if found_threads
            else {
                thread_id: room_id
                for thread_id, room_id in (
                    await db.execute(
                        select(_threads.c.id, _threads.c.room_id).where(
                            _threads.c.room_id.in_(ids)
                        )
                    )
                ).all()
            }
        )
    inner: dict[uuid.UUID, uuid.UUID] = {
        thread_id: room_id for thread_id, room_id in threads.items() if room_id in mine
    }
    # 刚起来、还开着、而且真送出去了的那些轮次。不是拿房间去 IN：这一份读的是很小
    # 的一张部分索引（`ix_agent_turns_open`），回来之后再在内存里挑出属于这些话题的。
    # 落在活上的轮次顺手带回那件活的房间：库里这些开着的轮次是全站的，大多不在这
    # 批房间里，另拿它们去 `tasks` 查一趟房间，次数就看别的项目此刻有没有在跑了。
    #
    # 这个进程手里在跑、库里还没有那一行的对话也在这同一条语句里问：一轮刚起、还
    # 没落库的那一下只有进程自己知道它在跑，而它归哪个房间仍要问 `tasks`。分开写
    # 就是一次只在「此刻真有一轮在跑」时才发生的额外往返 —— 列表接口的 SQL 条数
    # 于是随别的项目在不在跑而变（`test_topic_list_batches` 的 `_ALL_TASKS_SELECT`）。
    # 在跑的那几个对话摊成行，和开着的轮次一起读回来，条数就是恒定的。
    running_param = bindparam("running_conversations", list(running), type_=ARRAY(Uuid))
    this_process = (
        func.unnest(running_param)
        .table_valued(column("id", Uuid))
        .render_derived(name="running_conversations")
    )
    arrived = (
        await db.execute(
            union_all(
                select(AgentTurn.conversation_id, Task.room_id)
                .outerjoin(Task, Task.id == AgentTurn.conversation_id)
                .where(
                    AgentTurn.stopped_at.is_(None),
                    AgentTurn.delivered_at.is_not(None),
                    AgentTurn.started_at >= now - lookback,
                ),
                select(this_process.c.id, Task.room_id)
                .select_from(this_process)
                .outerjoin(Task, Task.id == this_process.c.id),
            )
        )
    ).all()
    for conversation, room_id in arrived:
        if conversation is not None and room_id in mine:
            inner[conversation] = room_id
    active = set(running) | {c for c, _ in arrived if c is not None}
    moving: set[uuid.UUID] = set()
    for conversation in active:
        if conversation in mine:
            moving.add(conversation)
        elif conversation in inner:
            moving.add(inner[conversation])
    return moving
