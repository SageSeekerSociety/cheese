"""守卫：活、轮次和子 agent 的数据结构上没有地点（不变量 I18b）。

手是 agent 的，不是房间的、更不是一条活的（结论 60）：地点由做这条活的那个 agent
的**会话**解析出来，原生子 agent 用的是父进程那一份（结论 43）。所以「这条活在哪
台机器上」这个问题在这几张结构上没有答案位可以写 —— 一旦有了，两处会各说各的，而
解析的时候没有任何地方能说出该听谁的。

这一条要拦的不是今天的代码，是下一次：加一列 `place_id` 到卡上，比起改地点解析，
永远是更快的那条路，而且它能跑通一次 —— 真正的代价要等到第二个 agent 进同一个房
间、或者一条活被迁到另一台机器上才露面。所以这里对着字段名认，不对着行为认。

`lease` 一并拦下，理由同一条：一份租约写在活上，就是这条活自己租了一台机器。
`agent_sessions` 不在扫描范围内 —— 会话**应当**有地点，那两列正是它的。
"""

import dataclasses

from app.domain.agent.models import AgentTurn
from app.domain.agent.service import AgentSubagentStart, AgentSubagentStop
from app.domain.room_task.models import Task
from app.domain.room_task.schemas import TaskOut

# 按 `_` 切词整词认，不用子串：`released_at` 里有 "lease"、`replaced_at` 里有
# "place"，子串匹配会让这条守卫红在一个跟地点毫无关系的字段上，而一条会误报的守卫，
# 第一个撞上的人改的是守卫不是代码 —— 改完它就再也拦不住 `place_id` 了。
_FORBIDDEN = frozenset(
    {"place", "places", "placement", "placements", "lease", "leases"}
)


def _offending(names) -> list[str]:
    return [name for name in names if set(name.lower().split("_")) & _FORBIDDEN]


def test_a_piece_of_work_has_no_place_of_its_own():
    """卡上没有地点：一条活的工作树在做它的那个 agent 手上。"""
    assert _offending(column.name for column in Task.__table__.columns) == []


def test_what_a_card_shows_has_no_place_either():
    """读侧同理 —— 接口上多一个字段，客户端就会开始按它渲染。"""
    assert _offending(TaskOut.model_fields) == []


def test_a_turn_has_no_place_of_its_own():
    """一轮只持有从会话那两列解析出来的租约句柄，自己不记地点。"""
    assert _offending(column.name for column in AgentTurn.__table__.columns) == []


def test_a_subagent_declares_no_environment():
    """子 agent 不声明独立环境：它跑在父进程里，手就是父进程那双。"""
    for event in (AgentSubagentStart, AgentSubagentStop):
        assert _offending(f.name for f in dataclasses.fields(event)) == [], event


def test_the_guard_reads_whole_words():
    """守卫认整词：一个带 lease/place 字样但与地点无关的字段名不该让它红。"""
    assert _offending(["released_at", "replaced_at", "replace_reason"]) == []
    assert _offending(["place_id", "placement", "work_lease"]) == [
        "place_id",
        "placement",
        "work_lease",
    ]
