"""Resource-pool market (design v3: AI and compute are symmetric pools).

A single catalog that backs both the 市场 (browse everything on offer) and a
project's settings (pick which pool this project runs on). Two kinds of pool:

  - ``ai``      — which model/provider a turn runs on (ExecutionProfile / AIPool)
  - ``compute`` — which machine runs the sandbox (ComputeProvider / ComputePool)

`available` is the honest flag: a listing that isn't deployed/credentialed can't
be selected, so a project never silently runs on something that isn't there.

The catalog carries only pools that EXIST. A permanently unavailable row teaches
the reader that connecting something would light it up — so `remote-cheesed` and
`gpu`, which had no provider and no resolution path, were removed rather than
shown greyed out, and `local-docker` went with the #358 retirement. `available`
is for a pool that is real but not reachable right now (no machine online, no
provisioning configured), not for one that does not exist.
"""

from dataclasses import dataclass

from app.domain.agent.profiles import ProfileRegistry

# Compute provider names (match ComputeProvider.name in compute.py).
COMPUTE_LOCAL = "local-docker"
COMPUTE_REMOTE = "remote-cheesed"
COMPUTE_DEVICE = "device"
COMPUTE_CLOUD = "cloud"
COMPUTE_GPU = "gpu"


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
    # Cloud is available when cheese can PROVISION it. Connector presence belongs
    # to an individual topic machine's later boot/enrolment state; using it here
    # would make a configured empty pool impossible to select.
    cloud_ready = bool(
        settings.microcloud_base_url and settings.microcloud_tenant_secret
    )
    # A device is real compute the moment a relevant machine is connected (DeviceHub
    # presence) — the honest `available` flag. Per-project when the caller knows the
    # context; else the global 'any device online'.
    if device_online is None:
        from app.domain.agent.device_hub import device_hub

        device_online = bool(device_hub.online_device_ids())
    device_ready = device_online
    # local-docker is GONE from the catalog (#358 "retire local"): not listed, not
    # selectable, not the fallback. It stays registered in the ComputePool — the
    # execution layer never consults this catalog — so a topic whose stored profile
    # still says `local-docker` keeps running there until its row is cleared. What
    # is removed is the CHOICE, and with it the last way for a new topic to land on
    # it. `remote-cheesed` and `gpu` are gone for a different reason: they never had
    # a provider at all.
    listings = [
        PoolListing(
            kind="compute",
            id=COMPUTE_DEVICE,
            label="自托管设备（我的机器）",
            tier="byo",
            price="自备",
            description="在你自己连接的机器上跑，工作树与数据留在本地；先到『我的设备』连接一台。",
            available=device_ready,
        ),
        PoolListing(
            kind="compute",
            id=COMPUTE_CLOUD,
            label="Cloud",
            tier="premium",
            price="按量计费",
            description="为这个话题创建一台独占云端机器；首次启动需要等待几分钟。",
            available=cloud_ready,
            # The fallback when nothing was selected — see `compute_default_name`.
            # Last selection still wins; this is only where a topic lands with no
            # topic choice, no project sticky and no team default.
            default=True,
        ),
    ]
    return listings


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


def compute_default_name() -> str:
    """What a topic runs on when nothing was chosen: last selection first (the
    topic's own, then the project's sticky memory, then the team default — see
    `_resolve_compute_id`), and Cloud when there is none.

    It used to be local-docker. That made the retired pool the destination of
    every unconfigured topic, which is the opposite of retiring it (#358).
    """
    return COMPUTE_CLOUD


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

    ``isolated`` is the conservative DEFAULT (``default=True``) but its per-room
    container transport is not built yet (#358 step 2), so it is honestly
    ``available=False`` — same convention as an undeployed compute pool. ``host``
    (whole machine) works today but is 申请制: ``default=False``, and its
    description IS the #282 safety line, so whoever renders the picker or the room
    badge reads the warning straight from the catalog."""
    return [
        PoolListing(
            kind="visibility",
            id=VISIBILITY_ISOLATED,
            label="沙盒（只看自己的工作树）",
            tier="included",
            price="包含",
            description="每个房间一个容器，只看得到自己的工作树，房间之间互不串扰；即将上线。",
            available=False,
            default=True,
        ),
        PoolListing(
            kind="visibility",
            id=VISIBILITY_HOST,
            label="整台机器（Hosted Machine）",
            tier="byo",
            price="自备",
            description=MACHINE_VISIBILITY_NOTICE,
            available=True,
            default=False,
        ),
    ]


# --- Subscription model (parallel to compute pool): which Claude model a
# project's subscription turns use. Only meaningful when the subscription path is
# deployed; a project picks it the same way it picks a compute pool. ------------
# (id, label, description, --model alias, is-default). The default carries no
# alias ("") so it uses the subscription's own default (Sonnet 5) with no --model
# — the proven path; Opus is the explicit opt-in.
_SUB_MODELS: list[tuple[str, str, str, str, bool]] = [
    (
        "sonnet",
        "Claude Sonnet 5",
        "均衡：足够聪明，最省订阅额度，适合绝大多数项目。",
        "",
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
    """The Claude `--model` alias for a selection id ('' = no flag → subscription
    default). An unknown id falls back to the default (no flag), never an error —
    a stale stored selection must not break a turn."""
    for m, _l, _d, alias, _dflt in _SUB_MODELS:
        if m == mid:
            return alias
    return ""


def subscription_model_ids() -> set[str]:
    return {mid for (mid, _l, _d, _a, _dflt) in _SUB_MODELS}
