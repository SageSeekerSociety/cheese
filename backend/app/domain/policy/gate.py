"""撞上项目档位策略的那次调用，产物是给人的一条提议（结论 3 后半、结论 40 后半）。

## 闸门管的是模型

这一轮要用的模型（`room_task/binding.py` 解析出来的那个）落在哪一档，轮次组装
（`agent/chat.py`）在占用之前问这里。产物不是报错，也不是挂在那里等——是一条给人
的提议，下一步在那个人手上。执行环境不过这道闸门：它是每位 AI 队友自己的，花不花
项目的钱由沙箱的规格说（`agent/environment.py`），钱不够在放沙箱的那一刻拒绝。

## 档位是资源的一个事实，写在目录里

档位的词只在**目录**里声明一次（`agent/market.py` 的 `_SUB_MODELS` 那一列）。这里
只做比较，不认识任何一个型号——策略说的是「哪几档可以自己发生」，不是一张型号白名
单。白名单的毛病是它会过期：目录里加一个新型号，白名单不认识它，于是最贵的那个新
模型反而随便用。

## 三个答案，其中一个是抛出去的

    档内            → Allowed
    超档 + 变提议   → Proposal
    超档 + 拒绝     → 抛 OverTier

拒绝抛出去而不是返回，是为了让它和「解析不出模型」在调用点是同一种东西
（`binding.resolve` 对一个项目用不了的模型就是抛 `ValidationError`，不变量 I27）。
两者都得是一次**看得见的**拒绝：悄悄降到下一档，屏幕上写着 Opus、跑的是 Sonnet，
而谁也不知道什么时候换的——那正是 I27 要杀掉的失败。

## 默认与今天等价

项目没说话时：不限档、超档拒绝。不限档意味着这个闸门对今天所有项目透明。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final

from app.core.errors import ValidationError
from app.core.sentences import say

#: 项目设置里的两个键。允许的档位缺席 = 不限档；处置缺席 = 拒绝。
ALLOWED_TIERS_KEY: Final = "allowed_tiers"
OVER_TIER_KEY: Final = "over_tier"

#: 超档的处置。`deny` 是默认，也是今天的行为。
DENY: Final = "deny"
PROPOSE: Final = "propose"
DISPOSITIONS: Final = frozenset({DENY, PROPOSE})


class Resource(enum.StrEnum):
    """闸门管的资源。"""

    model = "model"


@dataclass(frozen=True, slots=True)
class Policy:
    """项目对「花多少钱的调用可以自己发生」的两句话。

    `allowed_tiers` 是 `None` 而不是空集合时才叫不限档：空集合是「一档都不许」，
    和「没说过」不是一个意思，而它们对每一次调用的答案正好相反。
    """

    allowed_tiers: frozenset[str] | None = None
    over_tier: str = DENY
    #: 团队方案允许的模型档位（#2397），`None` 是不限。只管模型，不管机器；项目
    #: 只能在它里面再收紧。
    plan_model_tiers: frozenset[str] | None = None

    @property
    def lets_everything_through(self) -> bool:
        """不限档：这份策略对每一次调用的判决都是 `Allowed`。

        调用点据此跳过**把那次调用写出来**那一步——机器那一侧要列一遍项目设备、
        列一遍 host health、再取一次机主，而不限档时这几条查询的答案不影响判决，
        它们又落在每一轮都走的那条路上。跳过的只是构造：判决仍然只有 `check` 这
        一处在做。
        """
        return self.allowed_tiers is None


def policy_of(
    project_settings: dict | None, plan_model_tiers: frozenset[str] | None = None
) -> Policy:
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
        plan_model_tiers=plan_model_tiers,
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


class OverPlan(OverTier):
    """模型不在团队方案允许的档位内。总是拒绝：项目主人点头也解除不了方案。"""


def check(call: Call, policy: Policy, actor: str) -> Allowed | Proposal:
    """这次调用可以自己发生吗。

    **全仓只有这一处回答「超档怎么办」。** 调用点问的是这一句，不自己判；
    `tests/unit/test_policy_gate.py` 里有一条守卫盯着这件事。
    """
    if (
        call.resource == Resource.model
        and policy.plan_model_tiers is not None
        and call.tier not in policy.plan_model_tiers
    ):
        raise OverPlan(say("modelNotInPlan", label=call.label))
    if policy.allowed_tiers is None or call.tier in policy.allowed_tiers:
        return Allowed(call)
    if policy.over_tier == PROPOSE:
        return Proposal(call=call, asked_by=actor, content=_proposal_line(call, actor))
    raise OverTier(_refusal_line(call))


_WHAT: Final[dict[Resource, str]] = {
    Resource.model: say("policyResourceModel"),
}


def _proposal_line(call: Call, actor: str) -> str:
    return say(
        "policyProposal",
        actor=actor,
        what=_WHAT[call.resource],
        label=call.label,
        tier=call.tier,
        approver=f"@{call.approver}",
    )


def _refusal_line(call: Call) -> str:
    return say(
        "policyOverTier", what=_WHAT[call.resource], label=call.label, tier=call.tier
    )
