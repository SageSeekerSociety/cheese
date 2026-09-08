"""Resource-pool market (design v3: AI and compute are symmetric pools).

A single catalog that backs both the 市场 (browse everything on offer) and a
project's settings (pick which pool this project runs on). Two kinds of pool:

  - ``ai``      — which model/provider a turn runs on (ExecutionProfile / AIPool)
  - ``compute`` — which machine runs the sandbox (ComputeProvider / ComputePool)

`available` is the honest flag: a listing that isn't deployed/credentialed can't
be selected, so a project never silently runs on something that isn't there.

The catalog carries only pools that EXIST. `available` is for a pool that is
real but not reachable right now (no machine online, no provisioning
configured); a pool that does not exist is not listed greyed out, it is not
listed. Teaching a reader that connecting something would light a row up is
only honest when it would.
"""

from dataclasses import dataclass

from app.domain.agent.profiles import ProfileRegistry
from app.domain.device.supply import (
    Visibility,
    default_visibility,
    has_runnable_transport,
)

# Compute provider names (match ComputeProvider.name in compute.py).
COMPUTE_DEVICE = "device"
COMPUTE_CLOUD = "cloud"


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
    """Can cheese PROVISION a Cloud machine on this deployment?

    Connector presence belongs to an individual topic machine's later
    boot/enrolment state; using it here would make a configured empty pool
    impossible to select."""
    return bool(settings.microcloud_base_url and settings.microcloud_tenant_secret)


def compute_listings(
    settings,  # type: ignore[no-untyped-def]
    *,
    device_online: bool | None = None,
) -> list[PoolListing]:
    """Every compute pool in the catalog — self-hosted and Cloud. The device pool
    is on when a relevant machine is connected; Cloud is on when provisioning is
    configured.

    ``device_online`` scopes the device pool's availability to a CONTEXT: a route
    that knows the project passes whether THAT project has an online enrolled
    machine (compute belongs to the project/team, not globally). Left as ``None``
    (the global 市场 catalog) it falls back to 'is any device connected at all'."""
    cloud_ready = cloud_provisionable(settings)
    fallback = compute_default_name(settings)
    # A device is real compute the moment a relevant machine is connected (DeviceHub
    # presence) — the honest `available` flag. Per-project when the caller knows the
    # context; else the global 'any device online'.
    if device_online is None:
        from app.domain.agent.device_hub import device_hub

        device_online = bool(device_hub.online_device_ids())
    device_ready = device_online
    # Whose machine runs the work is the whole question, so both rows name an
    # owner. The platform's own box was a third row that never appeared here and
    # ran every unconfigured topic anyway (#358 "retire local"); it is gone from
    # the ComputePool too, which is what makes `default` below a fact rather
    # than a claim.
    return [
        PoolListing(
            kind="compute",
            id=COMPUTE_DEVICE,
            label="自托管设备（我的机器）",
            tier="byo",
            price="自备",
            description="在你自己连接的机器上跑，工作树与数据留在本地；先到『我的设备』连接一台。",
            available=device_ready,
            default=fallback == COMPUTE_DEVICE,
        ),
        PoolListing(
            kind="compute",
            id=COMPUTE_CLOUD,
            label="Cloud",
            tier="premium",
            price="按量计费",
            description="为这个话题创建一台独占云端机器；首次启动需要等待几分钟。",
            available=cloud_ready,
            default=fallback == COMPUTE_CLOUD,
        ),
    ]


def compute_selectable(
    settings,  # type: ignore[no-untyped-def]
    *,
    device_online: bool | None = None,
) -> list[PoolListing]:
    """The compute pools a project can actually pick right now (deployed). Pass
    ``device_online`` to scope the self-hosted pool to a project's own machines."""
    return [
        p
        for p in compute_listings(settings, device_online=device_online)
        if p.available
    ]


def compute_default_name(
    settings=None,  # type: ignore[no-untyped-def]
) -> str:
    """What a topic runs on when nothing was chosen: last selection first (the
    topic's own, then the project's sticky memory, then the team default — see
    `_resolve_compute_id`), and this pool when there is none.

    Cloud where the deployment can provision one, the self-hosted device pool
    where it cannot — the machine the deployment actually has, named honestly
    rather than aspirationally.

    This is also what `build_compute_pool` hands an unconfigured turn to, which
    is the point of putting it in ONE function. The catalogue used to declare
    Cloud the default while the execution layer's own default was the platform's
    own box, a pool the catalogue did not list at all: a person read 默认 next to
    Cloud and their turn ran in a container on our host. Two answers to one
    question can only ever disagree, so there is one.
    """
    if settings is None:
        from app.core.config import settings as deployment_settings

        settings = deployment_settings
    return COMPUTE_CLOUD if cloud_provisionable(settings) else COMPUTE_DEVICE


