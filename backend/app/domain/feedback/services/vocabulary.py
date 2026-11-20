"""反馈的封闭词表 —— 路由、服务与前端控件共用的那几个集合。

从 `services.py` 挪出来的一处，只因为它的读侧和写侧都要用：`reads` 校验
`tab` / `sort`，`writes` 拿 `STATUS_LADDER` 判「只能往前走」。定义留一份，
`app.domain.feedback.services` 仍然逐个再导出（路由是那么读的）。
"""

from __future__ import annotations

from app.domain.feedback.models import FeedbackStatus

#: The admin surface's tiers, in the order the admin tab bar draws them.
ADMIN_TABS: tuple[str, ...] = ("public", "private", "agent", "security")

#: The public tabs, in the order the tab bar draws them. Three, not four:
#: `active` went on 2026-10-28 because it is the same cut as the status pill of
#: the same name (「处理中」), and the pills came later — two controls on one
#: axis, and the tab's own 0 read as 「这一栏是空的」 rather than 「这一栏没人用」.
#: Narrowing to one rung is the pills' job now. See `_tab_where`.
PUBLIC_TABS: tuple[str, ...] = ("all", "hot", "resolved")

#: The two orderings any list can be asked for. `new` is the default everywhere;
#: `supports` is what the public `hot` tab implies and what the admin console's
#: 最新/最热 toggle asks for by name. One tuple because the serializer
#: (`_list_stmt`) has exactly two branches: a third name here would be a value
#: that silently means `new`.
SORTS: tuple[str, ...] = ("new", "supports")

#: Movement order shown by the status ladder. One rung per thing the person who
#: filed it can see happen: 收录 → 处理 → 解决 → 部署. `resolved` and `deployed`
#: are reachable from any state, and a reopen (resolved → in_progress) is
#: allowed: reports do get re-opened, and a status set that forbids it gets
#: worked around by filing a duplicate instead — which loses the history that
#: makes the report useful. `declined` (不修复) is not on it: it is the other way
#: out, not a fifth rung, and the reporter's ladder ends there instead.
STATUS_LADDER: tuple[FeedbackStatus, ...] = (
    FeedbackStatus.received,
    FeedbackStatus.in_progress,
    FeedbackStatus.resolved,
    FeedbackStatus.deployed,
)
