"""What a run can be given, and what each costs.

The tiers models and machines share (the project's tier policy compares against
them), the Claude models a project may pick for its subscription turns, and
whether this deployment can provision the platform's sandboxes. A project
chooses its model and its environment in its settings; the sandbox specs it is
offered come from ``app.domain.machine.environments.offered_specs``.
"""

from dataclasses import dataclass

from app.core.errors import ValidationError
from app.core.sentences import say

# --- 档位 (结论 3: 模型是工作占用的资源，与机器（地点）同形) --------------------
# 一件工作占用的资源花谁的钱，模型和机器用同一套词——项目的档位策略
# (`domain/policy/gate.py`) 拿它比对，所以它在目录里声明一次，别处只读。
#: 订阅或平台池里包含的常规档，不额外花钱。
TIER_INCLUDED = "included"
#: 自带的东西：某个人自己的凭证或机器。
TIER_BYO = "byo"
#: 更贵的一档：更快吃掉订阅额度的模型。
TIER_PREMIUM = "premium"
#: 前沿档。
TIER_FRONTIER = "frontier"


@dataclass(frozen=True)
class PoolListing:
    kind: str  # "model"
    id: str
    label: str
    tier: str
    price: str  # human-readable, e.g. "包含" / "按需报价"
    description: str
    available: bool
    default: bool = False


def cloud_provisionable(
    settings,  # type: ignore[no-untyped-def]
) -> bool:
    """Can cheese place sessions in cloud sandboxes on this deployment?

    Whether a host of the pool is up right now is the pool's business; using it
    here would make a configured empty pool impossible to select."""
    return bool(settings.microcloud_base_url and settings.microcloud_tenant_secret)


def cloud_vm_provisionable(
    settings,  # type: ignore[no-untyped-def]
) -> bool:
    """Can a session on this deployment have a whole cloud VM of its own? Only
    where cloud is configured and an operator named the VM offering."""
    return cloud_provisionable(settings) and bool(settings.microcloud_vm_offering_id)


# Models available to agents using the subscription supply.
# (id, label, description, explicit --model identifier, creation default, tier).
#
# 最后一列是档位。订阅模型没有一个在 included 档：方案按档位限定团队能用的模型
# （#2397），默认方案只给 included，订阅模型因此一律从 premium 起，前沿的 Fable 是
# frontier。项目的档位策略拿的也是这一列。
_SUB_MODELS: list[tuple[str, str, str, str, bool, str]] = [
    (
        "sonnet",
        "Claude Sonnet 5",
        "均衡：足够聪明，最省订阅额度，适合绝大多数项目。",
        "claude-sonnet-5",
        True,
        TIER_PREMIUM,
    ),
    (
        "sonnet-5.5",
        "Claude Sonnet 5.5",
        "Sonnet 的新一代：同样均衡省额度，复杂任务表现更好。",
        "claude-sonnet-5-5",
        False,
        TIER_PREMIUM,
    ),
    # Full model ids from here down, not CLI aliases: Fable falls back to
    # Opus 4.8 specifically (safety classifiers on cyber/bio topics reroute
    # there, Anthropic-official, <5% of sessions — plus quota-style silent
    # downgrades reported on Max), so 4.8-vs-5 is a distinction users must be
    # able to SEE and pick; a bare "opus" alias hides which one you get.
    (
        "opus",  # id kept as-is: stored selections must not break
        "Claude Opus 5",
        "最强：复杂任务表现更好，但更快消耗订阅额度（Max 有上限）。",
        "claude-opus-5",
        False,
        TIER_PREMIUM,
    ),
    (
        "opus-5.5",
        "Claude Opus 5.5",
        "Opus 的新一代：复杂任务表现更好，同样更快消耗订阅额度（Max 有上限）。",
        "claude-opus-5-5",
        False,
        TIER_PREMIUM,
    ),
    (
        "opus-4.8",
        "Claude Opus 4.8",
        "上一代 Opus（仍在售，1M 上下文）：Fable 被降级时实际落到的模型；"
        "需要复现或对齐降级后行为时可显式选它。",
        "claude-opus-4-8",
        False,
        TIER_PREMIUM,
    ),
    (
        "fable",
        "Claude Fable 5",
        "前沿：新一代最强模型。注意：安全分类器命中（网络安全/生物类话题）或"
        "配额受限时会被自动降级到 Opus 4.8；降级后，AI 队友在同一个频道、支线或任务里"
        "可能一直用 Opus 4.8（新开的支线或任务会恢复）。",
        "claude-fable-5",
        False,
        TIER_FRONTIER,
    ),
    (
        "fable-5.1",
        "Claude Fable 5.1",
        "Fable 的新一代：长时间、多步骤的复杂任务表现更好，最快消耗订阅额度。",
        "claude-fable-5-1",
        False,
        TIER_FRONTIER,
    ),
]


def subscription_model_listings() -> list[PoolListing]:
    """The Claude models a project may pick for its subscription turns."""
    return [
        PoolListing(
            kind="model",
            id=mid,
            label=label,
            tier=tier,
            price="包含（订阅）",
            description=desc,
            available=True,
            default=default,
        )
        for (mid, label, desc, _alias, default, tier) in _SUB_MODELS
    ]


def subscription_model_alias(mid: str | None) -> str:
    """The explicit Claude model identifier for a saved selection; unknown ids fail."""
    for m, _l, _d, alias, _dflt, _t in _SUB_MODELS:
        if m == mid:
            return alias

    raise ValidationError(say("marketUnknownSubscriptionModel", model=repr(mid)))


def subscription_model_ids() -> set[str]:
    return {mid for (mid, _l, _d, _a, _dflt, _t) in _SUB_MODELS}
