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

    # 房间：它自己不带分支和验收卡，它的活是派成支线去做。
    delegating = "delegating"
    # 一件事正在做：有分支、还没递卡（或卡被打回后回到这里）。
    working = "working"
    # 机器闸门在跑 / 刚红。
    gate = "gate"
    # 闸门过了，卡在等人采纳。
    awaiting = "awaiting"
    # 两阶段采纳：PR 已开，采纳人的 token 通道已打开，还在迭代。
    pr_open = "pr_open"
    # 采纳时撞上合并冲突，等芝士解决。
    conflict = "conflict"
    # 已合并归档。
    merged = "merged"


# 卡状态 → 阶段。`gate_failed` 和 `pending_gate` 合并成一段：闸门红了要做的事
# （读失败输出、修、重新递卡）和闸门正在跑时该知道的事（它只跑 lint）是同一段
# 知识，分成两段只会重复。
_CARD_STAGE = {
    AcceptStatus.conflict: TopicStage.conflict,
    AcceptStatus.gate_failed: TopicStage.gate,
    AcceptStatus.gate_blocked: TopicStage.gate,
    AcceptStatus.pending_gate: TopicStage.gate,
    AcceptStatus.pr_open: TopicStage.pr_open,
    AcceptStatus.pending: TopicStage.awaiting,
}

# 同时存在多张 open 卡时，谁说了算。越靠前越「需要芝士现在动手」：冲突和红闸门
# 是在等芝士干活，pr_open 是通道开着还能干活，pending 只是在等人——所以等人的
# 排最后。
_CARD_PRECEDENCE = (
    AcceptStatus.conflict,
    AcceptStatus.gate_failed,
    AcceptStatus.gate_blocked,
    AcceptStatus.pending_gate,
    AcceptStatus.pr_open,
    AcceptStatus.pending,
)


def resolve_stage(
    *,
    is_room: bool,
    finished: bool,
    card_statuses: Iterable[AcceptStatus] = (),
) -> TopicStage:
    """算出这个地点当前所处的流程阶段。

    卡状态优先于地点形态：一张活着的卡说明「现在正卡在流程的某一环」，比
    「这是个房间还是一件活」更具体、更该被告知。没有活卡时才退回到形态判断。

    问的是 `is_room` 而不是 `kind`：一件活已经不是 `topics` 表里的一行了，
    `topics.kind` 从此永远回答「房间」。照着它算，每一条支线都会被当成房间、
    拿到「拆活」那一段说明——分身会去拆它本该自己做的事。**这个错不报错。**
    """
    present = set(card_statuses)
    for candidate in _CARD_PRECEDENCE:
        if candidate in present:
            return _CARD_STAGE[candidate]
    if finished:
        return TopicStage.merged
    return TopicStage.delegating if is_room else TopicStage.working


def stage_scenario(stage: TopicStage) -> str:
    """阶段 → skill frontmatter 里的 `scenarios:` 标签。

    带 ``stage:`` 前缀，跟已有的场景标签（`accept`/`heartbeat`/…）分开命名空间，
    这样一个 skill 想同时服务多个阶段只要多写一个标签即可。
    """
    return f"stage:{stage.value}"
