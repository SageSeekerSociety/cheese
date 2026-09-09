"""流程阶段判定（按阶段渐进式披露操作说明）。

一个话题在任一时刻处在流程的某一段：房间在拆活、一件事在做、闸门在跑、
等人采纳、PR 开着在迭代、撞了冲突、已经合并。**每一段该知道的东西不一样**，
把整条流程一次性灌进 system prompt 既浪费 token 又淹没重点。

这里把「当前处在哪一段」算成一个 :class:`TopicStage`，`chat.py` 据此只注入
对应那一段的 skill（见 ``skills.load_scenario``）。

关键设计取舍：**渐进的是「平台注入哪一段」，不是「模型决定读哪一段」。**
Agent Skills 的懒加载不可靠（弱模型不会自己去 read 那个文件，见
``skills.load_cheese_cli_rules`` 的 docstring），所以这里算出来的那一段
依旧是**静态拼进 system prompt** 的，模型没有「要不要读」的选择权。

判定只用**已经在手的**事实——话题的 kind/status，以及 `chat.py` 早就为
「盲飞防护」查出来的 open 验收卡列表——不额外打一次库。
"""

import enum
from collections.abc import Iterable

from app.domain.review.models import AcceptStatus


class TopicStage(enum.StrEnum):
    """话题当前所处的流程阶段。值同时是 skill 的 scenario 标签后缀。"""

    # 还没递卡：房间在派活、在干活，两件事是同一段。任务=分身之后跑轮次的只有
    # 房间，所以「拆活」和「干活」不再是两个地点的两种处境，而是同一个地点同时
    # 在做的两件事 —— 分开注入等于让房间在派活时读不到该怎么交付。
    delegating = "delegating"
    # 机器闸门在跑 / 刚红。
    gate = "gate"
    # 卡在等人采纳（PR 已在递卡时开出，采纳即当场合并，#718）。
    awaiting = "awaiting"
    # 采纳时撞上合并冲突，等芝士解决。
    conflict = "conflict"
    # 已合并归档。
    archived = "archived"


# 卡状态 → 阶段。`gate_failed` 和 `pending_gate` 合并成一段：闸门红了要做的事
# （读失败输出、修、重新递卡）和闸门正在跑时该知道的事（它只跑 lint）是同一段
# 知识，分成两段只会重复。
_CARD_STAGE = {
    AcceptStatus.conflict: TopicStage.conflict,
    AcceptStatus.gate_failed: TopicStage.gate,
    AcceptStatus.gate_blocked: TopicStage.gate,
    AcceptStatus.pending_gate: TopicStage.gate,
    AcceptStatus.pending: TopicStage.awaiting,
}

# 同时存在多张 open 卡时，谁说了算。越靠前越「需要芝士现在动手」：冲突和红闸门
# 是在等芝士干活，pending 只是在等人——所以等人的排最后。
_CARD_PRECEDENCE = (
    AcceptStatus.conflict,
    AcceptStatus.gate_failed,
    AcceptStatus.gate_blocked,
    AcceptStatus.pending_gate,
    AcceptStatus.pending,
)


def resolve_stage(
    *,
    finished: bool,
    card_statuses: Iterable[AcceptStatus] = (),
) -> TopicStage:
    """算出这个地点当前所处的流程阶段。

    卡状态优先：一张活着的卡说明「现在正卡在流程的某一环」，比「还没递卡」具体，
    也更该被告知。没有活卡时才退回到那两种收尾状态。

    不再问「这是房间还是一件活」：跑轮次的只有房间了（一条活是房间会话里的一个
    分身，它没有自己的会话，也就没有 system prompt 可注入）。
    """
    present = set(card_statuses)
    for candidate in _CARD_PRECEDENCE:
        if candidate in present:
            return _CARD_STAGE[candidate]
    if finished:
        return TopicStage.archived
    return TopicStage.delegating


def stage_scenario(stage: TopicStage) -> str:
    """阶段 → skill frontmatter 里的 `scenarios:` 标签。

    带 ``stage:`` 前缀，跟已有的场景标签（`accept`/`heartbeat`/…）分开命名空间，
    这样一个 skill 想同时服务多个阶段只要多写一个标签即可。
    """
    return f"stage:{stage.value}"
