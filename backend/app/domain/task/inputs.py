"""题目域收到的**输入**：把接口形状的字符串与自由 dict 折成领域里的值。

这一层不碰 FastAPI / Starlette，也不碰任何别的领域的 repository —— 它只是把
``routes/tasks.py`` 里那几张「提交者类型 / 审批类型」的字符串映射搬下来，好让
路由函数只留 transport 那一截。
"""

from app.core.errors import BadRequestError
from app.core.sentences import say

# ``SubmitterType`` 那两个序号：报名是人报的还是小队报的。领域代码判这一点时读这里，
# 不再各自写一遍字面量。
SUBMITTER_USER = 0
SUBMITTER_TEAM = 1

# ``ApproveType`` 的三个序号。``APPROVE_APPROVED`` 是**唯一**算过审的那个。
APPROVE_APPROVED = 0
APPROVE_DISAPPROVED = 1
APPROVE_NONE = 2

# How a claim starts (``Task.start_mode``). ``DIRECT``: the claim is approved the
# moment it is made and its project opens then; the deadline is the task's own,
# shared by everyone. ``APPLY``: the claim waits for the publisher, and its
# project opens and its deadline (``default_deadline`` days) starts when it is
# approved.
START_DIRECT = "DIRECT"
START_APPLY = "APPLY"
START_MODES = (START_DIRECT, START_APPLY)


def map_submitter_type(value: str) -> int:
    """Map TaskSubmitterTypeDTO string to smallint ordinal."""
    mapping = {
        "USER": SUBMITTER_USER,
        "TEAM": SUBMITTER_TEAM,
    }
    if value not in mapping:
        raise BadRequestError(f"Invalid submitterType: {value}")
    return mapping[value]


def map_approve_type(value: str) -> int:
    """Map ApproveTypeDTO string to smallint ordinal."""
    mapping = {
        "APPROVED": APPROVE_APPROVED,
        "DISAPPROVED": APPROVE_DISAPPROVED,
        "NONE": APPROVE_NONE,
    }
    upper = value.upper()
    if upper not in mapping:
        raise BadRequestError(f"Invalid approved value: {value}")
    return mapping[upper]


def map_approve_type_to_int(value: str | None) -> int | None:
    if value is None:
        return None
    return map_approve_type(value)


def map_start_mode(value: str) -> str:
    """A start mode as the API names it; anything else is refused."""
    if value not in START_MODES:
        raise BadRequestError(say("taskStartModeInvalid"))
    return value