# --- Visibility (#282 §四 / #358): the whole-machine question -------------------
# Visibility is NOT a pool of its own — it is a sub-choice UNDER the self-hosted
# device pool: when a room runs on an enrolled machine, does its agent see only its
# own worktree (boxed) or the whole host (operate its services, exec into other
# rooms, reach the internal network)? The platform exposes this 档's capability
# description so the room can SHOW it, rather than granting whole-machine access
# silently (#358 原则八: 平台只说能力，不静默行为).
VISIBILITY_ISOLATED = "isolated"
VISIBILITY_HOST = "host"

# The exact honest UI line #282 §四 drafted — the "看得见的安全提示" a Hosted
# Machine room renders as its badge text / tooltip. Kept here as the single source
# so backend gate copy and the frontend badge cannot drift.
MACHINE_VISIBILITY_NOTICE = "让它看到整台机器（能操作这台机器上的服务和其他房间）"


def visibility_listings() -> list[PoolListing]:
    """The visibility 档 a room may run its self-hosted compute under (#282 §四).

    ``available`` says whether a 档 has a transport; ``default`` says which one a
    topic gets when nobody picks. Both come from `device.supply`, so this catalogue
    cannot tell someone their topic is boxed while the resolver binds it to the
    whole machine — which is exactly what it used to do, `isolated` being declared
    the default here while `resolve_pinned_device` wrote `host` unconditionally.

    ``host``'s description IS the #282 safety line, so whoever renders the picker
    or the room badge reads the warning straight from the catalogue."""
    default = default_visibility()
    return [
        PoolListing(
            kind="visibility",
            id=VISIBILITY_ISOLATED,
            label="沙盒（只看自己的工作树）",
            tier="included",
            price="包含",
            description="每个房间一个容器，只看得到自己的工作树，房间之间互不串扰；即将上线。",
            available=has_runnable_transport(Visibility.isolated),
            default=default is Visibility.isolated,
        ),
        PoolListing(
            kind="visibility",
            id=VISIBILITY_HOST,
            label="整台机器（Hosted Machine）",
            tier="byo",
            price="自备",
            description=MACHINE_VISIBILITY_NOTICE,
            available=has_runnable_transport(Visibility.host),
            default=default is Visibility.host,
        ),
    ]


# Models available to agents using the subscription supply.
# (id, label, description, explicit --model identifier, creation default).
_SUB_MODELS: list[tuple[str, str, str, str, bool]] = [
    (
        "sonnet",
        "Claude Sonnet 5",
        "均衡：足够聪明，最省订阅额度，适合绝大多数项目。",
        "claude-sonnet-5",
        True,
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
    ),
    (
        "opus-4.8",
        "Claude Opus 4.8",
        "上一代 Opus（仍在售，1M 上下文）：Fable 被降级时实际落到的模型；"
        "需要复现或对齐降级后行为时可显式选它。",
        "claude-opus-4-8",
        False,
    ),
    (
        "fable",
        "Claude Fable 5",
        "前沿：新一代最强模型。注意：安全分类器命中（网络安全/生物类话题）或"
        "配额受限时会被自动降级到 Opus 4.8，且降级可能持续到会话结束"
        "（重开会话恢复）。",
        "claude-fable-5",
        False,
    ),
]


def subscription_model_listings() -> list[PoolListing]:
    """The Claude models a project may pick for its subscription turns."""
    return [
        PoolListing(
            kind="model",
            id=mid,
            label=label,
            tier="subscription",
            price="包含（订阅）",
            description=desc,
            available=True,
            default=default,
        )
        for (mid, label, desc, _alias, default) in _SUB_MODELS
    ]


def subscription_model_default() -> str:
    return next(mid for (mid, _l, _d, _a, dflt) in _SUB_MODELS if dflt)


def subscription_model_alias(mid: str | None) -> str:
    """The explicit Claude model identifier for a saved selection; unknown ids fail."""
    for m, _l, _d, alias, _dflt in _SUB_MODELS:
        if m == mid:
            return alias
    from app.core.errors import ValidationError

    raise ValidationError(f"未知订阅模型 {mid!r}")


def subscription_model_ids() -> set[str]:
    return {mid for (mid, _l, _d, _a, _dflt) in _SUB_MODELS}
