"""递给 `review` 的那几个口子，测试也从同一个地方取。

`review`（第 4 层）够不着第 3 层的 `delivery` / `agent_instance`
（`app/domain/review/ports.py`）：结掉一张卡等着的那些话、给任务自己的席位留一句
指令、认一个席位是谁、往席位那本报账上记一行、放行投递 —— 这五件事都由门递进去。
生产上递的就是这里的这几个（`app/periodic_jobs.py`、`app/api/routes/accept.py`
接线的那几行），测试直接调这些函数时递同样的，被测的才是生产那条路。
"""

from app.domain.agent_instance.adapters import task_teammate
from app.domain.delivery.agent import (
    dispatch_pending as _dispatch_pending,
)
from app.domain.delivery.agent import (
    record_agent,
    record_task_instruction,
    seat_instance_id,
)
from app.domain.delivery.ledger import settle_card

__all__ = [
    "pumping",
    "record_agent",
    "record_task_instruction",
    "seat_instance_id",
    "settle_card",
    "task_teammate",
]


def pumping(chat, runner=None):
    """`DispatchPending`：不带参数的那一下「现在放」。

    生产里它是 `app/periodic_jobs.py` 里的一个闭包 —— 会话工厂、房间服务和跑机器
    在接线时就定好了，tick 到的时候只差这一个动作。这里照做一份，递进去的和它同形。
    ``runner`` 不填就用 ``chat.work_runner``。
    """

    async def dispatch_pending():
        return await _dispatch_pending(
            chat.session_factory,
            chat=chat,
            runner=chat.work_runner if runner is None else runner,
        )

    return dispatch_pending
