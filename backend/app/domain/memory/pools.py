"""这一轮该读哪几个记忆池。

回忆规则以前长在 `chat.py` 的一条分支里：私聊读那个人的池，别的房间不读。结论 54
把它改成「agent 总是有关于每个人的知识，任何房间都可以用」，所以「读谁的池」不再
是房间类型的函数，而是「这一轮跟谁在一起」的函数——这个文件就是那个函数。
"""

import uuid
from collections.abc import Iterable

from app.domain.memory.models import (
    MemoryScope,
    agent_project_scope_id,
    user_scope_id,
)


def pools_for_turn(
    project_id: uuid.UUID | str,
    agent_handle: str,
    people_present: Iterable[str],
) -> list[tuple[MemoryScope, str]]:
    """一位 agent 这一轮读得到的池，按注入顺序：先自己的，再一人一份。

    **不过滤。**在场的人各读一份，一份都不因为房间是什么类型而被拿掉——私聊不再
    是特例（结论 54）。这里之所以还要知道有谁在场，是因为池的清单要有限：一个项目
    里的人可以很多，而这一轮要用到的是跟它同席的那几位。

    跨项目读不到：每个 scope_id 都以这个项目的 id 开头，别的项目的池在这个清单里
    根本拼不出来（结论 8）。

    名册给什么就照单拼什么：两个调用者（`chat.py` 的召回、`api/routes/projects.py`
    的 `cheese recall`）传进来的都是 `people_handles` 读的那一份名册，而
    `topic_memberships` 上有 `(topic_id, member_handle)` 的唯一约束、``member_handle``
    非空——去重和空 handle 那两道挡在这里挡不到任何东西。
    """
    pools = [
        (MemoryScope.agent_project, agent_project_scope_id(project_id, agent_handle))
    ]
    for person in people_present:
        pools.append(
            (MemoryScope.user, user_scope_id(project_id, agent_handle, person))
        )
    return pools
