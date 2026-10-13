"""Resource-pool market (design v3: AI and compute are symmetric pools).

A single catalog that backs both the 市场 (browse everything on offer) and a
project's settings (pick which pool this project runs on). Two kinds of pool:

  - ``ai``      — which model/provider a turn runs on (ExecutionProfile / AIPool)
  - ``compute`` — the sandbox specs an agent's execution environment may have
    (``agent.environment``)

`available` is the honest flag: a listing that isn't deployed/credentialed can't
be selected, so a project never silently runs on something that isn't there.

The catalog carries only pools that EXIST. `available` is for a pool that is
real but not reachable right now (no machine online, no provisioning
configured); a pool that does not exist is not listed greyed out, it is not
listed. Teaching a reader that connecting something would light a row up is
only honest when it would.
"""

from dataclasses import dataclass

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent.environment import DEVICES_OF, STANDARD, VM
from app.domain.agent.profiles import ProfileRegistry

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
    kind: str  # "ai" | "compute"
    id: str
    label: str
    tier: str
    price: str  # human-readable, e.g. "包含" / "按需报价"
    description: str
    available: bool
    default: bool = False


# Curated copy for each AI profile, keyed by profile name. Falls back to a
# generic line for profiles we don't have bespoke copy for.
_AI_COPY: dict[str, tuple[str, str]] = {
    "default": (
        "包含（平台补贴）",
        "平台托管的通用大模型，开箱即用，适合绝大多数项目。",
    ),
    "claude-opus": (
        "仅团队内测",
        "原生 Claude Opus，仅团队 dogfooding 使用，不对外开放。",
    ),
}
_TIER_PRICE = {"byo": "自带凭证", "testing": "仅团队内测"}


def ai_listings(
    registry: ProfileRegistry, owner_handle: str | None = None
) -> list[PoolListing]:
    """Every AI pool in the catalog. `available` reflects credentials + whether
    this owner may select it (a testing-tier profile is unavailable off-dogfood)."""
    selectable = {v.name for v in registry.selectable(owner_handle)}
    out: list[PoolListing] = []
    for name, p in registry.all():
        price, desc = _AI_COPY.get(name, (_TIER_PRICE.get(p.tier, "—"), f"{p.label}。"))
        out.append(
            PoolListing(
                kind="ai",
                id=name,
                label=p.label,
                tier=p.tier,
                price=price,
                description=desc,
                available=name in selectable,
                default=(name == "default"),
            )
        )
    return out


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


def compute_listings(
    settings,  # type: ignore[no-untyped-def]
    *,
    device_online: bool | None = None,
) -> list[PoolListing]:
    """The sandbox specs on offer on this deployment: the platform's machines,
    a whole VM of the platform's, and people's own computers — each person's
    are a spec of their own in a project (``environment.devices_of``), listed
    here once. ``device_online`` scopes the last to a project's machines; left
    as None (the global 市场) it asks whether any machine is connected."""
    if device_online is None:
        # deferred-import: tests replace this name on app.domain.agent.device_hub
        from app.domain.agent.device_hub import device_hub

        device_online = bool(device_hub.online_device_ids())
    return [
        PoolListing(
            kind="compute",
            id=STANDARD,
            label="标准沙箱",
            tier="included",
            price="按时长计费",
            description="每位 AI 队友一个沙箱，在平台的机器上，"
            "只看得到自己的工作目录。",
            available=cloud_provisionable(settings),
            default=True,
        ),
        PoolListing(
            kind="compute",
            id=VM,
            label="虚拟机沙箱",
            tier="included",
            price="按规格和时长计费",
            description="一整台虚拟机给一位 AI 队友：能跑 Docker、KVM、装内核模块。",
            available=cloud_vm_provisionable(settings),
        ),
        PoolListing(
            kind="compute",
            id=DEVICES_OF.rstrip(":"),
            label="自己的设备",
            tier="byo",
            price="不收费",
            description="沙箱放在项目成员自己接入的电脑上；先到『我的设备』连接一台。",
            available=device_online,
        ),
    ]


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
        "配额受限时会被自动降级到 Opus 4.8，且降级可能持续到会话结束"
        "（重开会话恢复）。",
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


def subscription_model_default() -> str:
    return next(mid for (mid, _l, _d, _a, dflt, _t) in _SUB_MODELS if dflt)


def subscription_model_alias(mid: str | None) -> str:
    """The explicit Claude model identifier for a saved selection; unknown ids fail."""
    for m, _l, _d, alias, _dflt, _t in _SUB_MODELS:
        if m == mid:
            return alias

    raise ValidationError(say("marketUnknownSubscriptionModel", model=repr(mid)))


def subscription_model_ids() -> set[str]:
    return {mid for (mid, _l, _d, _a, _dflt, _t) in _SUB_MODELS}
