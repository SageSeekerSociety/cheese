"""看板每一行要的两位「此刻还在不在」，一次问完。

一行说自己在「运行中」，靠的是两句话：**做它的那个分身还在**、**它住的房间里
那个屏幕还在**。两句问的都是此刻，没有一列存着它们 —— 所以每一行都得从跑轮次的
进程手里现问，而看板一次要画一整个项目的行。

## 分身那一位有两处证据

`ChatService.worker_live` 是**单个后端进程的内存**，而它会忘，忘的三种情形都和
「那个分身死没死」无关：

- 后端重启一次（dev 每合一次 main 就部署一次），那份认领随进程没了。房间的屏幕
  会自己回来 —— 它是一条连接，重连就重新报到 —— 而分身不会：它只在开工那一刻
  （`SubagentStart`）报一次到，之后没人替它再说一遍。
- 房间的会话换成了另一个（`_note_room_session`）：那间房攒下的认领一次全弹掉。
- 分身交回一次话（`SubagentStop`）：收回声明，而它可能只是把一条长命令停到了
  后台，等会儿接着干。

这三种情形下只看内存，一条正在埋头干活的活会退回那条时间戳规则 —— 十分钟没有
block 就说它失联（`presentation.LOST_SIGNAL_AFTER`）。而一个埋头干了四十分钟、
一个 block 都没吐的分身，和一个同样安静的死分身，在时间戳上长得一模一样。

持久的那一处是**这条活开工的那一轮还没收尾**：`Task.execution_turn_id` 那一行还
开着（`AgentTurnRepository.open_of`）。它不是从沉默里推出来的 —— 关掉区间的是
知道区别的那一个，孤儿扫描，在容器或轮次真的死掉的时候。它答不出来（没有开工
轮次，或那一轮已经收尾）才轮回那条老规矩。

屏幕那一位仍然是先看的：房间的屏幕没了，它派出去的分身一定也没了 —— 那一道判据
在 `presentation.task_presentation` 里，这里只负责把事实喂进去。
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.chat import ChatService
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.room_task.models import Task


@dataclass(frozen=True, slots=True)
class Liveness:
    """一行活此刻的两位事实。都不是库里的一列，所以只能从外面喂。

    刻意做成一行一份而不是两个并排的字典：这两个答案问的是同一批行，调用方该拿到
    的也是同一批行。
    """

    #: 它住的房间那个屏幕还在不在（`ChatService.has_live_screen`）。
    screen: bool
    #: 做它的那个分身还在不在。True = 有人报过它还在做（进程记得，或它开工那一轮
    #: 还开着）；None = 关于它一个字都没有过。False 今天没有来源。
    worker: bool | None


async def task_liveness(
    chat: ChatService, session: AsyncSession, tasks: Sequence[Task]
) -> dict[uuid.UUID, Liveness]:
    """按活 id 给出每一行的两位事实。

    `worker` 为 True 的两种来源见模块开头：进程记得它还在做，或者它开工那一轮还
    开着。两处都答不出来才是 None —— 那时候看板按时间戳那条老规矩说，也就是今天
    的样子。False 今天没有来源。
    """
    if not tasks:
        return {}
    screens = {t.room_id: chat.has_live_screen(t.room_id) for t in tasks}
    claims = {t.id: chat.worker_live(t.room_id, t.subagent_id) for t in tasks}
    # 内存答不上话的那几条，一次问完它们的开工轮次还开不开 —— 一条活不单独查一次。
    asked = [
        t.execution_turn_id
        for t in tasks
        if claims[t.id] is None and t.execution_turn_id is not None
    ]
    still_open = await AgentTurnRepository(session).open_of(asked) if asked else set()
    out: dict[uuid.UUID, Liveness] = {}
    for task in tasks:
        started_here = claims[task.id] is None and task.execution_turn_id in still_open
        out[task.id] = Liveness(
            screen=screens[task.room_id],
            worker=True if started_here else claims[task.id],
        )
    return out
