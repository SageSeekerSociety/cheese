"""定时与触发规则：建规则、到点让它响、结算每一次执行。

一次执行是队友在频道主线上的一条消息，活本身发生在那条消息的支线里，追问回到
同一个会话。响的时候，执行行、那条消息、支线的开口行、交给队友的投递在同一个
事务里写好；送不送得到由投递台账负责。一次执行的结束，要么是队友自己交回结果，
要么是这一轮压根没开始或开始了没有回音 —— 后者由投递台账和轮次记录判出来。结算
之后那条消息写明它跑得怎么样，并且按跑完的时刻沉到主线底下。

这个包原是一个 1078 行的模块，按职责拆成下面几块：

* ``_shared`` —— 处处要用的那几个数与钟（超时、每小时事件上限、触发标签、``now``）。
  叶子，只依赖 ``app.core.config`` 与 ``routine.models``。
* ``rules`` —— 规则自己那一行：草稿、确认、启停、改配置、删，和一次手动执行。
* ``scheduling`` —— 现在该不该响：到点的按时刻挑，事件类的按游标挑。
* ``firing`` —— 响了之后：执行行、主线那条消息、交给队友的提示词。
* ``settlement`` —— 一次执行怎么结束：队友交回的结果，和它没交回时的判据。
* ``archive`` —— 房间归档了，它名下还在跑的规则要说一句话，且只说一次。
* ``sweep`` —— 30 秒一趟的时针，把上面几件事按顺序跑一遍，再推消息。
* ``service`` —— ``RoutineService`` 本身：只存 ``_session``，方法全是一句转发。

``RoutineService`` 的每个方法只转发，不作决定；决定在它转发到的那个模块里，一个
决定只写一遍。包门面（这里）把拆之前那份模块**自己定义**的名字再导出一遍，所以
``app.domain.routine.service`` 这个导入路径、``RoutineService`` 这个类对象、
以及按模块属性取的 patch 点（``periodic.py`` 的 ``service.sweep``、单元测试里的
``"app.domain.routine.service.sweep"``）都照旧。

拆成**包**而不是几个同级模块，是为了不欠新债：子模块 import 父包这条边契约本来
就不算环，同级模块互相 import 才会计入 C3（``sibling domains are acyclic``）。
形状和 ``app/domain/review/services/``、``app/domain/feedback/services/`` 一样。
"""

from __future__ import annotations

# 这不是本域定义的名字，是拆之前那份 `service.py` 从 `delivery.agent` 那里 import
# 进来的一个：它活在旧模块的名字空间里，路由就顺手按模块属性取了它
# （`routines.dispatch_pending`，手动执行那一条）。拆成包之后这个名字得留在这里，
# 否则那条路是 AttributeError。
from app.domain.delivery.agent import dispatch_pending as dispatch_pending

# 兼容门面：拆包之后各处可从 ``app.domain.routine.service`` 取到的名字 —— 八个常量、
# ``now``、名字与 spec 两个助手（``name_of`` / ``validate_spec``）、``RoutineService``、
# 九个函数，外加它当时 import 进来、被别人按模块属性取用的 ``dispatch_pending``。
# 除了上面这一个，它当时只是 import
# 进来、自己并不定义的名字（本域的 ``Routine``、``RoutineRun``，跨域的 ``Block``、
# ``Topic`` 都在内）不再从这条路取，仓库里也没有调用方用它们。每个都写成 ``X as X``
# 显式再导出，``RoutineService`` 仍是同一个类对象。
from app.domain.routine.service._shared import (
    EVENT_RUNS_PER_HOUR as EVENT_RUNS_PER_HOUR,
)
from app.domain.routine.service._shared import EVENT_TRIGGERS as EVENT_TRIGGERS
from app.domain.routine.service._shared import MISSED_GRACE as MISSED_GRACE
from app.domain.routine.service._shared import (
    NO_FEEDBACK_WAITING as NO_FEEDBACK_WAITING,
)
from app.domain.routine.service._shared import NO_REPORT as NO_REPORT
from app.domain.routine.service._shared import REPORT_TIMEOUT as REPORT_TIMEOUT
from app.domain.routine.service._shared import START_TIMEOUT as START_TIMEOUT
from app.domain.routine.service._shared import TRIGGER_LABELS as TRIGGER_LABELS
from app.domain.routine.service._shared import now as now
from app.domain.routine.service.firing import _fire as _fire
from app.domain.routine.service.firing import run_prompt as run_prompt
from app.domain.routine.service.rules import name_of as name_of
from app.domain.routine.service.rules import validate_spec as validate_spec
from app.domain.routine.service.scheduling import _events_for as _events_for
from app.domain.routine.service.scheduling import _fire_events as _fire_events
from app.domain.routine.service.scheduling import _fire_schedules as _fire_schedules
from app.domain.routine.service.service import RoutineService as RoutineService
from app.domain.routine.service.settlement import (
    _announce_finished as _announce_finished,
)
from app.domain.routine.service.settlement import _settle_open_runs as _settle_open_runs
from app.domain.routine.service.settlement import _turn_failure as _turn_failure
from app.domain.routine.service.sweep import (
    publish_run_messages as publish_run_messages,
)
from app.domain.routine.service.sweep import sweep as sweep
