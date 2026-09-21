"""撞上项目档位策略的那次调用，产物是给人的一条提议（结论 3 后半、结论 40 后半）。

## 一个闸门，两个调用者

「要一台自托管机器」和「这一轮用一个更贵的模型」看起来是两件事，对平台却是同一
件：**一次要花别人东西的调用，项目策略说它不该自己发生。** 产物不是报错，也不是
挂在那里等——是一条给人的提议，下一步在那个人手上。

两个调用者刚好是两个，所以这里是一个抽象而不是两段 if：

- 轮次组装（`agent/chat.py`）——这一轮要用的模型（`room_task/binding.py` 解析出来
  的那个）落在哪一档；
- 换算力（`api/routes/topics.py` 的 `set_topic_compute_profile`）——要的是自托管的
  那台机器还是 Cloud。

## 档位是资源的一个事实，写在目录里

结论 3 说模型是「工作占用的资源，与机器（地点）同形」，所以两者共用一套档位的词，
而且那些词只在**目录**里声明一次（`agent/market.py`）：订阅模型在 `_SUB_MODELS`
的那一列，算力池在 `COMPUTE_TIERS`。这里只做比较，不认识任何一个型号——策略说的是
「哪几档可以自己发生」，不是一张型号白名单。白名单的毛病是它会过期：目录里加一个
新型号，白名单不认识它，于是最贵的那个新模型反而随便用。

## 三个答案，其中一个是抛出去的

    档内            → Allowed
    超档 + 变提议   → Proposal
    超档 + 拒绝     → 抛 OverTier

拒绝抛出去而不是返回，是为了让它和「解析不出模型」在调用点是同一种东西
（`binding.resolve` 对一个项目用不了的模型就是抛 `ValidationError`，不变量 I27）。
两者都得是一次**看得见的**拒绝：悄悄降到下一档，屏幕上写着 Opus、跑的是 Sonnet，
而谁也不知道什么时候换的——那正是 I27 要杀掉的失败。

## 默认与今天等价

项目没说话时：不限档、超档拒绝。不限档意味着这个闸门对今天所有项目透明，所以部署
窗口里新旧两份代码读同一份设置得到同一个行为。

要让「自托管机器要机主同意」（结论 40）生效，项目把 `byo` 从允许档位里去掉，处置
写成 `propose`；此后要那台机器的调用会变成一条提议，收件人是机主本人。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final

from app.core.errors import ValidationError

#: 项目设置里的两个键。允许的档位缺席 = 不限档；处置缺席 = 拒绝。
ALLOWED_TIERS_KEY: Final = "allowed_tiers"
OVER_TIER_KEY: Final = "over_tier"

#: 超档的处置。`deny` 是默认，也是今天的行为。
DENY: Final = "deny"
PROPOSE: Final = "propose"
DISPOSITIONS: Final = frozenset({DENY, PROPOSE})


class Resource(enum.StrEnum):
    """闸门管的两种资源。两个调用者各一种。"""

    model = "model"
    machine = "machine"


@dataclass(frozen=True, slots=True)
class Policy:
    """项目对「花多少钱的调用可以自己发生」的两句话。

    `allowed_tiers` 是 `None` 而不是空集合时才叫不限档：空集合是「一档都不许」，
    和「没说过」不是一个意思，而它们对每一次调用的答案正好相反。
    """

    allowed_tiers: frozenset[str] | None = None
    over_tier: str = DENY


def policy_of(project_settings: dict | None) -> Policy:
    """从项目设置读出这两句话。读不懂的值按默认算。

    一个拼错的设置不能让项目下不去线——和 `agent/supply.py` 的 `resolve_pool` 同
    一条规矩。默认这一档又恰好等于今天的行为，所以读不懂时退回去的是「照旧」。
    """
    raw = (project_settings or {}).get(ALLOWED_TIERS_KEY)
    allowed: frozenset[str] | None = None
    if isinstance(raw, list | tuple | set | frozenset):
        allowed = frozenset(
            item.strip() for item in raw if isinstance(item, str) and item.strip()
        )
    disposition = (project_settings or {}).get(OVER_TIER_KEY)
    return Policy(
        allowed_tiers=allowed,
        over_tier=disposition if disposition in DISPOSITIONS else DENY,
    )


@dataclass(frozen=True, slots=True)
class Call:
    """一次要过闸门的调用。

    `approver` 是**谁点头**，由调用点填：自托管机器是那台机器的机主，别的是项目
    的主人。闸门不替它决定这个——谁有权点头是一件关于身份和所有权的事实，闸门只
    答「这次调用要不要人点头」。
    """

    resource: Resource
    #: 目录里的 id：模型 id 或算力池 id。
    subject: str
    #: 给人看的名字，进提议那句话。
    label: str
    tier: str
    approver: str


@dataclass(frozen=True, slots=True)
class Allowed:
    """档内，照常发生。"""

    call: Call


@dataclass(frozen=True, slots=True)
class Proposal:
    """超档，产物是给人的一条提议——这次调用**没有发生**。

    `content` 就是房间里要落下的那一行，也是通知里那句话：人在两处读到的是同一句
    （`agent/announce.py` 的规矩）。
    """

    call: Call
    #: 发起这次调用的参与者（agent 或人）。
    asked_by: str
    content: str

    @property
    def approver(self) -> str:
        return self.call.approver


class OverTier(ValidationError):
    """超档且项目的处置是拒绝——一次说得出口的拒绝，不是悄悄降档（I27）。"""


def check(call: Call, policy: Policy, actor: str) -> Allowed | Proposal:
    """这次调用可以自己发生吗。

    **全仓只有这一处回答「超档怎么办」。** 调用点问的是这一句，不自己判；
    `tests/unit/test_policy_gate.py` 里有一条守卫盯着这件事。
    """
    if policy.allowed_tiers is None or call.tier in policy.allowed_tiers:
        return Allowed(call)
    if policy.over_tier == PROPOSE:
        return Proposal(call=call, asked_by=actor, content=_proposal_line(call, actor))
    raise OverTier(_refusal_line(call))


_WHAT: Final[dict[Resource, str]] = {
    Resource.model: "模型",
    Resource.machine: "算力",
}


def _proposal_line(call: Call, actor: str) -> str:
    return (
        f"{actor} 要用{_WHAT[call.resource]}「{call.label}」，"
        f"超出本项目允许的档位（{call.tier}）；这一步等 @{call.approver} 点头。"
    )


def _refusal_line(call: Call) -> str:
    return (
        f"{_WHAT[call.resource]}「{call.label}」属于 {call.tier} 档，"
        "不在本项目允许的档位内；请改用档内的资源，或让项目管理者调整档位策略。"
    )
