"""规则这一域处处要用的那几个数与钟：超时、事件上限、触发标签，和 ``now()``。

这些名字原来定义在 ``routine/service.py`` 的顶上，拆包时原样搬到这里：子模块按
名字取用，包 ``app.domain.routine.service`` 再导出一遍，所以
``from app.domain.routine.service import REPORT_TIMEOUT`` 照旧答得出。

这里是叶子 —— 它只依赖 ``app.core.config`` 与 ``routine.models``，任何子模块都可以
import 它而不成环。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.domain.routine.models import RoutineTrigger

#: A planned moment found later than this was missed (platform down), not late.
MISSED_GRACE = timedelta(minutes=15)
#: A queued run whose turn never began is reported as not started after this.
START_TIMEOUT = timedelta(hours=2)
#: How long a delivered run may go without a report before it counts as failed.
#: A turn row cannot say when the work ended: in a live session every input's
#: row is delivered and stopped within milliseconds, and the business backend
#: does not see whether the device's session is still busy. So the only clock
#: is the longest a turn may run at all, plus a little for the report to land;
#: a real failure is caught sooner by the room's own turn-failed notice.
REPORT_TIMEOUT = timedelta(seconds=settings.agent_turn_hard_ceiling_s) + timedelta(
    minutes=5
)
NO_REPORT = "AI 队友一直没有交回结果"

#: Loop breaker: an event rule fires at most this often per hour.
EVENT_RUNS_PER_HOUR = 6

EVENT_TRIGGERS = frozenset(
    {
        RoutineTrigger.library_file_added,
        RoutineTrigger.task_closed,
        RoutineTrigger.card_accepted,
    }
)

TRIGGER_LABELS = {
    RoutineTrigger.schedule: "定时",
    RoutineTrigger.library_file_added: "资料库新增文件",
    RoutineTrigger.task_closed: "任务关闭",
    RoutineTrigger.card_accepted: "成果被采纳",
}


def now() -> datetime:
    return datetime.now(UTC)


NO_FEEDBACK_WAITING = "没有待分诊的反馈，这一次不执行"
